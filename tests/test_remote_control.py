import asyncio
import unittest
from types import SimpleNamespace

from ven4control.remote_control import (
    MAX_LOG_LINES,
    MIN_LOG_LINES,
    _first_health_message,
    build_backup_command,
    build_log_command,
    detect_platform,
    parse_systemd_services,
)


# Ответ PowerShell-пробы на настоящей Windows-машине.
WINDOWS_ANSWER = "windows\nMicrosoft Windows 11 Pro\n"

# POSIX-оболочка не разбирает текст пробы и завершается ненулевым кодом.
SHELL_SYNTAX_ERROR = ("", 2)


class FakeConnection:
    """SSH-соединение для тестов: отвечает по подстроке в команде.

    Позволяет проверять команды и разбор ответов без настоящего SSH.
    """

    def __init__(self, replies: list[tuple[str, str, int]] | None = None) -> None:
        # Каждая запись: подстрока команды, stdout, код возврата.
        self.replies = list(replies or [])
        self.commands: list[str] = []
        self.closed = False

    async def run(self, command: str, check: bool = False, timeout: int = 60):
        self.commands.append(command)
        for marker, stdout, status in self.replies:
            if marker in command:
                return SimpleNamespace(stdout=stdout, stderr="", exit_status=status)
        return SimpleNamespace(stdout="", stderr="команда не найдена", exit_status=127)

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


def windows_connection(*extra: tuple[str, str, int]) -> FakeConnection:
    return FakeConnection([("$PSVersionTable", WINDOWS_ANSWER, 0), *extra])


def openwrt_connection(*extra: tuple[str, str, int]) -> FakeConnection:
    return FakeConnection(
        [
            ("$PSVersionTable", *SHELL_SYNTAX_ERROR),
            ("openwrt_release", "openwrt\nOpenWrt 23.05.5\n", 0),
            *extra,
        ]
    )


class DetectPlatformTests(unittest.TestCase):
    def test_powershell_device_is_detected_as_windows(self) -> None:
        connection = windows_connection()
        platform, description = asyncio.run(detect_platform(connection))
        self.assertEqual("windows", platform)
        self.assertEqual("Microsoft Windows 11 Pro", description)

    def test_powershell_probe_goes_first_and_posix_stays_a_fallback(self) -> None:
        """POSIX-команда в PowerShell не выполняется, поэтому проба первая."""
        connection = openwrt_connection()
        platform, description = asyncio.run(detect_platform(connection))
        self.assertEqual("openwrt", platform)
        self.assertEqual("OpenWrt 23.05.5", description)
        self.assertIn("$PSVersionTable", connection.commands[0])
        self.assertIn("openwrt_release", connection.commands[1])

    def test_windows_device_is_not_asked_posix_questions(self) -> None:
        connection = windows_connection()
        asyncio.run(detect_platform(connection))
        self.assertEqual(1, len(connection.commands))
        self.assertNotIn("openwrt_release", connection.commands[0])

    def test_foreign_answer_with_zero_code_is_ignored(self) -> None:
        """Оболочка могла проглотить текст пробы и вернуть нулевой код."""
        connection = FakeConnection(
            [
                ("$PSVersionTable", "мусор\n", 0),
                ("openwrt_release", "linux\nUbuntu 24.04\n", 0),
            ]
        )
        self.assertEqual(("linux", "Ubuntu 24.04"), asyncio.run(detect_platform(connection)))

    def test_windows_without_description_keeps_platform(self) -> None:
        connection = FakeConnection([("$PSVersionTable", "windows\n", 0)])
        self.assertEqual(("windows", "Windows"), asyncio.run(detect_platform(connection)))

    def test_empty_posix_answer_is_reported(self) -> None:
        connection = FakeConnection(
            [("$PSVersionTable", *SHELL_SYNTAX_ERROR), ("openwrt_release", "", 0)]
        )
        with self.assertRaises(RuntimeError):
            asyncio.run(detect_platform(connection))


class LogCommandTests(unittest.TestCase):
    def test_unknown_source_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            build_log_command("secrets")

    def test_systemd_fallback_is_reachable_for_filtered_sources(self) -> None:
        """Раньше запасной journalctl стоял после конвейера и не выполнялся."""
        command = build_log_command("tailscale", 50)
        self.assertIn("command -v logread", command)
        self.assertIn("journalctl -u tailscaled -n 50 --no-pager", command)
        self.assertNotIn("| tail -n 50 || journalctl", command)

    def test_every_source_has_both_variants(self) -> None:
        for source, unit in (
            ("system", ""),
            ("tailscale", "tailscaled"),
            ("adguard", "AdGuardHome"),
            ("xray", "xray"),
        ):
            with self.subTest(source=source):
                command = build_log_command(source, 200)
                self.assertIn("logread", command)
                self.assertIn("journalctl", command)
                if unit:
                    self.assertIn(f"-u {unit}", command)

    def test_line_count_is_clamped(self) -> None:
        self.assertIn(f"-n {MIN_LOG_LINES}", build_log_command("system", 1))
        self.assertIn(f"-n {MAX_LOG_LINES}", build_log_command("system", 99999))


class SystemdParsingTests(unittest.TestCase):
    def test_columns_are_read_for_normal_units(self) -> None:
        output = (
            "ssh.service loaded active running OpenBSD Secure Shell server\n"
            "cron.service loaded active running Regular background program\n"
        )
        services = parse_systemd_services(output)
        self.assertEqual(["ssh.service", "cron.service"], [s.name for s in services])
        self.assertEqual("running", services[0].state)
        self.assertEqual("OpenBSD Secure Shell server", services[0].details)

    def test_marker_of_failed_unit_does_not_shift_columns(self) -> None:
        output = "● xray.service loaded failed failed Xray Service\n"
        services = parse_systemd_services(output)
        self.assertEqual(1, len(services))
        self.assertEqual("xray.service", services[0].name)
        self.assertEqual("failed", services[0].state)
        self.assertEqual("Xray Service", services[0].details)

    def test_empty_and_foreign_lines_are_skipped(self) -> None:
        output = "\n   \n8 loaded units listed.\nssh.service loaded active running SSH\n"
        self.assertEqual(["ssh.service"], [s.name for s in parse_systemd_services(output)])


class BackupCommandTests(unittest.TestCase):
    def test_openwrt_uses_sysupgrade(self) -> None:
        self.assertEqual(
            "sysupgrade -b /tmp/backup.tar.gz",
            build_backup_command("openwrt", "/tmp/backup.tar.gz"),
        )

    def test_missing_path_does_not_abort_collection(self) -> None:
        """`set -e` вместе с `[ -e ... ] &&` обрывал сбор на первом же пропуске."""
        command = build_backup_command("linux", "/tmp/backup.tar.gz")
        self.assertNotIn("set -eu", command)
        self.assertIn("if [ -e \"$p\" ]; then", command)
        self.assertIn("tar -czf /tmp/backup.tar.gz", command)

    def test_empty_selection_reports_reason(self) -> None:
        command = build_backup_command("linux", "/tmp/backup.tar.gz")
        self.assertIn("нет конфигов для копирования", command)
        self.assertIn("exit 1", command)


class TailscaleHealthTests(unittest.TestCase):
    def test_list_of_strings(self) -> None:
        self.assertEqual("нет доступа", _first_health_message(["нет доступа"]))

    def test_dictionary_of_warnings(self) -> None:
        self.assertEqual(
            "Обновите клиент",
            _first_health_message({"update": {"Title": "Обновите клиент"}}),
        )

    def test_missing_health_is_empty(self) -> None:
        self.assertEqual("", _first_health_message(None))
        self.assertEqual("", _first_health_message([]))


if __name__ == "__main__":
    unittest.main()
