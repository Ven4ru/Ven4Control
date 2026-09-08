import asyncio
import time
import unittest

import asyncssh
from PySide6.QtCore import QCoreApplication

from ven4control.models import Device
from ven4control.sftp_session import (
    KIND_DIRECTORY,
    KIND_FILE,
    KIND_LINK,
    KIND_UNKNOWN,
    STATUS_CONNECTING,
    STATUS_FAILED,
    STATUS_READY,
    RemoteEntry,
    SftpSession,
    child_path,
    describe_error,
    failure_message,
    format_size,
    is_connection_lost,
    is_reportable,
    parent_path,
    parse_listing,
    progress_message,
    sftp_status_label,
)


HANGING_PATH = "/hang"

DIRECTORY_MODE = 0o040755
FILE_MODE = 0o100644
LINK_MODE = 0o120777


def application() -> QCoreApplication:
    """Сигналы из служебного потока доставляются только через очередь Qt."""
    existing = QCoreApplication.instance()
    return existing if existing is not None else QCoreApplication([])


def device(name: str = "Роутер", fingerprint: str = "SHA256:router") -> Device:
    return Device(
        1, name, "100.64.0.1", 22, "root",
        auth_type="key", key_path="C:/keys/id_ed25519",
        fingerprint=fingerprint,
    )


class FakeAttrs:
    def __init__(self, permissions: int | None, size: int | None = None) -> None:
        self.permissions = permissions
        self.size = size


class FakeName:
    """Запись листинга в том виде, в каком её отдаёт asyncssh."""

    def __init__(self, filename, permissions: int | None = FILE_MODE, size=0) -> None:
        self.filename = filename
        self.attrs = FakeAttrs(permissions, size)


class FakeSftpClient:
    def __init__(self, listings: dict[str, list[FakeName]]) -> None:
        self.listings = listings
        self.errors: dict[str, Exception] = {}
        self.transfers: list[tuple[str, str, str]] = []
        self.progress: list[tuple[int, int]] = []
        self.transfer_error: Exception | None = None
        self.closed = False

    async def realpath(self, path: str) -> str:
        if isinstance(self.errors.get(path), Exception):
            raise self.errors[path]
        return "/root" if path == "." else path

    async def readdir(self, path: str) -> list[FakeName]:
        error = self.errors.get(path)
        if error is not None:
            raise error
        if path == HANGING_PATH:
            await asyncio.sleep(60)
        return self.listings.get(path, [])

    async def get(self, remote, local, progress_handler=None) -> None:
        await self._transfer("get", remote, local, progress_handler)

    async def put(self, local, remote, progress_handler=None) -> None:
        await self._transfer("put", local, remote, progress_handler)

    async def _transfer(self, kind, source, target, progress_handler) -> None:
        if self.transfer_error is not None:
            raise self.transfer_error
        for done, total in self.progress:
            if progress_handler is not None:
                progress_handler(source, target, done, total)
        self.transfers.append((kind, source, target))

    def exit(self) -> None:
        self.closed = True


class FakeConnection:
    def __init__(self, client: FakeSftpClient) -> None:
        self.client = client
        self.closed = False
        self.sftp_error: Exception | None = None

    async def start_sftp_client(self) -> FakeSftpClient:
        if self.sftp_error is not None:
            raise self.sftp_error
        return self.client

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


class ListingTests(unittest.TestCase):
    def test_directories_are_shown_before_files(self) -> None:
        entries = parse_listing([
            FakeName("readme.txt"),
            FakeName("etc", DIRECTORY_MODE),
            FakeName("Bin", DIRECTORY_MODE),
            FakeName("archive.tar"),
        ])

        self.assertEqual(
            ["Bin", "etc", "archive.tar", "readme.txt"],
            [entry.name for entry in entries],
        )

    def test_current_and_parent_are_not_listed(self) -> None:
        """Подъём наверх сделан кнопкой, строки «.» и «..» только мешают."""
        entries = parse_listing([
            FakeName(".", DIRECTORY_MODE),
            FakeName("..", DIRECTORY_MODE),
            FakeName("config"),
        ])

        self.assertEqual(["config"], [entry.name for entry in entries])

    def test_entry_types_are_recognised(self) -> None:
        entries = parse_listing([
            FakeName("etc", DIRECTORY_MODE),
            FakeName("passwd", FILE_MODE),
            FakeName("link", LINK_MODE),
        ])
        kinds = {entry.name: entry.kind for entry in entries}

        self.assertEqual(KIND_DIRECTORY, kinds["etc"])
        self.assertEqual(KIND_FILE, kinds["passwd"])
        self.assertEqual(KIND_LINK, kinds["link"])

    def test_permissions_are_shown_as_usual(self) -> None:
        entry = parse_listing([FakeName("passwd", FILE_MODE, 1024)])[0]

        self.assertEqual("-rw-r--r--", entry.permissions_label)
        self.assertEqual("1.0 КиБ", entry.size_label)

    def test_missing_attributes_are_reported_not_guessed(self) -> None:
        """Сервер может не прислать режим: молчать про это нельзя."""
        entry = parse_listing([FakeName("secret", None, None)])[0]

        self.assertEqual(KIND_UNKNOWN, entry.kind)
        self.assertEqual("нет данных", entry.permissions_label)
        self.assertEqual("нет данных", entry.size_label)
        self.assertTrue(entry.can_enter)

    def test_cyrillic_name_in_bytes_is_decoded(self) -> None:
        entry = parse_listing([FakeName("отчёт.txt".encode("utf-8"))])[0]

        self.assertEqual("отчёт.txt", entry.name)

    def test_directory_size_is_not_shown(self) -> None:
        entry = RemoteEntry("etc", KIND_DIRECTORY, 4096, "drwxr-xr-x")

        self.assertEqual("—", entry.size_label)
        self.assertEqual("папка", entry.kind_label)

    def test_file_is_not_entered_by_double_click(self) -> None:
        self.assertFalse(RemoteEntry("passwd", KIND_FILE).can_enter)
        self.assertTrue(RemoteEntry("share", KIND_LINK).can_enter)


class SizeTests(unittest.TestCase):
    def test_small_size_stays_in_bytes(self) -> None:
        self.assertEqual("0 Б", format_size(0))
        self.assertEqual("512 Б", format_size(512))

    def test_large_size_gets_a_unit(self) -> None:
        self.assertEqual("1.0 КиБ", format_size(1024))
        self.assertEqual("2.5 МиБ", format_size(int(2.5 * 1024 * 1024)))
        self.assertEqual("1.0 ГиБ", format_size(1024 ** 3))

    def test_unknown_size_is_named(self) -> None:
        self.assertEqual("нет данных", format_size(None))


class NavigationTests(unittest.TestCase):
    def test_entering_a_directory(self) -> None:
        self.assertEqual("/root/logs", child_path("/root", "logs"))
        self.assertEqual("/etc", child_path("/", "etc"))

    def test_going_up(self) -> None:
        self.assertEqual("/root", parent_path("/root/logs"))
        self.assertEqual("/", parent_path("/root"))

    def test_root_has_no_parent(self) -> None:
        self.assertEqual("/", parent_path("/"))

    def test_extra_separators_are_removed(self) -> None:
        self.assertEqual("/root/logs", child_path("/root/", "logs"))
        self.assertEqual("/var", child_path("/var/log", "../"))

    def test_windows_style_remote_root_survives(self) -> None:
        """OpenSSH на Windows отдаёт пути вида /C:/Users."""
        self.assertEqual("/C:/Users", child_path("/C:", "Users"))
        self.assertEqual("/C:", parent_path("/C:/Users"))


class ErrorTests(unittest.TestCase):
    def test_permission_denied_is_explained(self) -> None:
        message = failure_message(
            "открыть", "/root", asyncssh.SFTPPermissionDenied("Permission denied")
        )

        self.assertIn("/root", message)
        self.assertIn("нет прав доступа", message)

    def test_missing_file_hints_at_deletion(self) -> None:
        """Файл мог исчезнуть между листингом и скачиванием."""
        message = failure_message(
            "скачать", "dump.bin", asyncssh.SFTPNoSuchFile("No such file")
        )

        self.assertIn("не найдены", message)

    def test_operation_failure_does_not_mean_a_broken_connection(self) -> None:
        self.assertFalse(is_connection_lost(asyncssh.SFTPPermissionDenied("нет")))

    def test_broken_connection_is_recognised(self) -> None:
        self.assertTrue(is_connection_lost(ConnectionResetError("сброшено")))
        self.assertTrue(is_connection_lost(asyncssh.SFTPConnectionLost("потеряно")))
        self.assertEqual(
            "соединение с устройством потеряно",
            describe_error(ConnectionResetError("сброшено")),
        )

    def test_local_file_error_keeps_its_text(self) -> None:
        self.assertIn("нет места", describe_error(OSError("нет места на диске")))

    def test_known_statuses_are_translated(self) -> None:
        self.assertEqual("подключено", sftp_status_label(STATUS_READY))
        self.assertEqual("нечто", sftp_status_label("нечто"))


class ProgressTests(unittest.TestCase):
    def test_progress_shows_percent_and_size(self) -> None:
        message = progress_message("Скачивание", "dump.bin", 512 * 1024, 1024 * 1024)

        self.assertIn("50%", message)
        self.assertIn("512.0 КиБ из 1.0 МиБ", message)

    def test_unknown_total_shows_transferred_bytes(self) -> None:
        self.assertIn("передано 512 Б", progress_message("Загрузка", "x", 512, None))

    def test_small_steps_do_not_repaint_the_status(self) -> None:
        self.assertFalse(is_reportable(4096, 1024 ** 3, 0))
        self.assertTrue(is_reportable(2 * 1024 ** 2, 1024 ** 3, 0))

    def test_finished_transfer_is_always_reported(self) -> None:
        self.assertTrue(is_reportable(100, 100, 0))
        self.assertFalse(is_reportable(100, 100, 100))


class SftpSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.application = application()
        self.client = FakeSftpClient({"/root": [FakeName("config")]})
        self.connections: list[FakeConnection] = []
        self.connect_error: Exception | None = None
        self.sftp_error: Exception | None = None
        self.session: SftpSession | None = None
        self.listings: list[tuple[str, list[RemoteEntry]]] = []
        self.failures: list[str] = []
        self.progress: list[str] = []
        self.finished: list[str] = []

    def tearDown(self) -> None:
        if self.session is not None:
            self.session.shutdown(timeout=5)

    async def _connect(self, _device, _credentials) -> FakeConnection:
        if self.connect_error is not None:
            raise self.connect_error
        connection = FakeConnection(self.client)
        connection.sftp_error = self.sftp_error
        self.connections.append(connection)
        return connection

    def _session(self, target: Device | None = None) -> SftpSession:
        session = SftpSession(
            target if target is not None else device(),
            {},
            connect=self._connect,
        )
        session.listing_received.connect(
            lambda path, entries: self.listings.append((path, list(entries)))
        )
        session.operation_failed.connect(self.failures.append)
        session.progress_changed.connect(self.progress.append)
        session.transfer_finished.connect(self.finished.append)
        self.session = session
        return session

    def _wait(self, condition, timeout: float = 10.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.application.processEvents()
            if condition():
                return True
            time.sleep(0.01)
        return False

    def _opened(self) -> SftpSession:
        session = self._session()
        session.start()
        session.list_directory()
        self.assertTrue(self._wait(lambda: bool(self.listings)))
        return session

    def test_home_directory_is_listed_after_connect(self) -> None:
        session = self._opened()

        path, entries = self.listings[0]
        self.assertEqual("/root", path)
        self.assertEqual(["config"], [entry.name for entry in entries])
        self.assertEqual("/root", session.path)

    def test_status_starts_from_connecting(self) -> None:
        session = self._session()
        seen: list[str] = []
        session.status_changed.connect(seen.append)

        session.start()

        self.assertTrue(self._wait(lambda: session.status == STATUS_READY))
        self.assertEqual(STATUS_CONNECTING, seen[0])

    def test_one_connection_serves_the_whole_session(self) -> None:
        """Ради этого соединение и держится: клик по папке не платит handshake."""
        session = self._opened()
        self.client.listings["/root/logs"] = [FakeName("system.log")]

        session.list_directory(child_path(session.path, "logs"))

        self.assertTrue(self._wait(lambda: len(self.listings) == 2))
        self.assertEqual("/root/logs", self.listings[1][0])
        self.assertEqual(1, len(self.connections))

    def test_directory_without_access_is_explained(self) -> None:
        session = self._opened()
        self.client.errors["/root/private"] = asyncssh.SFTPPermissionDenied("denied")

        session.list_directory("/root/private")

        self.assertTrue(self._wait(lambda: bool(self.failures)))
        self.assertIn("нет прав доступа", self.failures[0])
        # Соединение живо: отказ одной папки не закрывает сессию.
        self.assertEqual(STATUS_READY, session.status)
        self.assertEqual("/root", session.path)

    def test_broken_connection_stops_the_session(self) -> None:
        session = self._opened()
        self.client.errors["/root/logs"] = ConnectionResetError("связь потеряна")

        session.list_directory("/root/logs")

        self.assertTrue(self._wait(lambda: bool(self.failures)))
        self.assertIn("соединение с устройством потеряно", self.failures[0])
        self.assertEqual(STATUS_FAILED, session.status)

    def test_download_saves_the_selected_file(self) -> None:
        session = self._opened()
        self.client.progress = [(1024 ** 2 * 2, 1024 ** 2 * 4)]

        session.download("config", "C:/tmp/config")

        self.assertTrue(self._wait(lambda: bool(self.finished)))
        self.assertEqual([("get", "/root/config", "C:/tmp/config")], self.client.transfers)
        self.assertIn("Скачивание «config»…", self.progress)
        self.assertTrue(any("50%" in text for text in self.progress))

    def test_download_of_a_vanished_file_is_reported(self) -> None:
        session = self._opened()
        self.client.transfer_error = asyncssh.SFTPNoSuchFile("No such file")

        session.download("config", "C:/tmp/config")

        self.assertTrue(self._wait(lambda: bool(self.failures)))
        self.assertIn("не найдены", self.failures[0])
        self.assertEqual([], self.finished)

    def test_upload_goes_to_the_current_directory_and_refreshes_it(self) -> None:
        session = self._opened()

        session.upload("C:/tmp/notes.txt")

        self.assertTrue(self._wait(lambda: bool(self.finished)))
        self.assertEqual(
            [("put", "C:/tmp/notes.txt", "/root/notes.txt")], self.client.transfers
        )
        self.assertTrue(self._wait(lambda: len(self.listings) == 2))

    def test_upload_without_rights_is_reported(self) -> None:
        session = self._opened()
        self.client.transfer_error = asyncssh.SFTPPermissionDenied("denied")

        session.upload("C:/tmp/notes.txt")

        self.assertTrue(self._wait(lambda: bool(self.failures)))
        self.assertIn("загрузить «notes.txt»", self.failures[0])
        self.assertIn("нет прав доступа", self.failures[0])

    def test_failed_connection_is_reported_once(self) -> None:
        self.connect_error = RuntimeError("fingerprint устройства изменился")
        session = self._session()

        session.start()
        session.list_directory()

        self.assertTrue(self._wait(lambda: len(self.failures) == 2))
        self.assertEqual(STATUS_FAILED, session.status)
        self.assertIn("fingerprint", self.failures[0])
        self.assertIn("Нет соединения", self.failures[1])
        self.assertEqual([], self.listings)

    def test_missing_sftp_subsystem_is_explained(self) -> None:
        """Живая находка: dropbear на OpenWrt без openssh-sftp-server рвёт

        канал сразу же в ответ на запрос подсистемы SFTP, и asyncssh поднимает
        SFTPConnectionLost — на вид неотличимо от обрыва связи, хотя
        SSH-соединение живо и сеть тут ни при чём.
        """
        self.sftp_error = asyncssh.SFTPConnectionLost(
            "0 bytes read on a total of 4 expected bytes"
        )
        session = self._session()

        session.start()

        # Ждём именно сигнал, а не self.status: тот меняется на фоновом
        # потоке раньше, чем operation_failed доставится в очередь Qt, —
        # опрос status первым иногда обгонял бы список failures.
        self.assertTrue(self._wait(lambda: bool(self.failures)))
        self.assertEqual(STATUS_FAILED, session.status)
        self.assertEqual(1, len(self.failures))
        self.assertIn("не поддерживает подсистему SFTP", self.failures[0])
        self.assertNotIn("потеряно", self.failures[0])

    def test_device_without_fingerprint_is_rejected(self) -> None:
        """Файлы — то же доверенное соединение: без fingerprint нельзя."""
        session = self._session(device(fingerprint=""))
        with self.assertRaises(ValueError):
            session.start()

    def test_operations_before_the_start_are_not_lost_silently(self) -> None:
        session = self._session()

        self.assertFalse(session.list_directory())
        self.assertFalse(session.download("config", "C:/tmp/config"))
        self.assertFalse(session.upload("C:/tmp/notes.txt"))

    def test_second_start_does_not_open_a_second_connection(self) -> None:
        session = self._opened()

        session.start()

        self.assertEqual(1, len(self.connections))

    def test_closing_the_dialog_closes_the_connection(self) -> None:
        session = self._opened()

        session.shutdown(timeout=5)

        self.assertTrue(self.client.closed)
        self.assertTrue(self.connections[0].closed)

    def test_closing_does_not_wait_for_a_hanging_operation(self) -> None:
        """Устройство перестало отвечать: окно всё равно должно закрыться."""
        session = self._opened()
        session.list_directory(HANGING_PATH)
        started = time.monotonic()

        session.shutdown(timeout=5)

        self.assertLess(time.monotonic() - started, 5)
        self.assertTrue(self.connections[0].closed)

    def test_shutdown_of_a_session_that_never_started_is_safe(self) -> None:
        session = self._session()
        session.shutdown(timeout=1)
        self.assertFalse(session.ready)


if __name__ == "__main__":
    unittest.main()
