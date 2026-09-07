import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import asyncssh

from ven4control.models import Device


SERVICE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_.@-]+$")

MIN_LOG_LINES = 20
MAX_LOG_LINES = 1000

# Источник журнала: фильтр для logread и юнит systemd.
# Пустой фильтр означает журнал целиком.
LOG_SOURCES: dict[str, tuple[str, str]] = {
    "system": ("", ""),
    "tailscale": ("tailscale", "tailscaled"),
    "adguard": ("adguard", "AdGuardHome"),
    "xray": ("xray", "xray"),
}


# Показатели системы одной командой: значения возвращаются строками вида
# «КЛЮЧ=значение». Команда общая для разового обзора и фонового снапшота,
# чтобы CPU и память считались одинаково в обоих режимах.
METRICS_COMMAND = r"""
read t1 i1 <<EOF
$(awk 'NR==1 {t=0; for(i=2;i<=NF;i++) t+=$i; print t, $5+$6}' /proc/stat)
EOF
sleep 1
read t2 i2 <<EOF
$(awk 'NR==1 {t=0; for(i=2;i<=NF;i++) t+=$i; print t, $5+$6}' /proc/stat)
EOF
awk -v t1="$t1" -v i1="$i1" -v t2="$t2" -v i2="$i2" \
  'BEGIN {dt=t2-t1; printf "CPU=%.1f%%\n", dt ? 100*(dt-(i2-i1))/dt : 0}'
awk '/MemTotal:/ {t=$2} /MemAvailable:/ {a=$2} END {
  if (!a) a=0; printf "MEM=%d/%d MiB (%.1f%%)\n", (t-a)/1024, t/1024, t ? 100*(t-a)/t : 0
}' /proc/meminfo
df -Pk / | awk 'NR==2 {printf "DISK=%.1f/%.1f GiB (%s)\n", $3/1048576, $2/1048576, $5}'
printf 'UPTIME='
uptime -p 2>/dev/null || awk '{printf "%.1f hours\n", $1/3600}' /proc/uptime
"""


# Проба Windows: переменная $PSVersionTable существует только в PowerShell,
# который служит оболочкой по умолчанию во встроенном OpenSSH Server Windows.
# В POSIX-оболочке этот текст не разбирается и завершается ненулевым кодом,
# поэтому проба безопасна для роутеров и Linux-серверов.
WINDOWS_PLATFORM_COMMAND = (
    "if ($null -eq $PSVersionTable) { exit 1 }; "
    "Write-Output 'windows'; "
    "$c = (Get-CimInstance Win32_OperatingSystem -ErrorAction SilentlyContinue).Caption; "
    "if (-not $c) { $c = 'Windows ' + [System.Environment]::OSVersion.Version }; "
    "Write-Output $c"
)

# Проба POSIX: разделяет OpenWrt и остальной Linux.
POSIX_PLATFORM_COMMAND = (
    "if [ -f /etc/openwrt_release ]; then "
    ". /etc/openwrt_release; echo openwrt; echo \"${DISTRIB_DESCRIPTION:-OpenWrt}\"; "
    "elif [ -f /etc/os-release ]; then "
    ". /etc/os-release; echo linux; echo \"${PRETTY_NAME:-Linux}\"; "
    "else echo linux; uname -sr; fi"
)


class FingerprintError(RuntimeError):
    """SSH fingerprint не сохранён или не совпадает с записанным ранее.

    Отдельный класс нужен фоновому стримингу: такую ошибку бессмысленно
    переживать переподключением, сессию нужно останавливать сразу.
    """


def parse_metrics(output: str) -> dict[str, str]:
    """Разбирает вывод METRICS_COMMAND в словарь «КЛЮЧ → значение»."""
    values: dict[str, str] = {}
    for line in output.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


@dataclass(slots=True)
class SystemOverview:
    platform: str
    description: str
    uptime: str
    cpu: str
    memory: str
    disk: str
    tailscale: str
    wireguard: str


@dataclass(slots=True)
class ServiceInfo:
    name: str
    state: str
    details: str = ""


def _connection_options(
    device: Device,
    credentials: dict[str, str],
) -> dict[str, object]:
    options: dict[str, object] = {
        "host": device.host,
        "port": device.port,
        "username": device.username,
        "known_hosts": None,
        "login_timeout": 15,
    }
    password = credentials.get("password", "")
    passphrase = credentials.get("passphrase", "")
    if device.auth_type == "key" and device.key_path:
        options["client_keys"] = [device.key_path]
        if passphrase:
            options["passphrase"] = passphrase
    elif password:
        options["password"] = password
    return options


async def _connect(
    device: Device,
    credentials: dict[str, str],
) -> asyncssh.SSHClientConnection:
    if not device.fingerprint:
        raise FingerprintError(
            "Для управления требуется сохранённый SSH fingerprint. "
            "Переустановите ключ Ven4Control для этого устройства."
        )
    connection = await asyncssh.connect(**_connection_options(device, credentials))
    actual = connection.get_server_host_key().get_fingerprint("sha256")
    if actual != device.fingerprint:
        connection.close()
        await connection.wait_closed()
        raise FingerprintError(
            "SSH fingerprint устройства изменился. Управление заблокировано."
        )
    return connection


async def _run(
    connection: asyncssh.SSHClientConnection,
    command: str,
    *,
    timeout: int = 60,
    check: bool = False,
) -> asyncssh.SSHCompletedProcess:
    try:
        result = await connection.run(command, check=False, timeout=timeout)
    except TimeoutError as error:
        raise RuntimeError(
            f"Устройство не ответило за {timeout} с, команда прервана."
        ) from error
    if check and result.exit_status != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(detail or f"Команда завершилась с кодом {result.exit_status}")
    return result


async def _detect_windows(
    connection: asyncssh.SSHClientConnection,
) -> tuple[str, str] | None:
    """Пробует опознать Windows. None — устройство отвечает не PowerShell."""
    try:
        result = await _run(connection, WINDOWS_PLATFORM_COMMAND, timeout=20)
    except (RuntimeError, OSError, asyncssh.Error):
        # Проба ничего не ломает: неудача просто означает «не Windows».
        return None
    if result.exit_status != 0:
        return None
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    # Оболочка могла проглотить текст и вернуть нулевой код: доверяем только
    # ответу нужного вида.
    if not lines or lines[0] != "windows":
        return None
    return "windows", lines[1] if len(lines) > 1 else "Windows"


async def detect_platform(
    connection: asyncssh.SSHClientConnection,
) -> tuple[str, str]:
    """Определяет платформу устройства: windows, openwrt или linux.

    Порядок проб важен. Windows-машины подключаются через встроенный OpenSSH
    Server, где оболочка по умолчанию — PowerShell, и POSIX-команда там не
    выполняется как задумано. Поэтому сначала проверяется PowerShell, а при
    неудаче остаётся прежняя POSIX-проба для OpenWrt и Linux.
    """
    windows = await _detect_windows(connection)
    if windows is not None:
        return windows
    result = await _run(connection, POSIX_PLATFORM_COMMAND, check=True)
    lines = result.stdout.strip().splitlines()
    if not lines:
        raise RuntimeError("Не удалось определить систему устройства.")
    return lines[0], lines[1] if len(lines) > 1 else lines[0]


async def collect_overview(
    device: Device,
    credentials: dict[str, str],
) -> SystemOverview:
    connection = await _connect(device, credentials)
    try:
        platform, description = await detect_platform(connection)
        metrics = await _run(
            connection,
            METRICS_COMMAND,
            timeout=15,
            check=True,
        )
        values = parse_metrics(metrics.stdout)

        tailscale = await _tailscale_status(connection)
        wireguard = await _wireguard_status(connection)
        return SystemOverview(
            platform=platform,
            description=description,
            uptime=values.get("UPTIME", "нет данных"),
            cpu=values.get("CPU", "нет данных"),
            memory=values.get("MEM", "нет данных"),
            disk=values.get("DISK", "нет данных"),
            tailscale=tailscale,
            wireguard=wireguard,
        )
    finally:
        connection.close()
        await connection.wait_closed()


def _first_health_message(health: object) -> str:
    """Первое предупреждение Tailscale: список строк или словарь записей."""
    if isinstance(health, dict):
        entries: list[object] = list(health.values())
    elif isinstance(health, list):
        entries = list(health)
    else:
        entries = []
    for entry in entries:
        if isinstance(entry, dict):
            for field in ("Title", "Text", "Message", "text"):
                value = entry.get(field)
                if value:
                    return str(value)
            continue
        if entry:
            return str(entry)
    return ""


async def _tailscale_status(connection: asyncssh.SSHClientConnection) -> str:
    result = await _run(
        connection,
        "command -v tailscale >/dev/null 2>&1 && tailscale status --json",
        timeout=20,
    )
    if result.exit_status != 0:
        return "не установлен или не запущен"
    try:
        data = json.loads(result.stdout)
        backend = str(data.get("BackendState", "неизвестно"))
        online = bool((data.get("Self") or {}).get("Online"))
        suffix = "онлайн" if online else "офлайн"
        warning = _first_health_message(data.get("Health"))
        if warning:
            return f"{backend}, {suffix}; {warning}"
        return f"{backend}, {suffix}"
    except (TypeError, ValueError):
        return result.stdout.strip().splitlines()[0] if result.stdout.strip() else "нет данных"


async def _wireguard_status(connection: asyncssh.SSHClientConnection) -> str:
    result = await _run(
        connection,
        "if command -v wg >/dev/null 2>&1; then "
        "i=$(wg show interfaces 2>/dev/null); "
        "[ -n \"$i\" ] && echo \"$i\" || echo 'интерфейсов нет'; "
        "else echo 'не установлен'; fi",
    )
    return result.stdout.strip() or "нет данных"


def parse_systemd_services(output: str) -> list[ServiceInfo]:
    """Разбирает вывод `systemctl list-units`.

    У проблемных юнитов systemd печатает маркер в начале строки, из-за
    которого столбцы сдвигались и в списке появлялся сервис с именем «●».
    """
    services: list[ServiceInfo] = []
    for raw in output.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line[0] in "●*x→":
            line = line[1:].strip()
        fields = line.split(None, 4)
        if len(fields) < 4 or not fields[0].endswith(".service"):
            continue
        services.append(
            ServiceInfo(fields[0], fields[3], fields[4] if len(fields) > 4 else "")
        )
    return services


async def list_services(
    device: Device,
    credentials: dict[str, str],
) -> tuple[str, list[ServiceInfo]]:
    connection = await _connect(device, credentials)
    try:
        platform, _ = await detect_platform(connection)
        if platform == "openwrt":
            result = await _run(connection, "ubus call service list", check=True)
            data = json.loads(result.stdout)
            services: list[ServiceInfo] = []
            for name, service in sorted(data.items()):
                instances = service.get("instances") or {}
                running = [
                    item for item in instances.values() if item.get("running") is True
                ]
                state = "работает" if running else "остановлен"
                pids = [str(item["pid"]) for item in running if "pid" in item]
                services.append(ServiceInfo(name, state, f"PID: {', '.join(pids)}" if pids else ""))
            return platform, services

        result = await _run(
            connection,
            "systemctl list-units --type=service --all --no-pager --no-legend "
            "--plain 2>/dev/null",
            check=True,
        )
        return platform, parse_systemd_services(result.stdout)
    finally:
        connection.close()
        await connection.wait_closed()


def build_log_command(source: str, lines: int = 200) -> str:
    """Собирает команду чтения журнала.

    Выбор между logread и journalctl делается заранее: у конвейера
    `logread | grep | tail` код возврата всегда нулевой, поэтому запасной
    вариант через `||` на системах с systemd не срабатывал.
    """
    if source not in LOG_SOURCES:
        raise ValueError("Неизвестный источник журнала")
    filter_word, unit = LOG_SOURCES[source]
    count = max(MIN_LOG_LINES, min(int(lines), MAX_LOG_LINES))
    if filter_word:
        openwrt = f"logread | grep -i {filter_word} | tail -n {count}"
        systemd = f"journalctl -u {unit} -n {count} --no-pager"
    else:
        openwrt = f"logread | tail -n {count}"
        systemd = f"journalctl -n {count} --no-pager"
    return (
        f"if command -v logread >/dev/null 2>&1; then {openwrt}; "
        f"elif command -v journalctl >/dev/null 2>&1; then {systemd}; "
        "else echo 'Журнал недоступен: нет logread и journalctl' >&2; exit 127; fi"
    )


async def read_logs(
    device: Device,
    credentials: dict[str, str],
    source: str,
    lines: int = 200,
) -> str:
    command = build_log_command(source, lines)
    connection = await _connect(device, credentials)
    try:
        result = await _run(connection, command, timeout=30, check=True)
        output = result.stdout.strip()
        return output or "Записей не найдено."
    finally:
        connection.close()
        await connection.wait_closed()


async def restart_service(
    device: Device,
    credentials: dict[str, str],
    service: str,
) -> str:
    if not SERVICE_NAME_PATTERN.fullmatch(service):
        raise ValueError("Недопустимое имя сервиса")
    connection = await _connect(device, credentials)
    try:
        platform, _ = await detect_platform(connection)
        if platform == "openwrt":
            command = (
                f"if [ -x /etc/init.d/{service} ]; then /etc/init.d/{service} restart; "
                f"else echo 'Нет скрипта /etc/init.d/{service}' >&2; exit 127; fi"
            )
        else:
            command = f"sudo -n systemctl restart {service}"
        await _run(connection, command, timeout=90, check=True)
        return f"Сервис {service} перезапущен."
    finally:
        connection.close()
        await connection.wait_closed()


async def reboot_device(
    device: Device,
    credentials: dict[str, str],
) -> str:
    connection = await _connect(device, credentials)
    try:
        platform, _ = await detect_platform(connection)
        command = (
            "nohup sh -c 'sleep 2; reboot' >/dev/null 2>&1 &"
            if platform == "openwrt"
            else "nohup sh -c 'sleep 2; sudo -n reboot' >/dev/null 2>&1 &"
        )
        await _run(connection, command, timeout=10, check=True)
        return "Команда перезагрузки отправлена."
    finally:
        connection.close()
        await connection.wait_closed()


async def update_packages(
    device: Device,
    credentials: dict[str, str],
) -> str:
    connection = await _connect(device, credentials)
    try:
        platform, _ = await detect_platform(connection)
        if platform == "openwrt":
            command = (
                "if command -v apk >/dev/null 2>&1; then apk update && apk upgrade; "
                "elif command -v opkg >/dev/null 2>&1; then "
                "opkg update && p=$(opkg list-upgradable | awk '{print $1}'); "
                "[ -z \"$p\" ] || opkg upgrade $p; "
                "else echo 'Менеджер пакетов не найден' >&2; exit 127; fi"
            )
        else:
            # DEBIAN_FRONTEND нужно передавать внутрь sudo: переменная перед
            # самим sudo отбрасывается вместе с остальным окружением.
            command = (
                "sudo -n apt-get update && "
                "sudo -n env DEBIAN_FRONTEND=noninteractive apt-get upgrade -y"
            )
        result = await _run(connection, command, timeout=1800, check=True)
        return result.stdout.strip() or "Обновление пакетов завершено."
    finally:
        connection.close()
        await connection.wait_closed()


async def check_openwrt_upgrade(
    device: Device,
    credentials: dict[str, str],
) -> str:
    connection = await _connect(device, credentials)
    try:
        platform, _ = await detect_platform(connection)
        if platform != "openwrt":
            raise RuntimeError("Обновление прошивки доступно только для OpenWrt.")
        result = await _run(
            connection,
            "if command -v owut >/dev/null 2>&1; then owut check; "
            "elif command -v auc >/dev/null 2>&1; then auc -c; "
            "else echo 'Установите owut или auc для безопасного обновления OpenWrt.' >&2; "
            "exit 127; fi",
            timeout=180,
            check=True,
        )
        return result.stdout.strip() or "Проверка завершена."
    finally:
        connection.close()
        await connection.wait_closed()


async def install_openwrt_upgrade(
    device: Device,
    credentials: dict[str, str],
) -> str:
    connection = await _connect(device, credentials)
    try:
        platform, _ = await detect_platform(connection)
        if platform != "openwrt":
            raise RuntimeError("Обновление прошивки доступно только для OpenWrt.")
        result = await _run(
            connection,
            "if command -v owut >/dev/null 2>&1; then owut upgrade; "
            "elif command -v auc >/dev/null 2>&1; then auc -y; "
            "else exit 127; fi",
            timeout=3600,
            check=True,
        )
        return result.stdout.strip() or "Обновление OpenWrt запущено."
    finally:
        connection.close()
        await connection.wait_closed()


def build_backup_command(platform: str, remote_path: str) -> str:
    """Собирает команду создания архива конфигов.

    Прошлый вариант использовал `set -e` вместе с `[ -e "$p" ] && ...`:
    первый же отсутствующий путь завершал оболочку, и копия не создавалась.
    """
    if platform == "openwrt":
        return f"sysupgrade -b {remote_path}"
    return (
        "set -u; files=''; "
        "for p in /etc/ssh /etc/systemd/system /etc/wireguard "
        "/etc/xray /opt/AdGuardHome/AdGuardHome.yaml; do "
        "if [ -e \"$p\" ]; then files=\"$files ${p#/}\"; fi; done; "
        "if [ -z \"$files\" ]; then "
        "echo 'На устройстве нет конфигов для копирования' >&2; exit 1; fi; "
        f"sudo -n tar -czf {remote_path} -C / $files"
    )


async def backup_configs(
    device: Device,
    credentials: dict[str, str],
    destination: Path,
) -> Path:
    connection = await _connect(device, credentials)
    remote_path = ""
    local_path: Path | None = None
    try:
        platform, _ = await detect_platform(connection)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", device.name).strip("_") or "device"
        destination.mkdir(parents=True, exist_ok=True)
        local_path = destination / f"{safe_name}-{platform}-{stamp}.tar.gz"
        remote_path = f"/tmp/ven4control-backup-{stamp}.tar.gz"
        await _run(
            connection,
            build_backup_command(platform, remote_path),
            timeout=300,
            check=True,
        )
        try:
            async with connection.start_sftp_client() as sftp:
                await sftp.get(remote_path, str(local_path))
        except Exception:
            # Недокачанный архив только путает: он выглядит как готовая копия.
            local_path.unlink(missing_ok=True)
            raise
        return local_path
    finally:
        if remote_path:
            await _run(connection, f"rm -f {remote_path}", timeout=15)
        connection.close()
        await connection.wait_closed()
