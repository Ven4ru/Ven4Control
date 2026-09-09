import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ven4control.models import Device
from ven4control.remote_control import (
    MAX_LOG_LINES,
    METRICS_COMMAND,
    MIN_LOG_LINES,
    WINDOWS_UNSUPPORTED,
    PackageResult,
    _first_health_message,
    backup_configs,
    build_backup_command,
    build_log_command,
    check_rdp,
    collect_overview,
    detect_platform,
    enable_rdp,
    install_package,
    install_ven4tools,
    is_rdp_enabled,
    list_services,
    read_logs,
    reboot_device,
    restart_service,
    update_packages,
    parse_apk_search,
    parse_apt_search,
    parse_metrics,
    parse_opkg_search,
    parse_rdp_state,
    parse_systemd_services,
    parse_winget_search,
    search_packages,
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


# Подстрока команды-пробы «какой менеджер пакетов стоит на устройстве».
# Только у неё есть `then echo apk`: сами команды поиска после фикса
# однобранчевые и слова `command -v` не содержат вовсе.
PACKAGE_MANAGER_PROBE = "then echo apk"


def openwrt_search_connection(
    manager: str,
    *extra: tuple[str, str, int],
) -> FakeConnection:
    """openwrt-соединение, где проба менеджера пакетов отвечает `manager`."""
    return openwrt_connection((PACKAGE_MANAGER_PROBE, f"{manager}\n", 0), *extra)


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


def device() -> Device:
    return Device(
        1, "ПК", "100.64.0.7", 22, "user",
        auth_type="key", key_path="C:/keys/id_ed25519",
        fingerprint="SHA256:pc",
    )


def patched_connect(connection: FakeConnection):
    """Подменяет установку SSH-соединения заранее готовым ответчиком."""

    async def connect(_device, _credentials):
        return connection

    return patch("ven4control.remote_control._connect", connect)


class WindowsGuardTests(unittest.TestCase):
    """POSIX-операции обязаны отказывать Windows понятным текстом.

    На PowerShell `systemctl`, `logread`, `apt-get` и `tar` дают мусорную
    ошибку «не является внутренней или внешней командой» — пользователь
    из неё ничего не понимает. Реализация Windows-аналогов — отдельная
    работа; отказ должен быть честным уже сейчас.
    """

    def _refuse(self, operation, forbidden: str) -> None:
        connection = windows_connection()
        with patched_connect(connection):
            with self.assertRaises(RuntimeError) as raised:
                asyncio.run(operation(connection))
        self.assertEqual(WINDOWS_UNSUPPORTED, str(raised.exception))
        self.assertNotIn(forbidden, " ".join(connection.commands))
        self.assertTrue(connection.closed)

    def test_overview_is_refused(self) -> None:
        self._refuse(lambda _c: collect_overview(device(), {}), "/proc/stat")

    def test_service_list_is_refused(self) -> None:
        self._refuse(lambda _c: list_services(device(), {}), "systemctl")

    def test_logs_are_refused(self) -> None:
        self._refuse(lambda _c: read_logs(device(), {}, "system"), "logread")

    def test_service_restart_is_refused(self) -> None:
        self._refuse(lambda _c: restart_service(device(), {}, "sshd"), "systemctl")

    def test_reboot_is_refused(self) -> None:
        self._refuse(lambda _c: reboot_device(device(), {}), "reboot")

    def test_package_update_is_refused(self) -> None:
        self._refuse(lambda _c: update_packages(device(), {}), "apt-get")

    def test_backup_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "копии"
            self._refuse(lambda _c: backup_configs(device(), {}, destination), "tar")
            # Пустая папка копий создавала бы вид, что копия делается.
            self.assertFalse(destination.exists())

    def test_backup_command_has_no_windows_variant(self) -> None:
        with self.assertRaises(RuntimeError) as raised:
            build_backup_command("windows", "/tmp/копия.tar.gz")
        self.assertEqual(WINDOWS_UNSUPPORTED, str(raised.exception))

    def test_posix_devices_are_untouched(self) -> None:
        """Отказ касается только Windows: роутер работает как раньше."""
        connection = openwrt_connection(
            ("sysupgrade", "", 0),
            ("logread", "строка журнала\n", 0),
        )
        with patched_connect(connection):
            self.assertIn("строка журнала", asyncio.run(read_logs(device(), {}, "system")))


class RdpStateParsingTests(unittest.TestCase):
    def test_zero_means_rdp_is_allowed(self) -> None:
        self.assertTrue(parse_rdp_state("RDP=0\n"))

    def test_nonzero_means_rdp_is_denied(self) -> None:
        self.assertFalse(parse_rdp_state("RDP=1\n"))

    def test_marker_is_found_among_other_output(self) -> None:
        self.assertTrue(parse_rdp_state("предупреждение\nRDP=0\nхвост\n"))

    def test_missing_or_broken_value_is_denied(self) -> None:
        self.assertFalse(parse_rdp_state(""))
        self.assertFalse(parse_rdp_state("RDP=нет\n"))
        self.assertFalse(parse_rdp_state("значения нет\n"))


class RdpCheckTests(unittest.TestCase):
    def test_enabled_windows_device_is_reported(self) -> None:
        connection = windows_connection(("fDenyTSConnections", "RDP=0\n", 0))
        with patched_connect(connection):
            status = asyncio.run(check_rdp(device(), {}))
        self.assertTrue(status.supported)
        self.assertTrue(status.enabled)
        self.assertEqual("windows", status.platform)
        self.assertTrue(connection.closed)

    def test_disabled_windows_device_is_reported(self) -> None:
        connection = windows_connection(("fDenyTSConnections", "RDP=1\n", 0))
        with patched_connect(connection):
            status = asyncio.run(check_rdp(device(), {}))
        self.assertTrue(status.supported)
        self.assertFalse(status.enabled)

    def test_missing_registry_value_is_not_enabled(self) -> None:
        connection = windows_connection(("fDenyTSConnections", "", 1))
        with patched_connect(connection):
            status = asyncio.run(check_rdp(device(), {}))
        self.assertTrue(status.supported)
        self.assertFalse(status.enabled)

    def test_router_is_unsupported_and_not_asked_about_registry(self) -> None:
        """У OpenWrt RDP не выключен, а отсутствует: реестр спрашивать нечего."""
        connection = openwrt_connection()
        with patched_connect(connection):
            status = asyncio.run(check_rdp(device(), {}))
        self.assertFalse(status.supported)
        self.assertFalse(status.enabled)
        self.assertEqual("openwrt", status.platform)
        self.assertNotIn(
            "fDenyTSConnections", " ".join(connection.commands)
        )

    def test_check_never_touches_the_network_port(self) -> None:
        """Проверка идёт по SSH: сырого подключения к 3389 быть не должно."""
        connection = windows_connection(("fDenyTSConnections", "RDP=0\n", 0))
        with patched_connect(connection):
            asyncio.run(check_rdp(device(), {}))
        self.assertNotIn("3389", " ".join(connection.commands))


class RdpEnabledTests(unittest.TestCase):
    def test_enabled_device_returns_true(self) -> None:
        connection = windows_connection(("fDenyTSConnections", "RDP=0\n", 0))
        with patched_connect(connection):
            self.assertTrue(asyncio.run(is_rdp_enabled(device(), {})))

    def test_disabled_device_returns_false(self) -> None:
        connection = windows_connection(("fDenyTSConnections", "RDP=1\n", 0))
        with patched_connect(connection):
            self.assertFalse(asyncio.run(is_rdp_enabled(device(), {})))

    def test_non_windows_device_is_rejected_with_a_clear_message(self) -> None:
        connection = openwrt_connection()
        with patched_connect(connection), self.assertRaises(RuntimeError) as error:
            asyncio.run(is_rdp_enabled(device(), {}))
        self.assertIn("только у Windows", str(error.exception))
        self.assertTrue(connection.closed)


class RdpEnableTests(unittest.TestCase):
    def test_only_incoming_connections_are_allowed(self) -> None:
        connection = windows_connection(("fDenyTSConnections", "RDP=0\n", 0))
        with patched_connect(connection):
            self.assertIsNone(asyncio.run(enable_rdp(device(), {})))
        command = connection.commands[-1]
        self.assertIn("fDenyTSConnections", command)
        self.assertIn("-Value 0", command)

    def test_firewall_is_never_opened(self) -> None:
        """Смысл модели: порт 3389 наружу не открывается никогда."""
        connection = windows_connection(("fDenyTSConnections", "RDP=0\n", 0))
        with patched_connect(connection):
            asyncio.run(enable_rdp(device(), {}))
        sent = " ".join(connection.commands)
        self.assertNotIn("NetFirewall", sent)
        self.assertNotIn("netsh", sent)
        self.assertNotIn("advfirewall", sent)

    def test_non_windows_device_is_not_changed(self) -> None:
        connection = openwrt_connection()
        with patched_connect(connection), self.assertRaises(RuntimeError):
            asyncio.run(enable_rdp(device(), {}))
        self.assertNotIn("fDenyTSConnections", " ".join(connection.commands))

    def test_failed_command_is_reported(self) -> None:
        connection = windows_connection(("fDenyTSConnections", "нет доступа", 1))
        with patched_connect(connection), self.assertRaises(RuntimeError):
            asyncio.run(enable_rdp(device(), {}))


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


# Ответ METRICS_COMMAND с реального роутера: по строке на показатель.
METRICS_ANSWER = (
    "CPU=7.4%\n"
    "MEM=118/247 MiB (47.8%)\n"
    "DISK=0.3/1.8 GiB (17%)\n"
    "UPTIME=up 3 days, 4 hours\n"
    "TEMP=42.5°C\n"
)


class MetricsCommandTests(unittest.TestCase):
    def test_every_metric_prints_on_its_own_line(self) -> None:
        """Защита от склейки: показатель, пришитый к соседнему, не разберётся."""
        lines = METRICS_COMMAND.splitlines()
        self.assertIn("printf 'UPTIME='", lines)
        self.assertIn("printf 'TEMP='", lines)
        self.assertTrue(METRICS_COMMAND.endswith("\n"))

    def test_temperature_reads_thermal_zones_and_never_fails(self) -> None:
        self.assertIn("/sys/class/thermal/thermal_zone*/temp", METRICS_COMMAND)
        self.assertIn("|| echo 'нет данных'", METRICS_COMMAND)

    def test_full_answer_is_parsed_into_every_key(self) -> None:
        self.assertEqual(
            {
                "CPU": "7.4%",
                "MEM": "118/247 MiB (47.8%)",
                "DISK": "0.3/1.8 GiB (17%)",
                "UPTIME": "up 3 days, 4 hours",
                "TEMP": "42.5°C",
            },
            parse_metrics(METRICS_ANSWER),
        )


class OverviewTests(unittest.TestCase):
    def test_temperature_reaches_the_overview(self) -> None:
        connection = openwrt_connection(("/proc/stat", METRICS_ANSWER, 0))
        with patched_connect(connection):
            overview = asyncio.run(collect_overview(device(), {}))
        self.assertEqual("42.5°C", overview.temperature)
        self.assertEqual("7.4%", overview.cpu)
        self.assertTrue(connection.closed)

    def test_device_without_sensor_keeps_the_other_metrics(self) -> None:
        without_temp = METRICS_ANSWER.replace("TEMP=42.5°C\n", "")
        connection = openwrt_connection(("/proc/stat", without_temp, 0))
        with patched_connect(connection):
            overview = asyncio.run(collect_overview(device(), {}))
        self.assertEqual("нет данных", overview.temperature)
        self.assertEqual("7.4%", overview.cpu)
        self.assertEqual("118/247 MiB (47.8%)", overview.memory)
        self.assertEqual("0.3/1.8 GiB (17%)", overview.disk)
        self.assertEqual("up 3 days, 4 hours", overview.uptime)


class ApkSearchParsingTests(unittest.TestCase):
    """Строки — реальный вывод `apk search -v -d sftp` с домашнего роутера
    (192.168.1.1, OpenWrt 25.12.0, apk-tools 3.0.2), не выдуманы."""

    def test_real_output_from_a_live_router(self) -> None:
        output = (
            "announce-1.0.1-r1 - Announce services on the network with "
            "Zeroconf/Bonjour.\n"
            "erlang-ssh-28.0.3-r1 - Erlang/OTP implementation of the Secure "
            "Shell protocol, with SSH & SFTP support.\n"
            "openssh-sftp-avahi-service-10.3_p1-r1 - This package contains "
            "the service definition for announcing SFTP support via "
            "mDNS/DNS-SD.\n"
            "openssh-sftp-client-10.3_p1-r1 - OpenSSH SFTP client.\n"
        )
        results = parse_apk_search(output)
        self.assertEqual(
            ["announce", "erlang-ssh", "openssh-sftp-avahi-service", "openssh-sftp-client"],
            [item.name for item in results],
        )
        self.assertEqual("OpenSSH SFTP client.", results[3].description)
        self.assertIsInstance(results[0], PackageResult)

    def test_version_suffix_is_stripped_from_the_name(self) -> None:
        # Живая находка: apk add с версией в имени (как в выводе search без -q)
        # падает с «no such package» — install должен получать чистое имя.
        results = parse_apk_search("vsftpd-3.0.5-r6 - FTP server.\n")
        self.assertEqual("vsftpd", results[0].name)

    def test_multi_word_version_suffix(self) -> None:
        # openssh-sftp-server-10.3_p1-r1: версия «10.3_p1» с подчёркиванием —
        # не просто «X.Y.Z», эвристика должна справляться и с этим.
        results = parse_apk_search(
            "openssh-sftp-server-10.3_p1-r1 - OpenSSH SFTP server.\n"
        )
        self.assertEqual("openssh-sftp-server", results[0].name)

    def test_blank_lines_are_skipped(self) -> None:
        self.assertEqual([], parse_apk_search("\n\n"))

    def test_no_matches_is_an_empty_list(self) -> None:
        self.assertEqual([], parse_apk_search(""))


class OpkgSearchParsingTests(unittest.TestCase):
    def test_name_version_description_format(self) -> None:
        # Формат `opkg list`: "имя - версия - описание" — не проверено на
        # реальном устройстве (нет доступного opkg-роутера), задокументировано
        # как предположение по формату, а не подтверждённый факт.
        output = "openssh-sftp-server - 9.6-r1 - OpenSSH SFTP server\n"
        results = parse_opkg_search(output)
        self.assertEqual([("openssh-sftp-server", "OpenSSH SFTP server")],
                         [(r.name, r.description) for r in results])

    def test_no_matches_is_an_empty_list(self) -> None:
        self.assertEqual([], parse_opkg_search(""))


class AptSearchParsingTests(unittest.TestCase):
    """Строки — реальный вывод `apt-cache search sftp` с живого
    Ubuntu 24.04-сервера, не выдуманы."""

    def test_real_output_from_a_live_server(self) -> None:
        output = (
            "curl - command line tool for transferring data with URL syntax\n"
            "gvfs-backends - userspace virtual filesystem - backends\n"
            "lftp - Sophisticated command-line FTP/HTTP/BitTorrent client "
            "programs\n"
        )
        results = parse_apt_search(output)
        self.assertEqual(["curl", "gvfs-backends", "lftp"], [r.name for r in results])
        # Живая находка: описание САМО содержит " - " ("virtual filesystem -
        # backends") — разбор обязан резать по ПЕРВОМУ разделителю, не по
        # первому вхождению паттерна где попало.
        self.assertEqual("userspace virtual filesystem - backends", results[1].description)

    def test_no_matches_is_an_empty_list(self) -> None:
        self.assertEqual([], parse_apt_search(""))


class WingetSearchParsingTests(unittest.TestCase):
    """Строки — реальный вывод `winget search sftp --accept-source-agreements`
    с VenchWork (Windows 11, winget v1.29.290), не выдуманы."""

    def test_real_output_from_a_live_windows_machine(self) -> None:
        output = (
            "Name                   Id                              Version       Match            Source\n"
            "---------------------------------------------------------------------------------------------\n"
            "Avash                  AdrienCros.Avash                0.10.1        Tag: sftp        winget\n"
            "Bitvise SSH Client     Bitvise.SSH.Client              9.66          Tag: sftp        winget\n"
        )
        results = parse_winget_search(output)
        # В установку идёт Id, не Name — тот же принцип, что у apk/opkg/apt:
        # PackageResult.name — точный устанавливаемый идентификатор.
        self.assertEqual(["AdrienCros.Avash", "Bitvise.SSH.Client"], [r.name for r in results])
        self.assertEqual("Avash · 0.10.1", results[0].description)

    def test_no_separator_line_is_an_empty_list(self) -> None:
        self.assertEqual([], parse_winget_search("No package found matching input criteria.\n"))

    def test_empty_output_is_an_empty_list(self) -> None:
        self.assertEqual([], parse_winget_search(""))

    def test_long_id_touching_the_version_column_is_not_merged(self) -> None:
        # Живая находка на VenchWork: `winget search curl` — Id
        # "Orange-OpenSource.Hurl" ровно упирается в границу столбца Version,
        # между ними всего ОДИН пробел (не 2+). Разбор по количеству пробелов
        # склеил бы Id и Version в одно поле ("Orange-OpenSource.Hurl 8.0.1")
        # — с таким --id winget не нашёл бы пакет при установке.
        output = (
            "Name       Id                     Version      Match     Source\n"
            "----------------------------------------------------------------\n"
            "cURL       cURL.cURL              8.21.0.6               winget\n"
            "Hurl       Orange-OpenSource.Hurl 8.0.1        Tag: curl winget\n"
        )
        results = parse_winget_search(output)
        self.assertEqual(["cURL.cURL", "Orange-OpenSource.Hurl"], [r.name for r in results])
        self.assertEqual("Hurl · 8.0.1", results[1].description)

    def test_table_without_the_match_column_is_still_parsed(self) -> None:
        # Живая находка: столбец Match winget печатает ТОЛЬКО когда совпадение
        # найдено не по имени пакета (по тегу). Обычный поиск по имени этого
        # столбца не имеет вовсе — требовать его обязательным значило показать
        # пользователю «Ничего не найдено» при непустом ответе winget.
        output = (
            "Name       Id                     Version      Source\n"
            "------------------------------------------------------\n"
            "Hurl       Orange-OpenSource.Hurl 8.0.1        winget\n"
        )
        results = parse_winget_search(output)
        self.assertEqual(["Orange-OpenSource.Hurl"], [r.name for r in results])
        self.assertEqual("Hurl · 8.0.1", results[0].description)

    def test_localised_headers_are_parsed(self) -> None:
        # Живая проверка на этой машине (Windows 11, ru-RU, winget v1.29.290):
        # заголовки таблицы ЛОКАЛИЗОВАНЫ — «Имя/ИД/Версия/Источник» вместо
        # «Name/Id/Version/Source». Ни LANG, ни LC_ALL, ни WINGET_CLI_LANGUAGE
        # английский вывод не возвращают. Поэтому границы столбцов берутся из
        # позиций заголовков, а не из их текста: порядок столбцов у winget
        # фиксирован (Name, Id, Version, [Match], Source), а имена — нет.
        output = (
            "Имя                ИД                 Версия Источник\n"
            "------------------------------------------------------\n"
            "Bitvise SSH Client Bitvise.SSH.Client 9.66   winget\n"
            "Bitvise SSH Server Bitvise.SSH.Server 9.66   winget\n"
        )
        results = parse_winget_search(output)
        self.assertEqual(
            ["Bitvise.SSH.Client", "Bitvise.SSH.Server"], [r.name for r in results]
        )
        # Пробел внутри Name («Bitvise SSH Client») — ещё одна причина резать
        # по позициям: разбор по пробелам развалил бы и имя, и всю строку.
        self.assertEqual("Bitvise SSH Client · 9.66", results[0].description)

    def test_localised_headers_with_the_match_column(self) -> None:
        # Тот же живой вывод `winget search curl` на ru-RU: Match здесь есть.
        output = (
            "Имя        ИД                     Версия       Совпадение Источник\n"
            "------------------------------------------------------------------\n"
            "cURL       cURL.cURL              8.21.0.6                winget\n"
            "Hurl       Orange-OpenSource.Hurl 8.0.1        Tag: curl  winget\n"
        )
        results = parse_winget_search(output)
        self.assertEqual(["cURL.cURL", "Orange-OpenSource.Hurl"], [r.name for r in results])
        self.assertEqual("Hurl · 8.0.1", results[1].description)

    def test_header_without_three_columns_is_an_empty_list(self) -> None:
        # Без Id и Version таблицу разбирать нечем — пустой список честнее
        # выдуманных имён пакетов.
        self.assertEqual([], parse_winget_search("Name\n----\nHurl\n"))


class SearchPackagesTests(unittest.TestCase):
    def test_apk_device_returns_parsed_results(self) -> None:
        # Маркер "apk search" — подстрока реальной команды, которую строит
        # search_packages для платформы openwrt: FakeConnection подставляет
        # ответ по вхождению маркера в отправленную строку.
        connection = openwrt_search_connection(
            "apk",
            ("apk search", "openssh-sftp-server-10.3_p1-r1 - OpenSSH SFTP server.\n", 0),
        )
        with patched_connect(connection):
            results = asyncio.run(search_packages(device(), {}, "sftp"))
        self.assertEqual("openssh-sftp-server", results[0].name)
        self.assertTrue(connection.closed)

    def test_opkg_device_is_parsed_by_the_opkg_parser(self) -> None:
        # Живой формат `opkg list` — "имя - версия - описание", три поля.
        # apk-парсер на этой же строке отрезал бы от имени «версию» и положил
        # в описание "9.6-r1": в install ушло бы несуществующее имя пакета.
        # Менеджер определяется пробой ДО поиска, а не угадывается по
        # платформе — на openwrt возможны и apk, и opkg.
        connection = openwrt_search_connection(
            "opkg",
            ("opkg list", "openssh-sftp-server - 9.6-r1 - OpenSSH SFTP server\n", 0),
        )
        with patched_connect(connection):
            results = asyncio.run(search_packages(device(), {}, "sftp"))
        self.assertEqual(
            [("openssh-sftp-server", "OpenSSH SFTP server")],
            [(r.name, r.description) for r in results],
        )

    def test_command_is_single_branch_for_the_manager_that_is_there(self) -> None:
        """После пробы команда поиска однобранчевая — без if/elif вовсе."""
        opkg = openwrt_search_connection("opkg", ("opkg list", "", 0))
        with patched_connect(opkg):
            asyncio.run(search_packages(device(), {}, "sftp"))
        executed = opkg.commands[-1]
        self.assertIn("opkg list", executed)
        self.assertNotIn("apk search", executed)
        self.assertNotIn("elif", executed)

        apk = openwrt_search_connection("apk", ("apk search", "", 0))
        with patched_connect(apk):
            asyncio.run(search_packages(device(), {}, "sftp"))
        executed = apk.commands[-1]
        self.assertIn("apk search", executed)
        self.assertNotIn("opkg list", executed)
        self.assertNotIn("elif", executed)

    def test_missing_package_manager_is_reported_before_the_search(self) -> None:
        connection = openwrt_search_connection("none")
        with patched_connect(connection):
            with self.assertRaises(RuntimeError) as raised:
                asyncio.run(search_packages(device(), {}, "sftp"))
        self.assertIn("Менеджер пакетов не найден", str(raised.exception))
        # Проба — последняя отправленная команда: до поиска дело не дошло.
        self.assertIn(PACKAGE_MANAGER_PROBE, connection.commands[-1])
        self.assertTrue(connection.closed)

    def test_search_term_is_shell_escaped(self) -> None:
        """Защита от command injection: термин уходит одним словом в кавычках."""
        connection = openwrt_search_connection("apk", ("apk search", "", 0))
        with patched_connect(connection):
            asyncio.run(search_packages(device(), {}, "sftp; rm -rf /"))
        executed = connection.commands[-1]
        self.assertIn("apk search -v -d 'sftp; rm -rf /'", executed)
        self.assertNotIn("apk search -v -d sftp;", executed)

        opkg = openwrt_search_connection("opkg", ("opkg list", "", 0))
        with patched_connect(opkg):
            asyncio.run(search_packages(device(), {}, "sftp; rm -rf /"))
        self.assertIn("grep -i 'sftp; rm -rf /'", opkg.commands[-1])
        self.assertNotIn("grep -i sftp;", opkg.commands[-1])

    def test_opkg_limit_is_applied_on_the_device(self) -> None:
        # Обрезка через head обязана остаться на устройстве и в opkg-ветке:
        # каталог там тоже тысячи строк, гонять их по SSH незачем.
        connection = openwrt_search_connection(
            "opkg", ("opkg list", "pkg-one - 1.0 - one\npkg-two - 2.0 - two\n", 0)
        )
        with patched_connect(connection):
            results = asyncio.run(search_packages(device(), {}, "", limit=200))
        self.assertEqual(["pkg-one", "pkg-two"], [r.name for r in results])
        self.assertTrue(connection.commands[-1].rstrip().endswith("| head -n 200"))

    def test_empty_term_with_limit_lists_the_whole_catalog_capped(self) -> None:
        # Живая находка: apk/apt при пустом паттерне сами перечисляют весь
        # каталог (apk называет это "no pattern given, list all packages") —
        # 10983/85576 строк на реальных устройствах. limit обрезает вывод
        # НА УСТРОЙСТВЕ через head, чтобы не гонять по SSH то, что всё равно
        # будет отброшено.
        connection = openwrt_search_connection(
            "apk", ("apk search", "pkg-one - one\npkg-two - two\n", 0)
        )
        with patched_connect(connection):
            results = asyncio.run(search_packages(device(), {}, "", limit=200))
        self.assertEqual(2, len(results))
        executed = connection.commands[-1]
        self.assertIn("apk search -v -d ''", executed)
        self.assertTrue(executed.rstrip().endswith("| head -n 200"))

    def test_windows_empty_term_with_limit_skips_the_network(self) -> None:
        # Стартовый каталог без фильтра — приём, проверенный только для
        # apk/apt; для winget пустой запрос не проверялся и не тот же
        # случай, поэтому запрос на устройство вообще не уходит.
        connection = windows_connection()
        with patched_connect(connection):
            results = asyncio.run(search_packages(device(), {}, "", limit=200))
        self.assertEqual([], results)
        self.assertNotIn("winget search", " ".join(connection.commands))


class InstallPackageTests(unittest.TestCase):
    def test_package_name_is_shell_escaped(self) -> None:
        # install_package использует _run(..., check=True) — ответ обязан быть
        # с кодом 0, иначе _run поднимет RuntimeError раньше проверки.
        connection = openwrt_connection(("apk add", "OK", 0))
        with patched_connect(connection):
            asyncio.run(install_package(device(), {}, "pkg`whoami`"))
        executed = connection.commands[-1]
        self.assertIn("apk add 'pkg`whoami`'", executed)
        self.assertNotIn("apk add pkg`whoami`", executed)


class SearchPackagesWindowsTests(unittest.TestCase):
    def test_windows_device_uses_winget(self) -> None:
        connection = windows_connection(("winget search", (
            "Name    Id            Version  Match       Source\n"
            "----------------------------------------------\n"
            "Avash   Vendor.Avash  1.0      Tag: sftp   winget\n"
        ), 0))
        with patched_connect(connection):
            results = asyncio.run(search_packages(device(), {}, "sftp"))
        self.assertEqual("Vendor.Avash", results[0].name)

    def test_search_term_is_powershell_escaped(self) -> None:
        connection = windows_connection(("winget search", "", 0))
        with patched_connect(connection):
            asyncio.run(search_packages(device(), {}, "sftp'; Remove-Item C:\\"))
        executed = connection.commands[-1]
        # PowerShell-экранирование: одинарная кавычка внутри строки
        # удваивается, не убегает обратным слэшем (POSIX-приём здесь неверен).
        self.assertIn("'sftp''; Remove-Item C:\\'", executed)


class InstallVen4ToolsTests(unittest.TestCase):
    def test_non_windows_device_is_rejected(self) -> None:
        connection = openwrt_connection()
        with patched_connect(connection):
            with self.assertRaises(RuntimeError) as raised:
                asyncio.run(install_ven4tools(device(), {}))
        self.assertIn("только на Windows", str(raised.exception))

    def test_windows_device_runs_the_download_command(self) -> None:
        connection = windows_connection(
            ("Invoke-RestMethod", "Ven4Tools v5.1.1 установлен в C:\\Ven4Tools", 0)
        )
        with patched_connect(connection):
            result = asyncio.run(install_ven4tools(device(), {}))
        self.assertIn("установлен", result)
        executed = connection.commands[-1]
        self.assertIn("SilentlyContinue", executed)

    def _install_command(self) -> str:
        connection = windows_connection(
            ("Invoke-RestMethod", "Ven4Tools v5.1.1 установлен", 0)
        )
        with patched_connect(connection):
            asyncio.run(install_ven4tools(device(), {}))
        return connection.commands[-1]

    def test_archive_hash_is_compared_with_the_release_digest(self) -> None:
        """Ответ GitHub API уже содержит digest: отдельный запрос не нужен."""
        command = self._install_command()
        self.assertIn("Get-FileHash", command)
        self.assertIn("$asset.digest", command)

    def test_nothing_is_unpacked_until_the_hash_matches(self) -> None:
        command = self._install_command()
        check = command.index("Get-FileHash")
        unpack = command.index("Expand-Archive")
        self.assertLess(check, unpack)
        # Между проверкой и распаковкой обязаны стоять удаление архива и
        # обрыв установки, иначе проверка ничего не защищает.
        between = command[check:unpack]
        self.assertIn("throw", between)
        self.assertIn("Remove-Item", between)

    def test_comparison_ignores_the_letter_case(self) -> None:
        command = self._install_command()
        self.assertIn("ToLower", command)

    def test_release_without_a_digest_is_refused(self) -> None:
        """Нечего сверять — установка не начинается, а не идёт вслепую."""
        command = self._install_command()
        prefix = command[: command.index("Get-FileHash")]
        self.assertIn("-not $expected", prefix)
        self.assertIn("throw", prefix[prefix.index("$asset.digest"):])

    def test_dropped_channel_after_real_success_is_confirmed_by_verification(self) -> None:
        # Живая находка на VenchWork: основная команда рвётся с
        # exit_status=None и пустым выводом уже ПОСЛЕ того, как установка
        # на устройстве реально завершилась. "Expand-Archive" — маркер
        # основной команды, "Get-Content" — отдельной команды проверки.
        connection = windows_connection(
            ("Expand-Archive", "", None),
            ("Get-Content", "v5.1.2", 0),
        )
        with patched_connect(connection):
            result = asyncio.run(install_ven4tools(device(), {}))
        self.assertIn("v5.1.2", result)
        self.assertIn("подтверждено проверкой", result)

    def test_dropped_channel_without_matching_marker_raises(self) -> None:
        # Проверка не подтвердила совпадение версии (маркер не совпал или
        # отсутствует) — исходная ошибка должна дойти до вызывающего кода,
        # не выдаваться молча за успех.
        connection = windows_connection(
            ("Expand-Archive", "", None),
            ("Get-Content", "", 0),
        )
        with patched_connect(connection):
            with self.assertRaises(RuntimeError) as raised:
                asyncio.run(install_ven4tools(device(), {}))
        self.assertIn("кодом None", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
