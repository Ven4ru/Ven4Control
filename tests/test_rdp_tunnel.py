import time
import unittest

from ven4control.models import Device
from ven4control.rdp_tunnel import (
    LOOPBACK,
    STATUS_ACTIVE,
    STATUS_CLOSED,
    STATUS_FAILED,
    RdpTunnelManager,
    closing_message,
    mstsc_command,
    tunnel_status_label,
)


def device(device_id: int = 1, name: str = "ПК", rdp_port: int = 3389) -> Device:
    return Device(
        device_id, name, "100.64.0.7", 22, "user",
        auth_type="key", key_path="C:/keys/id_ed25519",
        fingerprint="SHA256:pc", rdp_port=rdp_port,
    )


class FakeListener:
    def __init__(self, port: int) -> None:
        self.port = port
        self.closed = False

    def get_port(self) -> int:
        return self.port

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


class FakeConnection:
    """SSH-соединение без сети: запоминает параметры проброса."""

    def __init__(self, port: int = 54321) -> None:
        self.listener = FakeListener(port)
        self.forwards: list[tuple[str, int, str, int]] = []
        self.closed = False

    async def forward_local_port(self, host, port, target_host, target_port):
        self.forwards.append((host, port, target_host, target_port))
        return self.listener

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


class FakeProcess:
    """Окно RDP: живёт, пока его не закрыл пользователь или менеджер."""

    def __init__(self, args: list[str]) -> None:
        self.args = args
        self.code: int | None = None
        self.terminated = False

    def poll(self) -> int | None:
        return self.code

    def terminate(self) -> None:
        self.terminated = True
        self.code = 1

    def finish(self) -> None:
        """Пользователь закрыл окно RDP."""
        self.code = 0


class MstscCommandTests(unittest.TestCase):
    def test_client_always_connects_to_the_loopback(self) -> None:
        """Обращаться к адресу устройства нельзя: RDP идёт внутри туннеля."""
        self.assertEqual(["mstsc.exe", "/v:127.0.0.1:54321"], mstsc_command(54321))

    def test_assigned_port_is_used_as_is(self) -> None:
        self.assertEqual(["mstsc.exe", f"/v:{LOOPBACK}:1"], mstsc_command(1))
        self.assertEqual(["mstsc.exe", f"/v:{LOOPBACK}:65535"], mstsc_command(65535))

    def test_port_outside_the_range_is_rejected(self) -> None:
        for port in (0, -1, 65536):
            with self.subTest(port=port):
                with self.assertRaises(ValueError):
                    mstsc_command(port)


class TunnelStatusLabelTests(unittest.TestCase):
    def test_known_statuses_are_translated(self) -> None:
        self.assertEqual("открыт", tunnel_status_label(STATUS_ACTIVE))
        self.assertEqual("закрыт", tunnel_status_label(STATUS_CLOSED))

    def test_unknown_status_falls_back_to_raw_value(self) -> None:
        self.assertEqual("нечто", tunnel_status_label("нечто"))


class TunnelManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connections: list[FakeConnection] = []
        self.processes: list[FakeProcess] = []
        self.connect_error: Exception | None = None
        self.manager = RdpTunnelManager(
            connect=self._connect,
            launch=self._launch,
            poll_interval=0.01,
        )
        self.addCleanup(self.manager.shutdown)

    async def _connect(self, _device, _credentials) -> FakeConnection:
        if self.connect_error is not None:
            raise self.connect_error
        connection = FakeConnection()
        self.connections.append(connection)
        return connection

    def _launch(self, args: list[str]) -> FakeProcess:
        process = FakeProcess(args)
        self.processes.append(process)
        return process

    def _wait(self, condition, timeout: float = 10.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if condition():
                return True
            time.sleep(0.01)
        return False

    def test_tunnel_becomes_active_and_launches_mstsc(self) -> None:
        tunnel = self.manager.start(device(), {})

        self.assertTrue(self._wait(lambda: tunnel.status == STATUS_ACTIVE))
        self.assertTrue(self.manager.is_active(1))
        self.assertEqual(STATUS_ACTIVE, self.manager.status(1))
        self.assertEqual(54321, tunnel.local_port)
        self.assertEqual(mstsc_command(54321), self.processes[0].args)

    def test_forward_goes_to_the_loopback_of_the_device(self) -> None:
        """Проброс нужен именно на 127.0.0.1 устройства, не на его адрес."""
        self.manager.start(device(rdp_port=13389), {})

        self.assertTrue(
            self._wait(lambda: bool(self.connections) and bool(self.connections[0].forwards))
        )
        self.assertEqual(
            (LOOPBACK, 0, LOOPBACK, 13389), self.connections[0].forwards[0]
        )

    def test_closed_rdp_window_closes_the_tunnel(self) -> None:
        tunnel = self.manager.start(device(), {})
        self.assertTrue(self._wait(lambda: tunnel.status == STATUS_ACTIVE))

        self.processes[0].finish()

        self.assertTrue(self._wait(lambda: not self.manager.is_active(1)))
        self.assertEqual(STATUS_CLOSED, tunnel.status)
        self.assertTrue(self.connections[0].closed)
        self.assertTrue(self.connections[0].listener.closed)
        self.assertEqual("RDP-сессия «ПК» закрыта.", closing_message(tunnel))

    def test_closing_the_session_also_closes_the_rdp_window(self) -> None:
        tunnel = self.manager.start(device(), {})
        self.assertTrue(self._wait(lambda: tunnel.status == STATUS_ACTIVE))

        self.assertTrue(self.manager.close(1))

        self.assertTrue(self._wait(lambda: not self.manager.is_active(1)))
        self.assertTrue(self.processes[0].terminated)
        self.assertTrue(self.connections[0].closed)

    def test_second_session_for_the_same_device_is_not_opened(self) -> None:
        first = self.manager.start(device(), {})
        second = self.manager.start(device(), {})

        self.assertIs(first, second)
        self.assertTrue(self._wait(lambda: first.status == STATUS_ACTIVE))
        self.assertEqual(1, self.manager.active_count())
        self.assertEqual(1, len(self.processes))

    def test_several_devices_share_one_event_loop(self) -> None:
        self.manager.start(device(1, "ПК"), {})
        self.manager.start(device(2, "Ноутбук"), {})

        self.assertTrue(self._wait(lambda: self.manager.active_count() == 2))
        self.assertEqual(2, len(self.manager.active_tunnels()))

        self.manager.close_all(timeout=10)

        self.assertEqual(0, self.manager.active_count())

    def test_failed_connection_is_reported_and_leaves_no_session(self) -> None:
        self.connect_error = RuntimeError("fingerprint устройства изменился")

        tunnel = self.manager.start(device(), {})

        self.assertTrue(self._wait(lambda: not self.manager.is_active(1)))
        self.assertEqual(STATUS_FAILED, tunnel.status)
        self.assertEqual([], self.processes)
        self.assertIn("fingerprint", tunnel.error)
        self.assertIn("fingerprint", closing_message(tunnel))
        self.assertIn("не открыта", closing_message(tunnel))

    def test_unsaved_device_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.manager.start(Device(None, "Новый", "192.0.2.10", 22, "user"), {})

    def test_device_without_fingerprint_is_rejected(self) -> None:
        """Туннель — то же доверенное соединение: без fingerprint нельзя."""
        unpinned = device()
        unpinned.fingerprint = ""
        with self.assertRaises(ValueError):
            self.manager.start(unpinned, {})

    def test_state_of_unknown_device_is_closed(self) -> None:
        self.assertEqual(STATUS_CLOSED, self.manager.status(404))
        self.assertEqual(STATUS_CLOSED, self.manager.status(None))
        self.assertFalse(self.manager.is_active(404))
        self.assertFalse(self.manager.is_active(None))
        self.assertIsNone(self.manager.tunnel(404))
        self.assertIsNone(self.manager.tunnel(None))

    def test_closing_unknown_device_is_reported(self) -> None:
        self.assertFalse(self.manager.close(404))


if __name__ == "__main__":
    unittest.main()
