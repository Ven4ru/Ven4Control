import asyncio
import unittest
from types import SimpleNamespace

from ven4control.log_worker import (
    RECONNECT_DELAYS,
    STATUS_FAILED,
    STATUS_STOPPED,
    LogStreamWorker,
    build_stream_command,
    connected_marker,
    disconnected_marker,
    format_gap,
    format_snapshot,
    reconnect_delay,
    reconnected_marker,
    snapshot_failure,
    status_label,
)
from ven4control.models import Device
from ven4control.remote_control import FingerprintError


SNAPSHOT_OUTPUT = (
    "CPU=3.4%\n"
    "MEM=120/512 MiB (23.4%)\n"
    "DISK=1.2/7.4 GiB (17%)\n"
    "UPTIME=up 3 days\n"
    "CONNTRACK=482\n"
)


def device() -> Device:
    return Device(
        1, "Роутер", "192.168.99.1", 22, "root",
        auth_type="key", key_path="C:/keys/id_ed25519",
        fingerprint="SHA256:router",
    )


class StreamCommandTests(unittest.TestCase):
    def test_unknown_source_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            build_stream_command("openwrt", "secrets")

    def test_openwrt_checks_logread_first(self) -> None:
        command = build_stream_command("openwrt")
        self.assertIn("command -v logread", command)
        self.assertLess(command.index("logread -f"), command.index("journalctl"))

    def test_linux_checks_journalctl_first(self) -> None:
        command = build_stream_command("linux")
        self.assertIn("command -v journalctl", command)
        self.assertLess(command.index("journalctl -f"), command.index("logread"))

    def test_filtered_source_keeps_stream_unbuffered(self) -> None:
        """Без --line-buffered grep отдаёт строки пачками по 4 КБ."""
        command = build_stream_command("openwrt", "tailscale")
        self.assertIn("grep -i --line-buffered tailscale", command)
        self.assertIn("journalctl -u tailscaled -f", command)

    def test_every_source_has_both_variants(self) -> None:
        for source in ("system", "tailscale"):
            for platform in ("openwrt", "linux"):
                with self.subTest(source=source, platform=platform):
                    command = build_stream_command(platform, source)
                    self.assertIn("logread", command)
                    self.assertIn("journalctl", command)

    def test_stream_never_limits_history(self) -> None:
        """У стрима не должно быть `tail -n`: он читает новые записи."""
        self.assertNotIn("tail -n", build_stream_command("openwrt"))
        self.assertIn("-n 0", build_stream_command("linux"))


class ReconnectTimingTests(unittest.TestCase):
    def test_delays_follow_the_list(self) -> None:
        self.assertEqual(
            list(RECONNECT_DELAYS),
            [reconnect_delay(attempt) for attempt in range(len(RECONNECT_DELAYS))],
        )

    def test_last_delay_is_held_after_the_list_ends(self) -> None:
        self.assertEqual(RECONNECT_DELAYS[-1], reconnect_delay(99))

    def test_negative_attempt_uses_first_delay(self) -> None:
        self.assertEqual(RECONNECT_DELAYS[0], reconnect_delay(-3))

    def test_empty_list_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            reconnect_delay(0, ())


class MarkerTests(unittest.TestCase):
    def test_gap_is_readable(self) -> None:
        self.assertEqual("7 с", format_gap(7.4))
        self.assertEqual("1 мин 5 с", format_gap(65))
        self.assertEqual("1 ч 1 мин", format_gap(3700))
        self.assertEqual("0 с", format_gap(-1))

    def test_markers_name_device_and_reason(self) -> None:
        self.assertIn("Роутер", connected_marker("Роутер", "192.168.99.1"))
        self.assertIn("192.168.99.1", connected_marker("Роутер", "192.168.99.1"))
        self.assertIn("нет маршрута", disconnected_marker(OSError("нет маршрута")))
        self.assertIn("2 мин 0 с", reconnected_marker(120))

    def test_empty_reason_does_not_produce_blank_marker(self) -> None:
        self.assertIn("соединение закрыто", disconnected_marker(""))

    def test_status_label_falls_back_to_raw_value(self) -> None:
        self.assertEqual("остановлено", status_label(STATUS_STOPPED))
        self.assertEqual("нечто", status_label("нечто"))


class SnapshotTests(unittest.TestCase):
    def test_all_fields_are_rendered(self) -> None:
        lines = format_snapshot(SNAPSHOT_OUTPUT)
        self.assertEqual("=== СНАПШОТ ===", lines[0])
        self.assertEqual("=== /СНАПШОТ ===", lines[-1])
        body = "\n".join(lines)
        self.assertIn("Загрузка CPU: 3.4%", body)
        self.assertIn("Память: 120/512 MiB (23.4%)", body)
        self.assertIn("Соединений conntrack: 482", body)
        self.assertIn("Время работы: up 3 days", body)

    def test_missing_values_are_marked(self) -> None:
        lines = format_snapshot("CPU=1.0%\n")
        self.assertIn("Соединений conntrack: нет данных", "\n".join(lines))

    def test_failure_is_reported_as_marker(self) -> None:
        self.assertIn("нет ответа", snapshot_failure("нет ответа")[0])


class FakeWriter:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def write_line(self, text: str, prefix_timestamp: bool = True) -> None:
        self.lines.append(text)


class FakeStdout:
    def __init__(self, lines: list[str], keep_open: bool) -> None:
        self._lines = list(lines)
        self._keep_open = keep_open

    def __aiter__(self) -> "FakeStdout":
        return self

    async def __anext__(self) -> str:
        if self._lines:
            return self._lines.pop(0)
        if self._keep_open:
            # Живой канал: строк пока нет, но поток не закрыт.
            await asyncio.Event().wait()
        raise StopAsyncIteration


class FakeProcess:
    def __init__(self, stdout: FakeStdout) -> None:
        self.stdout = stdout

    async def __aenter__(self) -> "FakeProcess":
        return self

    async def __aexit__(self, *_exception) -> bool:
        return False


class FakeConnection:
    def __init__(
        self,
        lines: list[str] | None = None,
        *,
        platform: str = "openwrt",
        keep_open: bool = True,
    ) -> None:
        self.lines = lines or []
        self.platform = platform
        self.keep_open = keep_open
        self.commands: list[str] = []
        self.closed = False

    async def run(self, command: str, check: bool = False, timeout: int = 60):
        self.commands.append(command)
        if "openwrt_release" in command:
            return SimpleNamespace(
                stdout=f"{self.platform}\nТестовая система\n", stderr="", exit_status=0
            )
        return SimpleNamespace(stdout=SNAPSHOT_OUTPUT, stderr="", exit_status=0)

    def create_process(self, command: str) -> FakeProcess:
        self.commands.append(command)
        return FakeProcess(FakeStdout(self.lines, self.keep_open))

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


def connect_sequence(items: list[object]):
    """Возвращает функцию подключения, выдающую подготовленные ответы."""
    attempts: list[object] = []

    async def connect(_device, _credentials):
        item = items[min(len(attempts), len(items) - 1)]
        attempts.append(item)
        if isinstance(item, BaseException):
            raise item
        return item

    return connect, attempts


class WorkerTests(unittest.IsolatedAsyncioTestCase):
    async def _wait_for(self, writer: FakeWriter, fragment: str) -> None:
        for _ in range(400):
            if any(fragment in line for line in writer.lines):
                return
            await asyncio.sleep(0.01)
        raise AssertionError(f"Строка «{fragment}» не появилась: {writer.lines}")

    def _worker(self, writer: FakeWriter, connect, **options) -> LogStreamWorker:
        return LogStreamWorker(
            device(),
            {"password": "", "passphrase": ""},
            writer,
            reconnect_delays=(0.01,),
            snapshot_interval=options.pop("snapshot_interval", 60.0),
            connect=connect,
            **options,
        )

    async def _stop(self, worker: LogStreamWorker, task: asyncio.Task) -> None:
        worker.request_stop()
        await asyncio.wait_for(task, 5)

    async def test_lines_are_written_until_stop(self) -> None:
        writer = FakeWriter()
        connection = FakeConnection(["первая\n", "вторая\n"])
        connect, _ = connect_sequence([connection])
        worker = self._worker(writer, connect)
        task = asyncio.create_task(worker.run())

        await self._wait_for(writer, "вторая")
        await self._stop(worker, task)

        self.assertIn("первая", writer.lines)
        self.assertIn(connected_marker("Роутер", "192.168.99.1"), writer.lines)
        self.assertEqual(STATUS_STOPPED, worker.status)
        self.assertTrue(connection.closed)

    async def test_stream_command_matches_detected_platform(self) -> None:
        writer = FakeWriter()
        connection = FakeConnection(["строка\n"], platform="linux")
        connect, _ = connect_sequence([connection])
        worker = self._worker(writer, connect)
        task = asyncio.create_task(worker.run())

        await self._wait_for(writer, "строка")
        await self._stop(worker, task)

        streams = [item for item in connection.commands if "journalctl -f" in item]
        self.assertTrue(streams, connection.commands)

    async def test_broken_connection_is_restored(self) -> None:
        writer = FakeWriter()
        connection = FakeConnection(["после разрыва\n"])
        connect, attempts = connect_sequence(
            [OSError("сеть недоступна"), connection]
        )
        worker = self._worker(writer, connect)
        task = asyncio.create_task(worker.run())

        await self._wait_for(writer, "после разрыва")
        await self._stop(worker, task)

        self.assertIn("сеть недоступна", "\n".join(writer.lines))
        self.assertIn("ПЕРЕПОДКЛЮЧЕНО", "\n".join(writer.lines))
        self.assertGreaterEqual(len(attempts), 2)

    async def test_closed_stream_triggers_reconnect(self) -> None:
        """Перезагрузка устройства обрывает канал, а не сессию."""
        writer = FakeWriter()
        first = FakeConnection(["до перезагрузки\n"], keep_open=False)
        second = FakeConnection(["после перезагрузки\n"])
        connect, _ = connect_sequence([first, second])
        worker = self._worker(writer, connect)
        task = asyncio.create_task(worker.run())

        await self._wait_for(writer, "после перезагрузки")
        await self._stop(worker, task)

        body = "\n".join(writer.lines)
        self.assertIn("закрыла поток журнала", body)
        self.assertIn("ПЕРЕПОДКЛЮЧЕНО", body)

    async def test_fingerprint_error_stops_session(self) -> None:
        writer = FakeWriter()
        connect, attempts = connect_sequence(
            [FingerprintError("SSH fingerprint устройства изменился.")]
        )
        worker = self._worker(writer, connect)

        await asyncio.wait_for(worker.run(), 5)

        self.assertEqual(STATUS_FAILED, worker.status)
        self.assertEqual(1, len(attempts))
        self.assertIn("fingerprint", "\n".join(writer.lines))

    async def test_snapshot_is_written_periodically(self) -> None:
        writer = FakeWriter()
        connection = FakeConnection()
        connect, _ = connect_sequence([connection])
        worker = self._worker(writer, connect, snapshot_interval=0.01)
        task = asyncio.create_task(worker.run())

        await self._wait_for(writer, "Соединений conntrack: 482")
        await self._stop(worker, task)

        self.assertIn("=== СНАПШОТ ===", writer.lines)

    async def test_statuses_are_reported(self) -> None:
        writer = FakeWriter()
        statuses: list[str] = []
        connection = FakeConnection(["строка\n"])
        connect, _ = connect_sequence([OSError("нет сети"), connection])
        worker = self._worker(writer, connect, on_status=statuses.append)
        task = asyncio.create_task(worker.run())

        await self._wait_for(writer, "строка")
        await self._stop(worker, task)

        self.assertIn("connecting", statuses)
        self.assertIn("reconnecting", statuses)
        self.assertIn("streaming", statuses)
        self.assertEqual("stopped", statuses[-1])


if __name__ == "__main__":
    unittest.main()
