import unittest

from ven4control.app import (
    BULK_MESSAGE_LIMIT,
    RDP_DISABLED,
    RDP_ENABLED,
    RDP_UNKNOWN,
    RDP_UNSUPPORTED,
    BulkResult,
    apply_rdp_result,
    bulk_report,
    online_summary,
    rdp_cell_text,
    rdp_check_label,
    rdp_state,
    tailscale_candidates,
    terminal_command,
)
from ven4control.models import Device
from ven4control.rdp_tunnel import (
    STATUS_ACTIVE,
    STATUS_FAILED,
    STATUS_STARTING,
    RdpTunnel,
)


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


if __name__ == "__main__":
    unittest.main()
