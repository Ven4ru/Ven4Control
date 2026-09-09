import json
import re
import shlex
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import asyncssh

from ven4control.host_key import HostKeyPin, pinned_options
from ven4control.models import Device
from ven4control.powershell import quote as ps_quote


SERVICE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_.@-]+$")

# Номер релиза в конце имени пакета apk: `openssh-sftp-server-10.3_p1-r1`.
APK_RELEASE_PATTERN = re.compile(r"r\d+")

MIN_LOG_LINES = 20
MAX_LOG_LINES = 1000

# Источник журнала: фильтр для logread и юнит systemd.
# Пустой фильтр означает журнал целиком.
LOG_SOURCES: dict[str, tuple[str, str]] = {
    "system": ("", ""),
    "tailscale": ("tailscale", "tailscaled"),
}


# Показатели системы одной командой: значения возвращаются строками вида
# «КЛЮЧ=значение». Команда общая для разового обзора и фонового снапшота,
# чтобы CPU и память считались одинаково в обоих режимах.
#
# Температура берётся максимумом по всем зонам /sys/class/thermal: их обычно
# несколько (ядро, WiFi-радио), и показательна самая горячая. Ошибка awk
# подавлена и закрыта запасным echo намеренно: термозон может не быть вовсе —
# тогда шаблон остаётся нераскрытым и awk падает, — а команда выполняется с
# check=True, поэтому ненулевой код утащил бы за собой и остальные показатели.
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
printf 'TEMP='
awk 'FNR==1 && $1+0>0 {if ($1+0>m) m=$1+0} END {
  if (m) printf "%.1f°C\n", m/1000; else print "нет данных"
}' /sys/class/thermal/thermal_zone*/temp 2>/dev/null || echo 'нет данных'
"""


# Проба Windows: переменная $PSVersionTable существует только в PowerShell,
# который служит оболочкой по умолчанию во встроенном OpenSSH Server Windows.
# В POSIX-оболочке этот текст не разбирается и завершается ненулевым кодом,
# поэтому проба безопасна для роутеров и Linux-серверов.
#
# Описание системы намеренно не через Get-CimInstance/Get-WmiObject: живая
# проверка на реальном OpenSSH Server Windows показала, что CIM/WMI-запрос в
# неинтерактивном exec-канале SSH не просто падает, а рвёт всё соединение
# целиком (exit_status=None, пустой вывод) — тогда и POSIX-проба на том же
# соединении отваливается с «SSH connection closed», а не честным отказом.
# [System.Environment]::OSVersion.Version не трогает CIM/WMI вообще и не
# воспроизводит эту проблему; описание менее «человеческое» ("Windows
# 10.0.26200"), но не ценой падения всего определения платформы.
WINDOWS_PLATFORM_COMMAND = (
    "if ($null -eq $PSVersionTable) { exit 1 }; "
    "Write-Output 'windows'; "
    "Write-Output ('Windows ' + [System.Environment]::OSVersion.Version)"
)

# Проба POSIX: разделяет OpenWrt и остальной Linux.
POSIX_PLATFORM_COMMAND = (
    "if [ -f /etc/openwrt_release ]; then "
    ". /etc/openwrt_release; echo openwrt; echo \"${DISTRIB_DESCRIPTION:-OpenWrt}\"; "
    "elif [ -f /etc/os-release ]; then "
    ". /etc/os-release; echo linux; echo \"${PRETTY_NAME:-Linux}\"; "
    "else echo linux; uname -sr; fi"
)


# Ветка реестра службы удалённых рабочих столов Windows.
TERMINAL_SERVER_KEY = "HKLM:\\System\\CurrentControlSet\\Control\\Terminal Server"

# Метка, по которой ответ узнаётся среди прочего вывода PowerShell.
RDP_STATE_MARKER = "RDP"

RDP_STATE_COMMAND = (
    f"$v = (Get-ItemProperty -Path '{TERMINAL_SERVER_KEY}' "
    "-Name fDenyTSConnections -ErrorAction Stop).fDenyTSConnections; "
    f"Write-Output ('{RDP_STATE_MARKER}=' + [int]$v)"
)

# Включение меняет только приём входящих подключений. Правила файрвола не
# трогаются намеренно: RDP ходит внутри SSH-туннеля на 127.0.0.1 устройства,
# и открытый наружу порт 3389 не нужен ни при каких условиях.
RDP_ENABLE_COMMAND = (
    f"Set-ItemProperty -Path '{TERMINAL_SERVER_KEY}' "
    "-Name fDenyTSConnections -Value 0 -Type DWord -ErrorAction Stop; "
    f"Write-Output '{RDP_STATE_MARKER}=0'"
)


# Отказ операций, у которых есть только POSIX-реализация. Windows-аналоги —
# отдельная работа; до неё пользователь должен получать объяснение, а не
# «systemctl не является внутренней или внешней командой» от PowerShell.
WINDOWS_UNSUPPORTED = "Операция недоступна для Windows-устройств."


# Единственный оставшийся способ остаться без fingerprint — ответить «Нет»
# на подтверждение при добавлении устройства: сам отпечаток запрашивается и
# сохраняется до и независимо от установки ключа. Совет «переустановите ключ»
# был неисполним — пути переустановки в интерфейсе нет.
MISSING_FINGERPRINT_MESSAGE = (
    "Fingerprint не был подтверждён при добавлении устройства. "
    "Удалите устройство и добавьте заново, подтвердив fingerprint."
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
    temperature: str
    tailscale: str
    wireguard: str


@dataclass(slots=True)
class ServiceInfo:
    name: str
    state: str
    details: str = ""


@dataclass(slots=True)
class PackageResult:
    """Один результат поиска пакета: имя, готовое для установки, и описание."""

    name: str
    description: str = ""


def _apk_bare_name(versioned: str) -> str:
    """Имя пакета apk без версии.

    `apk search` без `-q` печатает `имя-версия-релиз` одной строкой (`apk
    search -q` даёт чистое имя, но тогда пропадает возможность получить
    описание в том же вызове — поэтому парсим версию сами). Правило: с
    конца отрезается релиз вида `rN`, затем сегменты версии, начинающиеся
    с цифры; имя пакета в apk с цифры не начинается (проверено на реальных
    примерах: `vsftpd-3.0.5-r6`, `openssh-sftp-server-10.3_p1-r1`,
    `erlang-ssh-28.0.3-r1`).
    """
    parts = versioned.split("-")
    if len(parts) > 1 and APK_RELEASE_PATTERN.fullmatch(parts[-1]):
        parts.pop()
    while len(parts) > 1 and parts[-1][:1].isdigit():
        parts.pop()
    return "-".join(parts)


def parse_apk_search(output: str) -> list[PackageResult]:
    """Разбирает вывод `apk search -v -d <термин>`."""
    results: list[PackageResult] = []
    for line in output.splitlines():
        line = line.strip()
        if not line or " - " not in line:
            continue
        versioned, description = line.split(" - ", 1)
        results.append(PackageResult(_apk_bare_name(versioned.strip()), description.strip()))
    return results


def parse_opkg_search(output: str) -> list[PackageResult]:
    """Разбирает вывод `opkg list | grep <термин>` — формат `имя - версия - описание`."""
    results: list[PackageResult] = []
    for line in output.splitlines():
        line = line.strip()
        parts = line.split(" - ", 2)
        if len(parts) < 2 or not parts[0].strip():
            continue
        name = parts[0].strip()
        description = parts[2].strip() if len(parts) > 2 else ""
        results.append(PackageResult(name, description))
    return results


def parse_apt_search(output: str) -> list[PackageResult]:
    """Разбирает вывод `apt-cache search <термин>` — формат `имя - описание`.

    Разрез строго по ПЕРВОМУ ` - `: описание само может содержать этот же
    разделитель (реальный пример: `gvfs-backends - userspace virtual
    filesystem - backends`), maxsplit=1 обязателен.
    """
    results: list[PackageResult] = []
    for line in output.splitlines():
        line = line.strip()
        if " - " not in line:
            continue
        name, description = line.split(" - ", 1)
        name = name.strip()
        if name:
            results.append(PackageResult(name, description.strip()))
    return results


def parse_winget_search(output: str) -> list[PackageResult]:
    """Разбирает вывод `winget search <термин> --accept-source-agreements`.

    winget не даёт структурированный вывод для search (нет флага JSON) —
    только таблицу с колонками ФИКСИРОВАННОЙ ширины (не просто выровненную
    пробелами): позиции начала столбцов берутся из строки заголовка и
    одинаковы для всех строк таблицы. Разбор по «2+ пробела подряд» ломается
    на реальных данных: длинный Id может упираться в границу столбца Version
    всего одним пробелом (живой пример — `Orange-OpenSource.Hurl 8.0.1`,
    оба поля склеиваются в одно) — тогда в install ушёл бы `--id` с лишним
    текстом версии, и winget не нашёл бы такой пакет. Нарезка по позициям
    заголовка не подвержена этой проблеме — граница столбца не зависит от
    того, сколько пробелов оказалось у конкретной строки.

    В install идёт Id (`Bitvise.SSH.Client`), не Name — тот же принцип, что
    у apk/opkg/apt: PackageResult.name — точный устанавливаемый
    идентификатор, Name+Version собираются в description для показа.
    """
    lines = [line for line in output.splitlines() if line.strip()]
    separator_index = next(
        (i for i, line in enumerate(lines) if set(line.strip()) == {"-"}),
        None,
    )
    if separator_index is None or separator_index == 0:
        return []
    header = lines[separator_index - 1]
    try:
        id_start = header.index("Id")
        version_start = header.index("Version")
        match_start = header.index("Match")
    except ValueError:
        return []
    results: list[PackageResult] = []
    for line in lines[separator_index + 1:]:
        name = line[:id_start].strip()
        package_id = line[id_start:version_start].strip()
        version = line[version_start:match_start].strip()
        if not package_id:
            continue
        results.append(PackageResult(package_id, f"{name} · {version}"))
    return results


@dataclass(slots=True)
class RdpStatus:
    """Ответ на вопрос «можно ли подключиться к устройству по RDP».

    Платформа хранится рядом с признаком, чтобы интерфейс отличал
    «RDP выключен» от «RDP неприменим»: у роутера и Linux-сервера его нет
    вовсе, и предлагать там кнопку включения бессмысленно.
    """

    platform: str
    description: str
    enabled: bool

    @property
    def supported(self) -> bool:
        return self.platform == "windows"


def _connection_options(
    device: Device,
    credentials: dict[str, str],
) -> dict[str, object]:
    options: dict[str, object] = {
        "host": device.host,
        "port": device.port,
        "username": device.username,
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
        raise FingerprintError(MISSING_FINGERPRINT_MESSAGE)
    options = _connection_options(device, credentials)
    options.update(pinned_options(HostKeyPin(device.fingerprint)))
    try:
        return await asyncssh.connect(**options)
    except asyncssh.HostKeyNotVerifiable as error:
        # Проверка сработала при обмене ключами: пароль и приватный ключ
        # устройству не отправлялись.
        raise FingerprintError(
            "SSH fingerprint устройства изменился. Управление заблокировано."
        ) from error


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


async def _require_windows(
    connection: asyncssh.SSHClientConnection,
) -> tuple[str, str]:
    """Возвращает платформу или отказывает, если это не Windows."""
    platform, description = await detect_platform(connection)
    if platform != "windows":
        raise RuntimeError(
            f"RDP есть только у Windows, устройство определено как "
            f"{description} ({platform})."
        )
    return platform, description


async def _reject_windows(
    connection: asyncssh.SSHClientConnection,
) -> tuple[str, str]:
    """Платформа устройства; для Windows — честный отказ вместо мусора.

    Зеркало `_require_windows`: там операция есть только у Windows, здесь —
    только у POSIX-систем. Проба платформы стоит одну короткую команду и
    дешевле, чем разбор невнятной ошибки PowerShell пользователем.
    """
    platform, description = await detect_platform(connection)
    if platform == "windows":
        raise RuntimeError(WINDOWS_UNSUPPORTED)
    return platform, description


def parse_rdp_state(output: str) -> bool:
    """Разбирает ответ проверки RDP.

    Ноль в fDenyTSConnections означает «входящие RDP-подключения разрешены»;
    любое другое значение и отсутствие строки считаются запретом.
    """
    for line in output.splitlines():
        text = line.strip()
        if text.startswith(f"{RDP_STATE_MARKER}="):
            value = text.split("=", 1)[1].strip()
            try:
                return int(value) == 0
            except ValueError:
                return False
    return False


async def check_rdp(
    device: Device,
    credentials: dict[str, str],
) -> RdpStatus:
    """Узнаёт по SSH, принимает ли устройство RDP-подключения.

    Сетевого обращения к порту RDP не происходит: состояние читается из
    реестра через то же доверенное SSH-соединение, что и всё управление,
    поэтому порт 3389 наружу открывать не требуется.
    """
    connection = await _connect(device, credentials)
    try:
        platform, description = await detect_platform(connection)
        if platform != "windows":
            return RdpStatus(platform, description, False)
        result = await _run(connection, RDP_STATE_COMMAND, timeout=30)
        if result.exit_status != 0:
            # Значения в реестре нет: RDP на устройстве ни разу не включали.
            return RdpStatus(platform, description, False)
        return RdpStatus(platform, description, parse_rdp_state(result.stdout))
    finally:
        connection.close()
        await connection.wait_closed()


async def is_rdp_enabled(
    device: Device,
    credentials: dict[str, str],
) -> bool:
    """True, если Windows-устройство принимает входящие RDP-подключения.

    Для не-Windows поднимает ошибку: у таких устройств RDP не выключен,
    а отсутствует, и молчаливое False скрыло бы разницу.
    """
    connection = await _connect(device, credentials)
    try:
        await _require_windows(connection)
        result = await _run(connection, RDP_STATE_COMMAND, timeout=30)
        if result.exit_status != 0:
            return False
        return parse_rdp_state(result.stdout)
    finally:
        connection.close()
        await connection.wait_closed()


async def enable_rdp(
    device: Device,
    credentials: dict[str, str],
) -> None:
    """Разрешает приём входящих RDP-подключений на Windows-устройстве.

    Меняется только локальная настройка службы удалённых рабочих столов.
    Файрвол не трогается намеренно: подключение идёт внутри SSH-туннеля на
    127.0.0.1 устройства, поэтому порт наружу не нужен.
    """
    connection = await _connect(device, credentials)
    try:
        await _require_windows(connection)
        await _run(connection, RDP_ENABLE_COMMAND, timeout=60, check=True)
    finally:
        connection.close()
        await connection.wait_closed()


async def collect_overview(
    device: Device,
    credentials: dict[str, str],
) -> SystemOverview:
    connection = await _connect(device, credentials)
    try:
        platform, description = await _reject_windows(connection)
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
            temperature=values.get("TEMP", "нет данных"),
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
        platform, _ = await _reject_windows(connection)
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
        await _reject_windows(connection)
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
        platform, _ = await _reject_windows(connection)
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
        platform, _ = await _reject_windows(connection)
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
        platform, _ = await _reject_windows(connection)
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


async def search_packages(
    device: Device,
    credentials: dict[str, str],
    term: str,
    *,
    limit: int | None = None,
) -> list[PackageResult]:
    """Ищет пакет в уже настроенных на устройстве репозиториях.

    `term=""` — без фильтра: apk/apt в этом случае сами перечисляют весь
    каталог (проверено живьём: `apk search` без паттерна и с пустой
    строкой-паттерном возвращают один и тот же список — apk называет это
    «no pattern given, list all packages»). Каталог настоящий — десятки
    тысяч строк на apt (85576 живьём на реальном VPS), поэтому `limit`
    обрезает вывод НА УСТРОЙСТВЕ через `head`, не после передачи по SSH —
    не гонять по сети то, что всё равно будет отброшено. Для Windows
    пустой запрос без `limit` не проверялся и не тот же приём, что у
    Linux/OpenWrt — при пустом `term` с `limit` winget просто пропускается
    (пустой список), сама возможность стартового каталога — только для
    Linux/OpenWrt по прямой просьбе пользователя.
    """
    connection = await _connect(device, credentials)
    try:
        platform, _ = await detect_platform(connection)
        safe_term = shlex.quote(term)
        if platform == "windows":
            if not term and limit is not None:
                return []
            command = f"winget search {ps_quote(term)} --accept-source-agreements"
            parser = parse_winget_search
        elif platform == "openwrt":
            command = (
                "if command -v apk >/dev/null 2>&1; then "
                "apk update >/dev/null 2>&1; "
                f"apk search -v -d {safe_term} 2>/dev/null; "
                "elif command -v opkg >/dev/null 2>&1; then "
                "opkg update >/dev/null 2>&1; "
                f"opkg list 2>/dev/null | grep -i {safe_term}; "
                "else echo 'Менеджер пакетов не найден' >&2; exit 127; fi"
            )
            parser = parse_apk_search
        else:
            command = (
                "sudo -n apt-get update >/dev/null 2>&1; "
                f"apt-cache search {safe_term}"
            )
            parser = parse_apt_search
        if limit is not None:
            command = f"{command} | head -n {int(limit)}"
        result = await _run(connection, command, timeout=30, check=False)
        # Ненулевой код при пустом выводе — настоящий сбой; ненулевой код при
        # непустом выводе даёт grep opkg-ветки, когда совпадений нет, и это
        # не ошибка.
        if result.exit_status not in (0, None) and not result.stdout.strip():
            detail = (result.stderr or "").strip()
            raise RuntimeError(detail or "Поиск не выполнен.")
        return parser(result.stdout)
    finally:
        connection.close()
        await connection.wait_closed()


async def install_package(
    device: Device,
    credentials: dict[str, str],
    name: str,
) -> str:
    """Устанавливает пакет по имени, полученному из результатов поиска."""
    connection = await _connect(device, credentials)
    try:
        platform, _ = await detect_platform(connection)
        safe_name = shlex.quote(name)
        if platform == "windows":
            command = (
                f"winget install --id {ps_quote(name)} --exact --silent "
                "--accept-package-agreements --accept-source-agreements"
            )
        elif platform == "openwrt":
            command = (
                "if command -v apk >/dev/null 2>&1; then "
                f"apk add {safe_name}; "
                "elif command -v opkg >/dev/null 2>&1; then "
                f"opkg install {safe_name}; "
                "else echo 'Менеджер пакетов не найден' >&2; exit 127; fi"
            )
        else:
            command = (
                "sudo -n env DEBIAN_FRONTEND=noninteractive "
                f"apt-get install -y {safe_name}"
            )
        result = await _run(connection, command, timeout=300, check=True)
        return result.stdout.strip() or f"Пакет «{name}» установлен."
    finally:
        connection.close()
        await connection.wait_closed()


# Владелец этой папки на устройстве — кнопка «Ven4Tools»: обновление
# полностью перезаписывает её содержимое, не класть туда ничего своего.
VEN4TOOLS_INSTALL_PATH = "C:\\Ven4Tools"
VEN4TOOLS_REPO = "Ven4ru/Ven4Tools"
VEN4TOOLS_VERSION_MARKER = f"{VEN4TOOLS_INSTALL_PATH}\\.ven4tools-version"


async def install_ven4tools(device: Device, credentials: dict[str, str]) -> str:
    """Скачивает последний релиз Ven4Tools с GitHub и распаковывает на устройство.

    Тот же путь и для первой установки, и для обновления — Expand-Archive
    -Force перезаписывает совпадающие файлы. $ProgressPreference обязателен
    (без него Invoke-WebRequest зависает на неинтерактивной SSH-сессии на
    некоторых машинах).

    Живая находка: при долгом скачивании SSH-канал клиента может
    оборваться уже ПОСЛЕ того, как установка на устройстве реально
    завершилась — `_run` в этом случае получает `exit_status=None` и
    пустой вывод (не `TimeoutError`, соединение не зависает, оно рвётся
    именно в момент завершения передачи) и репортует это как обычную
    ошибку. Чтобы не выдавать реальный успех за сбой, при любой ошибке
    основной команды делаем короткую отдельную проверку через
    `_verify_ven4tools_install` — если она подтверждает свежую установку,
    возвращаем успех; если сама проверка тоже не отвечает — не гадать,
    дать исходной ошибке распространиться как есть.
    """
    connection = await _connect(device, credentials)
    try:
        platform, description = await detect_platform(connection)
        if platform != "windows":
            raise RuntimeError(
                f"Ven4Tools ставится только на Windows, устройство "
                f"определено как {description} ({platform})."
            )
        command = (
            '$ProgressPreference = "SilentlyContinue"; '
            "$release = Invoke-RestMethod -Uri "
            f'"https://api.github.com/repos/{VEN4TOOLS_REPO}/releases/latest"; '
            '$asset = $release.assets | Where-Object { $_.name -like "*.zip" } '
            "| Select-Object -First 1; "
            "if (-not $asset) { throw 'В последнем релизе Ven4Tools нет ZIP-архива.' }; "
            '$zipPath = "$env:TEMP\\ven4tools-update.zip"; '
            "Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $zipPath; "
            f"Expand-Archive -Path $zipPath -DestinationPath {ps_quote(VEN4TOOLS_INSTALL_PATH)} -Force; "
            "Remove-Item $zipPath -Force; "
            f"Set-Content -Path {ps_quote(VEN4TOOLS_VERSION_MARKER)} -Value $release.tag_name -NoNewline; "
            f'Write-Output "Ven4Tools $($release.tag_name) установлен в {VEN4TOOLS_INSTALL_PATH}"'
        )
        try:
            result = await _run(connection, command, timeout=600, check=True)
            return result.stdout.strip()
        except RuntimeError as error:
            # Живая находка: после того как канал рвётся во время долгой
            # передачи, само SSH-соединение (не только этот
            # канал) оказывается непригодно для новых команд — повторное
            # использование того же `connection` для проверки надёжно не
            # срабатывает. Проверка открывает СВОЁ отдельное соединение.
            verified = await _verify_ven4tools_install(device, credentials)
            if verified is not None:
                return verified
            raise error
    finally:
        connection.close()
        await connection.wait_closed()


async def _verify_ven4tools_install(
    device: Device, credentials: dict[str, str]
) -> str | None:
    """Короткая проверка после сбоя основной команды `install_ven4tools`.

    Запрашивает у GitHub актуальный тег ещё раз и сравнивает его с
    маркером версии, который основная команда пишет ПОСЛЕДНИМ шагом
    (после `Expand-Archive`) — если совпадает, установка на устройстве
    реально завершена, даже если исходная команда не успела вернуть
    ответ. Открывает НОВОЕ соединение (не переиспользует то, что только
    что оборвалось) — сама проверка не отвечает — возвращает None (не
    гадать).
    """
    try:
        connection = await _connect(device, credentials)
    except Exception:
        return None
    try:
        command = (
            "$release = Invoke-RestMethod -Uri "
            f'"https://api.github.com/repos/{VEN4TOOLS_REPO}/releases/latest"; '
            f"$marker = Get-Content -Path {ps_quote(VEN4TOOLS_VERSION_MARKER)} -ErrorAction SilentlyContinue; "
            "if ($marker -eq $release.tag_name) { Write-Output $release.tag_name } "
            "else { Write-Output '' }"
        )
        try:
            result = await _run(connection, command, timeout=30, check=False)
        except Exception:
            return None
        tag = result.stdout.strip()
        if not tag:
            return None
        return (
            f"Ven4Tools {tag} уже установлен в {VEN4TOOLS_INSTALL_PATH} "
            "(подтверждено проверкой после обрыва соединения)."
        )
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
    if platform == "windows":
        raise RuntimeError(WINDOWS_UNSUPPORTED)
    if platform == "openwrt":
        return f"sysupgrade -b {remote_path}"
    return (
        "set -u; files=''; "
        "for p in /etc/ssh /etc/systemd/system /etc/wireguard; do "
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
        platform, _ = await _reject_windows(connection)
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
