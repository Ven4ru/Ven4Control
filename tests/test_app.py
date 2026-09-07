import unittest

from ven4control.app import (
    apply_rdp_result,
    rdp_check_label,
    rdp_command,
    tailscale_candidates,
    terminal_command,
)
from ven4control.models import Device


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


class RdpCommandTests(unittest.TestCase):
    def test_default_port_is_passed_explicitly(self) -> None:
        device = Device(1, "ПК", "100.64.0.7", 22, "user")
        self.assertEqual(["mstsc.exe", "/v:100.64.0.7:3389"], rdp_command(device))

    def test_custom_port_is_used(self) -> None:
        device = Device(1, "ПК", "198.51.100.10", 2222, "user", rdp_port=13389)
        self.assertEqual(["mstsc.exe", "/v:198.51.100.10:13389"], rdp_command(device))

    def test_ipv6_host_is_wrapped_in_brackets(self) -> None:
        device = Device(1, "ПК", "2001:db8::5", 22, "user")
        self.assertEqual(["mstsc.exe", "/v:[2001:db8::5]:3389"], rdp_command(device))

    def test_already_bracketed_host_is_not_wrapped_twice(self) -> None:
        device = Device(1, "ПК", "[2001:db8::5]", 22, "user")
        self.assertEqual(["mstsc.exe", "/v:[2001:db8::5]:3389"], rdp_command(device))

    def test_ssh_port_does_not_affect_rdp_command(self) -> None:
        device = Device(1, "ПК", "host", 2222, "user")
        self.assertNotIn("/v:host:2222", rdp_command(device))


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
        self.assertEqual("RDP не отвечает — проверить снова", rdp_check_label(device))
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


if __name__ == "__main__":
    unittest.main()
