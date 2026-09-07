import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from ven4control.log_sessions import LogSessionManager
from ven4control.log_worker import STATUS_STOPPED, STATUS_STREAMING
from ven4control.models import Device


def device(device_id: int = 1, name: str = "Router") -> Device:
    return Device(
        device_id, name, "192.168.99.1", 22, "root",
        auth_type="key", key_path="C:/keys/id_ed25519",
        fingerprint="SHA256:router",
    )


class FakeWorker:
    """Заменяет SSH-воркера: пишет пару строк и ждёт остановки."""

    created: list["FakeWorker"] = []

    def __init__(self, device, credentials, writer, *, source="system",
                 on_status=None, on_line=None, **_options):
        self.device = device
        self.credentials = credentials
        self.writer = writer
        self.source = source
        self.on_status = on_status
        self.on_line = on_line
        self.status = STATUS_STOPPED
        self._stop = asyncio.Event()
        FakeWorker.created.append(self)

    def request_stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        self.status = STATUS_STREAMING
        if self.on_status:
            self.on_status(STATUS_STREAMING)
        for number in range(3):
            self.writer.write_line(f"строка {number}")
            if self.on_line:
                self.on_line(f"строка {number}")
        await self._stop.wait()
        self.writer.write_line("=== СЕССИЯ ОСТАНОВЛЕНА ===")


class SessionManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeWorker.created = []
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        patcher = patch("ven4control.log_sessions.LogStreamWorker", FakeWorker)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.temporary.cleanup)
        self.manager = LogSessionManager(self.root)
        self.addCleanup(self.manager.shutdown)

    def test_session_writes_log_and_exports_on_stop(self) -> None:
        session = self.manager.start(device(), {}, export_format="txt")
        self.assertTrue(self.manager.is_active(1))

        self.manager.stop_all(timeout=10)

        self.assertFalse(self.manager.is_active(1))
        self.assertEqual(STATUS_STOPPED, self.manager.status(1))
        raw = session.writer.parts[0].read_text(encoding="utf-8")
        self.assertIn("строка 0", raw)
        self.assertIn("=== СЕССИЯ ОСТАНОВЛЕНА ===", raw)
        exported = list((session.directory / "export").glob("*.txt"))
        self.assertEqual(1, len(exported))

    def test_logs_live_in_the_application_tree(self) -> None:
        session = self.manager.start(device(name="Домашний роутер"), {})
        self.addCleanup(self.manager.stop_all)

        self.assertEqual(self.root, session.directory.parent.parent)
        self.assertTrue(session.directory.is_dir())

    def test_repeated_start_does_not_create_second_session(self) -> None:
        first = self.manager.start(device(), {})
        second = self.manager.start(device(), {})
        self.addCleanup(self.manager.stop_all)

        self.assertIs(first, second)
        self.assertEqual(1, self.manager.active_count())
        self.assertEqual(1, len(FakeWorker.created))

    def test_several_devices_share_one_event_loop(self) -> None:
        self.manager.start(device(1, "Router"), {})
        self.manager.start(device(2, "Server"), {})

        self.assertEqual(2, self.manager.active_count())
        self.assertTrue(self.manager.is_active(2))

        self.manager.stop_all(timeout=10)

        self.assertEqual(0, self.manager.active_count())

    def test_status_of_unknown_device_is_stopped(self) -> None:
        self.assertEqual(STATUS_STOPPED, self.manager.status(404))
        self.assertFalse(self.manager.is_active(404))
        self.assertFalse(self.manager.is_active(None))
        self.assertIsNone(self.manager.session(404))

    def test_stop_of_unknown_device_is_reported(self) -> None:
        self.assertFalse(self.manager.stop(404))

    def test_unsaved_device_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.manager.start(Device(None, "Новое", "192.0.2.10", 22, "root"), {})

    def test_source_is_passed_to_the_worker(self) -> None:
        self.manager.start(device(), {}, source="tailscale")
        self.addCleanup(self.manager.stop_all)

        self.assertEqual("tailscale", FakeWorker.created[0].source)

    def test_failed_export_does_not_hide_the_raw_log(self) -> None:
        session = self.manager.start(device(), {}, export_format="pdf")
        messages: list[str] = []
        self.manager.session_finished.connect(
            lambda _device_id, text: messages.append(text)
        )

        self.manager.stop_all(timeout=10)

        self.assertTrue(session.writer.parts[0].exists())
        self.assertFalse(self.manager.is_active(1))


class RealWorkerSessionTests(unittest.TestCase):
    """Менеджер с настоящим воркером: подменено только SSH-подключение."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.manager = LogSessionManager(Path(self.temporary.name))
        self.addCleanup(self.manager.shutdown)

    def test_session_writes_stream_and_exports_it(self) -> None:
        from test_log_worker import FakeConnection, connect_sequence

        connection = FakeConnection(["первая строка\n", "вторая строка\n"])
        connect, _ = connect_sequence([connection])
        with patch("ven4control.log_worker._connect", connect):
            session = self.manager.start(device(), {}, export_format="txt")
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if session.writer.total_lines >= 3:
                    break
                time.sleep(0.05)
            self.manager.stop_all(timeout=10)

        raw = session.writer.parts[0].read_text(encoding="utf-8")
        self.assertIn("=== ПОДКЛЮЧЕНО", raw)
        self.assertIn("вторая строка", raw)
        self.assertIn("=== СЕССИЯ ОСТАНОВЛЕНА ===", raw)
        self.assertEqual(1, len(list((session.directory / "export").glob("*.txt"))))
        self.assertTrue(connection.closed)


if __name__ == "__main__":
    unittest.main()
