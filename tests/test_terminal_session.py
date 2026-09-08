import asyncio
import time
import unittest

from PySide6.QtCore import QCoreApplication

from ven4control.ansi_screen import MAX_COLUMNS, MIN_ROWS
from ven4control.models import Device
from ven4control.terminal_session import (
    STATUS_ACTIVE,
    STATUS_CLOSED,
    STATUS_CONNECTING,
    STATUS_FAILED,
    TERM_TYPE,
    TerminalSession,
    closing_message,
    pty_options,
    terminal_status_label,
)


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


class FakeWriter:
    def __init__(self) -> None:
        self.written: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.written.append(data)


class FakeReader:
    """Поток вывода устройства: отдаёт подготовленные порции, потом ждёт."""

    def __init__(self) -> None:
        self.queue: asyncio.Queue = asyncio.Queue()
        self.loop: asyncio.AbstractEventLoop | None = None

    async def read(self, _size: int) -> bytes:
        self.loop = asyncio.get_running_loop()
        item = await self.queue.get()
        if isinstance(item, Exception):
            raise item
        return item


class FakeProcess:
    def __init__(self, options: dict) -> None:
        self.options = options
        self.stdin = FakeWriter()
        self.stdout = FakeReader()
        self.sizes: list[tuple[int, int]] = []
        self.closed = False

    def change_terminal_size(self, width: int, height: int) -> None:
        self.sizes.append((width, height))

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


class FakeConnection:
    """SSH-соединение без сети: запоминает параметры интерактивного канала."""

    def __init__(self) -> None:
        self.process: FakeProcess | None = None
        self.closed = False

    async def create_process(self, **options):
        self.process = FakeProcess(options)
        return self.process

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


class PtyOptionsTests(unittest.TestCase):
    def test_interactive_channel_asks_for_a_pty(self) -> None:
        options = pty_options(80, 24)
        self.assertEqual(TERM_TYPE, options["term_type"])
        self.assertEqual((80, 24), options["term_size"])

    def test_decoding_is_left_to_the_application(self) -> None:
        """UTF-8, разрезанный между пакетами, ломает декодер asyncssh."""
        self.assertIsNone(pty_options(80, 24)["encoding"])

    def test_impossible_size_is_clamped(self) -> None:
        self.assertEqual((MAX_COLUMNS, MIN_ROWS), pty_options(9000, 1)["term_size"])


class StatusLabelTests(unittest.TestCase):
    def test_known_statuses_are_translated(self) -> None:
        self.assertEqual("подключено", terminal_status_label(STATUS_ACTIVE))
        self.assertEqual("сессия закрыта", terminal_status_label(STATUS_CLOSED))

    def test_unknown_status_falls_back_to_raw_value(self) -> None:
        self.assertEqual("нечто", terminal_status_label("нечто"))


class TerminalSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.application = application()
        self.connections: list[FakeConnection] = []
        self.connect_error: Exception | None = None
        self.session: TerminalSession | None = None

    def tearDown(self) -> None:
        if self.session is not None:
            self.session.shutdown(timeout=5)

    async def _connect(self, _device, _credentials) -> FakeConnection:
        if self.connect_error is not None:
            raise self.connect_error
        connection = FakeConnection()
        self.connections.append(connection)
        return connection

    def _session(self, target: Device | None = None, **kwargs) -> TerminalSession:
        self.session = TerminalSession(
            target if target is not None else device(),
            {},
            connect=self._connect,
            **kwargs,
        )
        return self.session

    def _wait(self, condition, timeout: float = 10.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.application.processEvents()
            if condition():
                return True
            time.sleep(0.01)
        return False

    def _started(self, **kwargs) -> tuple[TerminalSession, FakeProcess]:
        session = self._session(**kwargs)
        session.start()
        self.assertTrue(self._wait(lambda: session.status == STATUS_ACTIVE))
        process = self.connections[0].process
        assert process is not None
        return session, process

    def _push(self, process: FakeProcess, item) -> None:
        """Кладёт порцию вывода в поток устройства из потока теста."""
        self.assertTrue(self._wait(lambda: process.stdout.loop is not None))
        loop = process.stdout.loop
        assert loop is not None
        loop.call_soon_threadsafe(process.stdout.queue.put_nowait, item)

    def test_session_opens_an_interactive_shell(self) -> None:
        session, process = self._started(columns=90, rows=25)

        self.assertEqual(TERM_TYPE, process.options["term_type"])
        self.assertEqual((90, 25), process.options["term_size"])
        self.assertTrue(session.active)

    def test_status_starts_from_connecting(self) -> None:
        session = self._session()
        seen: list[str] = []
        session.status_changed.connect(seen.append)

        session.start()

        self.assertTrue(self._wait(lambda: session.status == STATUS_ACTIVE))
        self.assertEqual(STATUS_CONNECTING, seen[0])

    def test_output_of_the_device_reaches_the_interface(self) -> None:
        session, process = self._started()
        received: list[str] = []
        session.output_received.connect(received.append)

        self._push(process, "привет".encode("utf-8"))

        self.assertTrue(self._wait(lambda: received == ["привет"]))

    def test_character_split_between_packets_is_assembled(self) -> None:
        """Чанк канала рвёт UTF-8 посреди символа: строгий декодер тут падал."""
        session, process = self._started()
        received: list[str] = []
        session.output_received.connect(received.append)
        data = "щ".encode("utf-8")

        self._push(process, data[:1])
        self._push(process, data[1:])

        self.assertTrue(self._wait(lambda: "".join(received) == "щ"))

    def test_keystrokes_reach_the_device(self) -> None:
        session, process = self._started()

        self.assertTrue(session.send("ls\r"))

        self.assertTrue(self._wait(lambda: process.stdin.written == [b"ls\r"]))

    def test_empty_input_is_not_sent(self) -> None:
        session, process = self._started()

        self.assertFalse(session.send(""))

        self.assertEqual([], process.stdin.written)

    def test_resize_is_passed_to_the_device(self) -> None:
        session, process = self._started(columns=80, rows=24)

        self.assertTrue(session.resize(120, 40))

        self.assertTrue(self._wait(lambda: process.sizes == [(120, 40)]))

    def test_same_size_is_not_sent_again(self) -> None:
        session, process = self._started(columns=80, rows=24)

        self.assertFalse(session.resize(80, 24))

        self.assertEqual([], process.sizes)

    def test_size_before_the_start_is_used_for_the_pty(self) -> None:
        session = self._session(columns=80, rows=24)
        session.resize(120, 40)

        session.start()

        self.assertTrue(self._wait(lambda: session.status == STATUS_ACTIVE))
        process = self.connections[0].process
        assert process is not None
        self.assertEqual((120, 40), process.options["term_size"])

    def test_closing_the_window_closes_the_shell(self) -> None:
        session, process = self._started()
        finished: list[str] = []
        session.session_finished.connect(finished.append)

        self.assertTrue(session.close())

        self.assertTrue(self._wait(lambda: bool(finished)))
        self.assertEqual(STATUS_CLOSED, session.status)
        self.assertTrue(process.closed)
        self.assertTrue(self.connections[0].closed)
        self.assertEqual(["Терминал «Роутер» закрыт."], finished)

    def test_remote_side_closing_the_channel_ends_the_session(self) -> None:
        """Пользователь набрал `exit`: канал закрывается со стороны устройства."""
        session, process = self._started()

        self._push(process, b"")

        self.assertTrue(self._wait(lambda: session.status == STATUS_CLOSED))
        self.assertTrue(self.connections[0].closed)
        self.assertFalse(session.active)

    def test_broken_connection_is_reported_and_not_hidden(self) -> None:
        session, process = self._started()
        finished: list[str] = []
        session.session_finished.connect(finished.append)

        self._push(process, ConnectionResetError("соединение потеряно"))

        self.assertTrue(self._wait(lambda: session.status == STATUS_FAILED))
        self.assertIn("соединение потеряно", session.error)
        self.assertTrue(self._wait(lambda: bool(finished)))
        self.assertIn("не открыт", finished[0])

    def test_failed_connection_leaves_no_open_session(self) -> None:
        self.connect_error = RuntimeError("fingerprint устройства изменился")
        session = self._session()

        session.start()

        self.assertTrue(self._wait(lambda: session.status == STATUS_FAILED))
        self.assertIn("fingerprint", session.error)
        self.assertIn("fingerprint", closing_message(session))
        self.assertFalse(session.active)

    def test_device_without_fingerprint_is_rejected(self) -> None:
        """Терминал — то же доверенное соединение: без fingerprint нельзя."""
        session = self._session(device(fingerprint=""))
        with self.assertRaises(ValueError):
            session.start()

    def test_second_start_does_not_open_a_second_shell(self) -> None:
        session, _process = self._started()

        session.start()

        self.assertEqual(1, len(self.connections))

    def test_input_before_the_start_is_not_lost_silently(self) -> None:
        session = self._session()
        self.assertFalse(session.send("ls\r"))
        self.assertFalse(session.close())

    def test_shutdown_of_a_session_that_never_started_is_safe(self) -> None:
        session = self._session()
        session.shutdown(timeout=1)
        self.assertEqual(STATUS_CLOSED, session.status)


if __name__ == "__main__":
    unittest.main()
