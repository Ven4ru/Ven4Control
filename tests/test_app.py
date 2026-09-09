import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QMessageBox

from ven4control.app import (
    BULK_MESSAGE_LIMIT,
    EMPTY_LIST_HINT,
    RDP_DISABLED,
    RDP_ENABLED,
    RDP_UNKNOWN,
    RDP_UNSUPPORTED,
    BulkResult,
    MainWindow,
    apply_rdp_result,
    bulk_report,
    empty_hint_visible,
    needs_key_install,
    online_summary,
    rdp_cell_text,
    rdp_check_label,
    rdp_state,
    section_header_text,
    tailscale_candidate_line,
    tailscale_candidates,
    tailscale_import_report,
    terminal_command,
)
from ven4control.dialogs import USERNAME_PLACEHOLDER
from ven4control.models import Device
from ven4control.rdp_tunnel import (
    STATUS_ACTIVE,
    STATUS_FAILED,
    STATUS_STARTING,
    RdpTunnel,
)
from ven4control.remote_control import MISSING_FINGERPRINT_MESSAGE
from ven4control.sftp_session import SftpSession
from ven4control.storage import DeviceStorage
from ven4control.terminal_session import TerminalSession


def application() -> QCoreApplication:
    """Сигналы Worker существуют только внутри приложения Qt."""
    existing = QCoreApplication.instance()
    return existing if existing is not None else QCoreApplication([])


class FakeMessageBox:
    """Подмена QMessageBox: диалоги записываются, а не показываются.

    Перечисление кнопок настоящее — проверяемый код сравнивает ответ
    именно с ним.
    """

    StandardButton = QMessageBox.StandardButton

    def __init__(self, answer=QMessageBox.StandardButton.Yes) -> None:
        self.answer = answer
        self.shown: list[tuple[str, str, str]] = []

    def question(self, _parent, title, text, *_args, **_kwargs):
        self.shown.append(("question", title, text))
        return self.answer

    def information(self, _parent, title, text, *_args, **_kwargs) -> None:
        self.shown.append(("information", title, text))

    def warning(self, _parent, title, text, *_args, **_kwargs) -> None:
        self.shown.append(("warning", title, text))

    def critical(self, _parent, title, text, *_args, **_kwargs) -> None:
        self.shown.append(("critical", title, text))

    def kinds(self) -> list[str]:
        return [kind for kind, _title, _text in self.shown]

    def texts(self) -> str:
        return "\n".join(text for _kind, _title, text in self.shown)


def run_worker(worker) -> None:
    """Выполняет задачу немедленно: в тесте нет ни пула, ни цикла Qt."""
    try:
        result = worker.function(*worker.args)
    except Exception as error:  # как в Worker.run
        worker.signals.failed.emit(str(error))
    else:
        worker.signals.finished.emit(result)


class AddDeviceFingerprintTests(unittest.TestCase):
    """Fingerprint сохраняется при подтверждении, а не при установке ключа.

    Без записи в базе устройство видно в списке, но ни одна кнопка не
    работает: `_connect`, терминал, SFTP и RDP требуют fingerprint.
    Windows-устройства автоустановку ключа не поддерживают в принципе,
    поэтому привязка к её успеху делала их вечно неуправляемыми.
    """

    def setUp(self) -> None:
        application()
        self._directory = TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.storage = DeviceStorage(Path(self._directory.name) / "devices.db")
        self.box = FakeMessageBox()
        self.reloads = 0

    def _window(self) -> MainWindow:
        window = MainWindow.__new__(MainWindow)
        window.storage = self.storage
        window.private_key = Path("C:/Ven4Control/ssh/id_ed25519")
        window.public_key = Path("C:/Ven4Control/ssh/id_ed25519.pub")
        window.workers = set()
        window._start_worker = run_worker
        window.reload = self._count_reload
        return window

    def _count_reload(self) -> None:
        self.reloads += 1

    def _device(self, **fields) -> Device:
        return self.storage.save(
            Device(None, "Домашний ПК", "100.64.0.7", 22, "user", **fields)
        )

    def _saved(self) -> Device:
        return self.storage.list_devices()[0]

    def _probe(self, fingerprint: str = "SHA256:probe"):
        async def probe(_device) -> str:
            return fingerprint

        return probe

    def test_failed_key_install_still_leaves_a_manageable_device(self) -> None:
        """Windows: установка ключа падает всегда, устройство обязано работать."""
        device = self._device()

        async def install(*_args, **_kwargs) -> str:
            raise RuntimeError("Автоматическая установка поддерживает Linux и OpenWrt")

        window = self._window()
        with (
            patch("ven4control.app.probe_device", self._probe()),
            patch("ven4control.app.install_public_key", install),
            patch("ven4control.app.QMessageBox", self.box),
        ):
            window._install_key(device, "секрет")

        self.assertEqual("SHA256:probe", self._saved().fingerprint)
        # Вход остаётся по паролю: ключа на устройстве нет.
        self.assertEqual("password", self._saved().auth_type)
        # Неудача автоустановки — не ошибка: устройство управляемо.
        self.assertIn("information", self.box.kinds())
        self.assertNotIn("warning", self.box.kinds())
        self.assertIn("Установка ключа", self.box.texts())

    def test_successful_key_install_switches_the_login(self) -> None:
        device = self._device()

        async def install(*_args, **_kwargs) -> str:
            return "linux"

        window = self._window()
        with (
            patch("ven4control.app.probe_device", self._probe()),
            patch("ven4control.app.install_public_key", install),
            patch("ven4control.app.QMessageBox", self.box),
        ):
            window._install_key(device, "секрет")

        saved = self._saved()
        self.assertEqual("SHA256:probe", saved.fingerprint)
        self.assertEqual("key", saved.auth_type)
        self.assertEqual("C:\\Ven4Control\\ssh\\id_ed25519", saved.key_path.replace("/", "\\"))

    def test_device_with_a_ready_key_gets_a_fingerprint_without_installing(self) -> None:
        """Ключ уже стоит на устройстве: ставить нечего, отпечаток нужен."""
        device = self._device(auth_type="key", key_path="C:/keys/id_ed25519")

        async def install(*_args, **_kwargs) -> str:
            raise AssertionError("Установка ключа на этом пути недопустима")

        window = self._window()
        with (
            patch("ven4control.app.probe_device", self._probe("SHA256:ready")),
            patch("ven4control.app.install_public_key", install),
            patch("ven4control.app.QMessageBox", self.box),
        ):
            window._capture_fingerprint_only(device)

        self.assertEqual("SHA256:ready", self._saved().fingerprint)
        self.assertEqual("key", self._saved().auth_type)

    def test_declined_fingerprint_is_not_saved(self) -> None:
        device = self._device()
        self.box.answer = QMessageBox.StandardButton.No
        window = self._window()
        with (
            patch("ven4control.app.probe_device", self._probe()),
            patch("ven4control.app.QMessageBox", self.box),
        ):
            window._capture_fingerprint_only(device)
        self.assertEqual("", self._saved().fingerprint)

    def test_unreachable_device_reports_the_reason(self) -> None:
        device = self._device()

        async def probe(_device) -> str:
            raise RuntimeError("Устройство не сообщило SSH fingerprint")

        window = self._window()
        with (
            patch("ven4control.app.probe_device", probe),
            patch("ven4control.app.QMessageBox", self.box),
        ):
            window._capture_fingerprint_only(device)
        self.assertEqual("", self._saved().fingerprint)
        self.assertIn("не сообщило SSH fingerprint", self.box.texts())
        self.assertGreaterEqual(self.reloads, 1)


class KeyInstallDecisionTests(unittest.TestCase):
    def test_password_device_with_the_checkbox_installs_the_key(self) -> None:
        device = Device(None, "Роутер", "192.168.1.1", 22, "root")
        self.assertTrue(needs_key_install(device, True, "секрет"))

    def test_ready_key_is_never_reinstalled(self) -> None:
        device = Device(None, "ПК", "100.64.0.7", 22, "user", auth_type="key")
        self.assertFalse(needs_key_install(device, True, "секрет"))

    def test_cleared_checkbox_is_respected(self) -> None:
        device = Device(None, "Роутер", "192.168.1.1", 22, "root")
        self.assertFalse(needs_key_install(device, False, "секрет"))

    def test_empty_password_cannot_install_anything(self) -> None:
        device = Device(None, "Роутер", "192.168.1.1", 22, "root")
        self.assertFalse(needs_key_install(device, True, ""))


class UsernameFieldTests(unittest.TestCase):
    """Поле «Пользователь» пустое: `root` был реальным значением, не подсказкой."""

    def test_placeholder_names_both_cases(self) -> None:
        self.assertIn("root", USERNAME_PLACEHOLDER)
        self.assertIn("Ubuntu", USERNAME_PLACEHOLDER)
        self.assertNotEqual("root", USERNAME_PLACEHOLDER)


class MissingFingerprintMessageTests(unittest.TestCase):
    """Совет должен вести к действию, которое в приложении есть."""

    def test_message_offers_a_real_action(self) -> None:
        self.assertIn("Удалите устройство", MISSING_FINGERPRINT_MESSAGE)
        self.assertNotIn("Переустановите ключ", MISSING_FINGERPRINT_MESSAGE)

    def test_main_window_uses_the_shared_text(self) -> None:
        box = FakeMessageBox()
        window = MainWindow.__new__(MainWindow)
        device = Device(1, "ПК", "100.64.0.7", 22, "user")
        with patch("ven4control.app.QMessageBox", box):
            self.assertFalse(
                MainWindow._require_fingerprint(window, device, "Терминал недоступен")
            )
        self.assertIn(MISSING_FINGERPRINT_MESSAGE, box.texts())

    def test_file_browser_uses_the_shared_text(self) -> None:
        application()
        session = SftpSession(Device(1, "ПК", "100.64.0.7", 22, "user"), {})
        with self.assertRaises(ValueError) as error:
            session.start()
        self.assertIn(MISSING_FINGERPRINT_MESSAGE, str(error.exception))

    def test_terminal_uses_the_shared_text(self) -> None:
        application()
        session = TerminalSession(Device(1, "ПК", "100.64.0.7", 22, "user"), {})
        with self.assertRaises(ValueError) as error:
            session.start()
        self.assertIn(MISSING_FINGERPRINT_MESSAGE, str(error.exception))


class TerminalCommandTests(unittest.TestCase):
    def test_password_device_has_no_key_argument(self) -> None:
        device = Device(1, "Сервер", "100.64.0.1", 2222, "root")
        self.assertEqual(
            ["ssh", "-p", "2222", "root@100.64.0.1"], terminal_command(device)
        )

    def test_key_device_uses_stored_key_path(self) -> None:
        device = Device(
            1, "ПК", "203.0.113.10", 22, "user",
            auth_type="key", key_path=r"C:\Ven4Control\ssh\id_ed25519",
        )
        self.assertEqual(
            [
                "ssh", "-p", "22",
                "-i", r"C:\Ven4Control\ssh\id_ed25519",
                "user@203.0.113.10",
            ],
            terminal_command(device),
        )

    def test_key_device_without_path_falls_back_to_plain_ssh(self) -> None:
        device = Device(1, "ПК", "host", 22, "root", auth_type="key")
        self.assertEqual(["ssh", "-p", "22", "root@host"], terminal_command(device))


class RdpPanelStateTests(unittest.TestCase):
    def _device(self) -> Device:
        return Device(1, "ПК", "100.64.0.7", 22, "user")

    def test_unchecked_device_has_no_rdp_buttons(self) -> None:
        self.assertEqual(RDP_UNKNOWN, rdp_state(None))
        self.assertEqual(RDP_UNKNOWN, rdp_state(self._device()))

    def test_router_is_unsupported_not_disabled(self) -> None:
        """У OpenWrt RDP отсутствует: кнопка включения там гарантированно врёт."""
        self.assertEqual(RDP_UNSUPPORTED, rdp_state(self._device(), "openwrt"))
        self.assertEqual(RDP_UNSUPPORTED, rdp_state(self._device(), "linux"))

    def test_windows_without_rdp_offers_enabling(self) -> None:
        self.assertEqual(RDP_DISABLED, rdp_state(self._device(), "windows"))

    def test_windows_with_rdp_offers_the_session(self) -> None:
        device = self._device()
        apply_rdp_result(device, True)
        self.assertEqual(RDP_ENABLED, rdp_state(device, "windows"))

    def test_saved_result_survives_a_restart_without_a_known_platform(self) -> None:
        device = self._device()
        apply_rdp_result(device, True)
        self.assertEqual(RDP_ENABLED, rdp_state(device))

    def test_fresh_platform_answer_overrides_the_saved_flag(self) -> None:
        """Устройство могли заменить: свежий ответ важнее записи в базе."""
        device = self._device()
        apply_rdp_result(device, True)
        self.assertEqual(RDP_UNSUPPORTED, rdp_state(device, "openwrt"))


class RdpCellTests(unittest.TestCase):
    def _tunnel(self, status: str, port: int = 0) -> RdpTunnel:
        return RdpTunnel(1, "ПК", 3389, local_port=port, status=status)

    def test_device_without_a_tunnel_is_empty(self) -> None:
        self.assertEqual("—", rdp_cell_text(None))

    def test_open_tunnel_shows_the_local_end(self) -> None:
        self.assertEqual(
            "туннель 127.0.0.1:54321",
            rdp_cell_text(self._tunnel(STATUS_ACTIVE, 54321)),
        )

    def test_other_states_are_named(self) -> None:
        self.assertEqual("подключение…", rdp_cell_text(self._tunnel(STATUS_STARTING)))
        self.assertEqual("ошибка", rdp_cell_text(self._tunnel(STATUS_FAILED)))

    def test_active_tunnel_without_a_port_is_not_shown_as_ready(self) -> None:
        self.assertEqual("открыт", rdp_cell_text(self._tunnel(STATUS_ACTIVE)))


class RdpStateTests(unittest.TestCase):
    def _device(self) -> Device:
        return Device(1, "ПК", "100.64.0.7", 22, "user")

    def test_unchecked_device_becomes_available(self) -> None:
        device = self._device()
        self.assertTrue(apply_rdp_result(device, True))
        self.assertTrue(device.rdp_checked)
        self.assertTrue(device.rdp_available)

    def test_unchecked_device_becomes_unavailable(self) -> None:
        device = self._device()
        self.assertTrue(apply_rdp_result(device, False))
        self.assertTrue(device.rdp_checked)
        self.assertFalse(device.rdp_available)

    def test_repeated_result_does_not_require_saving(self) -> None:
        device = self._device()
        apply_rdp_result(device, True)
        self.assertFalse(apply_rdp_result(device, True))
        self.assertTrue(device.rdp_available)

    def test_available_device_can_become_unavailable(self) -> None:
        device = self._device()
        apply_rdp_result(device, True)
        self.assertTrue(apply_rdp_result(device, False))
        self.assertFalse(device.rdp_available)

    def test_unavailable_device_can_become_available(self) -> None:
        device = self._device()
        apply_rdp_result(device, False)
        self.assertTrue(apply_rdp_result(device, True))
        self.assertTrue(device.rdp_available)

    def test_check_label_reflects_three_states(self) -> None:
        device = self._device()
        self.assertEqual("Проверить RDP", rdp_check_label(None))
        self.assertEqual("Проверить RDP", rdp_check_label(device))
        apply_rdp_result(device, False)
        self.assertEqual("RDP выключен — проверить снова", rdp_check_label(device))
        apply_rdp_result(device, True)
        self.assertEqual("Проверить RDP", rdp_check_label(device))


class TailscaleImportTests(unittest.TestCase):
    def _status(self) -> dict:
        return {
            "Peer": {
                "a": {"HostName": "router", "TailscaleIPs": ["100.64.0.1", "fd7a::1"]},
                "b": {"DNSName": "pc.tail.ts.net.", "TailscaleIPs": ["100.64.0.2"]},
                "c": {"HostName": "нет-адреса", "TailscaleIPs": []},
                "d": {"HostName": "ipv6", "TailscaleIPs": ["fd7a:115c::2"]},
            }
        }

    def test_only_new_ipv4_peers_are_offered(self) -> None:
        candidates = tailscale_candidates(self._status(), {("100.64.0.1", 22)})
        self.assertEqual(["pc.tail.ts.net"], [item.name for item in candidates])
        self.assertEqual(["100.64.0.2"], [item.host for item in candidates])
        self.assertEqual(22, candidates[0].port)
        self.assertEqual("root", candidates[0].username)
        self.assertIsNone(candidates[0].id)

    def test_username_applies_to_every_candidate(self) -> None:
        """`root` неверен для типовой Ubuntu и для Windows-машин в тейлнете."""
        candidates = tailscale_candidates(self._status(), set(), username="vench")
        self.assertTrue(candidates)
        self.assertEqual({"vench"}, {item.username for item in candidates})

    def test_candidate_line_names_the_fingerprint_state(self) -> None:
        answered = Device(None, "ПК", "100.64.0.2", 22, "vench", fingerprint="SHA256:x")
        silent = Device(None, "Роутер", "100.64.0.1", 22, "vench")
        self.assertIn("отпечаток получен", tailscale_candidate_line(answered))
        self.assertIn("100.64.0.2", tailscale_candidate_line(answered))
        self.assertIn("не ответило", tailscale_candidate_line(silent))

    def test_report_names_the_applied_username(self) -> None:
        saved = [
            Device(None, "ПК", "100.64.0.2", 22, "vench", fingerprint="SHA256:x"),
            Device(None, "Роутер", "100.64.0.1", 22, "vench"),
        ]
        report = tailscale_import_report("vench", saved, [])
        self.assertIn("vench", report)
        self.assertIn("Добавлено устройств: 2", report)
        self.assertIn("Без подтверждённого fingerprint: 1", report)

    def test_report_without_silent_devices_says_nothing_about_them(self) -> None:
        saved = [Device(None, "ПК", "100.64.0.2", 22, "vench", fingerprint="SHA256:x")]
        report = tailscale_import_report("vench", saved, [])
        self.assertNotIn("Без подтверждённого fingerprint", report)

    def test_report_lists_devices_that_were_not_saved(self) -> None:
        report = tailscale_import_report("vench", [], ["ПК — UNIQUE constraint"])
        self.assertIn("UNIQUE constraint", report)


class TailscaleImportFlowTests(unittest.TestCase):
    """Импорт должен создавать управляемые устройства, а не строки в списке."""

    def setUp(self) -> None:
        application()
        self._directory = TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.storage = DeviceStorage(Path(self._directory.name) / "devices.db")
        self.box = FakeMessageBox()
        self.reloads = 0

    def _window(self) -> MainWindow:
        window = MainWindow.__new__(MainWindow)
        window.storage = self.storage
        window.reload = self._count_reload
        return window

    def _count_reload(self) -> None:
        self.reloads += 1

    def _status_json(self) -> str:
        return (
            '{"Peer": {"a": {"HostName": "router", "TailscaleIPs": ["100.64.0.1"]}, '
            '"b": {"HostName": "pc", "TailscaleIPs": ["100.64.0.2"]}}}'
        )

    def _run_import(self, username: str = "vench", accepted: bool = True):
        completed = SimpleNamespace(stdout=self._status_json())

        async def probe(device: Device) -> str:
            if device.host == "100.64.0.1":
                raise OSError("хост недоступен")
            return "SHA256:pc"

        window = self._window()
        with (
            patch("ven4control.app.subprocess.run", return_value=completed),
            patch("ven4control.ssh_service.probe_device", probe),
            patch(
                "ven4control.app.QInputDialog.getText",
                return_value=(username, accepted),
            ),
            patch("ven4control.app.QMessageBox", self.box),
        ):
            window.import_tailscale()
        return window

    def test_answered_peer_is_saved_with_a_fingerprint(self) -> None:
        self._run_import()
        saved = {device.host: device for device in self.storage.list_devices()}
        self.assertEqual("SHA256:pc", saved["100.64.0.2"].fingerprint)
        self.assertEqual("vench", saved["100.64.0.2"].username)

    def test_silent_peer_is_still_added_without_a_fingerprint(self) -> None:
        self._run_import()
        saved = {device.host: device for device in self.storage.list_devices()}
        self.assertIn("100.64.0.1", saved)
        self.assertEqual("", saved["100.64.0.1"].fingerprint)

    def test_username_from_the_dialog_replaces_the_hardcoded_root(self) -> None:
        self._run_import(username="ubuntu")
        self.assertEqual(
            {"ubuntu"}, {device.username for device in self.storage.list_devices()}
        )
        self.assertIn("ubuntu", self.box.texts())

    def test_cancelled_username_dialog_imports_nothing(self) -> None:
        self._run_import(accepted=False)
        self.assertEqual([], self.storage.list_devices())

    def test_empty_username_is_refused(self) -> None:
        self._run_import(username="   ")
        self.assertEqual([], self.storage.list_devices())
        self.assertIn("Пользователь", self.box.texts())

    def test_confirmation_lists_the_fingerprint_state(self) -> None:
        self._run_import()
        question = next(text for kind, _t, text in self.box.shown if kind == "question")
        self.assertIn("отпечаток получен", question)
        self.assertIn("не ответило", question)

    def test_declined_confirmation_saves_nothing(self) -> None:
        self.box.answer = QMessageBox.StandardButton.No
        self._run_import()
        self.assertEqual([], self.storage.list_devices())

    def test_duplicates_inside_one_answer_are_collapsed(self) -> None:
        status = {
            "Peer": {
                "a": {"HostName": "первый", "TailscaleIPs": ["100.64.0.5"]},
                "b": {"HostName": "второй", "TailscaleIPs": ["100.64.0.5"]},
            }
        }
        self.assertEqual(1, len(tailscale_candidates(status, set())))

    def test_answer_without_peers_is_handled(self) -> None:
        self.assertEqual([], tailscale_candidates({}, set()))
        self.assertEqual([], tailscale_candidates({"Peer": None}, set()))
        self.assertEqual([], tailscale_candidates({"Peer": {"a": "мусор"}}, set()))


class BulkReportTests(unittest.TestCase):
    def test_all_successful_devices_are_listed(self) -> None:
        report = bulk_report(
            [
                BulkResult("Роутер", True, "Команда перезагрузки отправлена."),
                BulkResult("Домашний ПК", True, "Обновление пакетов завершено."),
            ]
        )
        lines = report.splitlines()
        self.assertEqual("Операция выполнена на всех устройствах: 2.", lines[0])
        self.assertIn("✔ Роутер — Команда перезагрузки отправлена.", lines)
        self.assertIn("✔ Домашний ПК — Обновление пакетов завершено.", lines)

    def test_failed_device_does_not_hide_the_successful_ones(self) -> None:
        """Отчёт нужен по каждому устройству: ошибка одного не отменяет остальные."""
        report = bulk_report(
            [
                BulkResult("Роутер", True, "Команда перезагрузки отправлена."),
                BulkResult("Сервер", False, "Соединение не установлено"),
                BulkResult("Домашний ПК", True, "Готово."),
            ]
        )
        lines = report.splitlines()
        self.assertEqual("Выполнено: 2 из 3, с ошибкой: 1.", lines[0])
        self.assertIn("✖ Сервер — Соединение не установлено", lines)
        self.assertIn("✔ Роутер — Команда перезагрузки отправлена.", lines)
        self.assertIn("✔ Домашний ПК — Готово.", lines)

    def test_multiline_output_stays_on_one_line(self) -> None:
        report = bulk_report([BulkResult("Роутер", True, "первая\nвторая   строка\n")])
        self.assertIn("✔ Роутер — первая вторая строка", report.splitlines())

    def test_long_output_is_trimmed(self) -> None:
        """Вывод apt-get не должен вытеснять из окна остальные устройства."""
        report = bulk_report([BulkResult("Сервер", True, "п" * 5000)])
        line = report.splitlines()[-1]
        self.assertTrue(line.endswith("…"))
        self.assertLess(len(line), BULK_MESSAGE_LIMIT + 60)

    def test_empty_answer_is_named(self) -> None:
        self.assertIn("✔ Роутер — без ответа", bulk_report([BulkResult("Роутер", True, "")]))

    def test_report_without_devices(self) -> None:
        self.assertEqual("Ни одно устройство не было затронуто.", bulk_report([]))


class OnlineSummaryTests(unittest.TestCase):
    def test_no_devices_at_all(self) -> None:
        self.assertEqual("Устройств нет", online_summary(0, 0))

    def test_all_online(self) -> None:
        self.assertEqual("Онлайн: 3 из 3", online_summary(3, 3))

    def test_some_offline(self) -> None:
        self.assertEqual("Онлайн: 1 из 3", online_summary(1, 3))

    def test_none_online_yet(self) -> None:
        # Проверки ещё не пришли (или все офлайн) — 0 из total, не «нет устройств».
        self.assertEqual("Онлайн: 0 из 3", online_summary(0, 3))


class EmptyHintTests(unittest.TestCase):
    """Подсказка про кнопку «Добавить» видна ровно при пустом списке."""

    def test_hint_is_visible_without_devices(self) -> None:
        self.assertTrue(empty_hint_visible(0))

    def test_hint_is_hidden_with_devices(self) -> None:
        self.assertFalse(empty_hint_visible(1))
        self.assertFalse(empty_hint_visible(7))

    def test_hint_text_names_the_button(self) -> None:
        self.assertIn("Добавить", EMPTY_LIST_HINT)

    def test_window_shows_the_label_when_the_list_is_empty(self) -> None:
        window = MainWindow.__new__(MainWindow)
        window.empty_hint = SimpleNamespace(
            visible=None, setVisible=lambda value: setattr(window.empty_hint, "visible", value)
        )
        window.devices = []
        MainWindow._update_empty_hint(window)
        self.assertTrue(window.empty_hint.visible)

    def test_window_hides_the_label_once_a_device_appears(self) -> None:
        window = MainWindow.__new__(MainWindow)
        window.empty_hint = SimpleNamespace(
            visible=None, setVisible=lambda value: setattr(window.empty_hint, "visible", value)
        )
        window.devices = [Device(1, "Роутер", "192.168.1.1", 22, "root")]
        MainWindow._update_empty_hint(window)
        self.assertFalse(window.empty_hint.visible)


class SectionHeaderTextTests(unittest.TestCase):
    def test_expanded_shows_down_arrow(self) -> None:
        self.assertEqual("▾ УСТРОЙСТВА", section_header_text("УСТРОЙСТВА", True))

    def test_collapsed_shows_right_arrow(self) -> None:
        self.assertEqual("▸ УСТРОЙСТВА", section_header_text("УСТРОЙСТВА", False))


if __name__ == "__main__":
    unittest.main()
