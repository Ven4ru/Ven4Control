import unittest

from ven4control.app import tailscale_candidates, terminal_command
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
