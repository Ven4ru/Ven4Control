import asyncio
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import asyncssh

from ven4control.models import Device


SERVICE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_.@-]+$")


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
        raise RuntimeError(
            "Для управления требуется сохранённый SSH fingerprint. "
            "Переустановите ключ Ven4Control для этого устройства."
        )
    connection = await asyncssh.connect(**_connection_options(device, credentials))
    actual = connection.get_server_host_key().get_fingerprint("sha256")
    if actual != device.fingerprint:
        connection.close()
        await connection.wait_closed()
        raise RuntimeError(
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
    result = await connection.run(command, check=False, timeout=timeout)
    if check and result.exit_status != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(detail or f"Команда завершилась с кодом {result.exit_status}")
    return result


async def detect_platform(
    connection: asyncssh.SSHClientConnection,
) -> tuple[str, str]:
    result = await _run(
        connection,
        "if [ -f /etc/openwrt_release ]; then "
        ". /etc/openwrt_release; echo openwrt; echo \"${DISTRIB_DESCRIPTION:-OpenWrt}\"; "
        "elif [ -f /etc/os-release ]; then "
        ". /etc/os-release; echo linux; echo \"${PRETTY_NAME:-Linux}\"; "
        "else echo linux; uname -sr; fi",
        check=True,
    )
    lines = result.stdout.strip().splitlines()
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
            r"""
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
""",
            timeout=15,
            check=True,
        )
        values: dict[str, str] = {}
        for line in metrics.stdout.splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                values[key] = value.strip()

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
        health = data.get("Health") or []
        suffix = "онлайн" if online else "офлайн"
        if health:
            return f"{backend}, {suffix}; {health[0]}"
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
        services = []
        for line in result.stdout.splitlines():
            fields = line.split(None, 4)
            if len(fields) >= 4:
                services.append(ServiceInfo(fields[0], fields[3], fields[4] if len(fields) > 4 else ""))
        return platform, services
    finally:
        connection.close()
        await connection.wait_closed()


async def read_logs(
    device: Device,
    credentials: dict[str, str],
    source: str,
    lines: int = 200,
) -> str:
    commands = {
        "system": ("logread 2>/dev/null || journalctl -n {lines} --no-pager", None),
        "tailscale": (
            "logread 2>/dev/null | grep -i tailscale | tail -n {lines} || "
            "journalctl -u tailscaled -n {lines} --no-pager",
            None,
        ),
        "adguard": (
            "logread 2>/dev/null | grep -i adguard | tail -n {lines} || "
            "journalctl -u AdGuardHome -n {lines} --no-pager",
            None,
        ),
        "xray": (
            "logread 2>/dev/null | grep -i xray | tail -n {lines} || "
            "journalctl -u xray -n {lines} --no-pager",
            None,
        ),
    }
    if source not in commands:
        raise ValueError("Неизвестный источник журнала")
    connection = await _connect(device, credentials)
    try:
        command = commands[source][0].format(lines=max(20, min(lines, 1000)))
        result = await _run(connection, command, timeout=30)
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
                f"[ -x /etc/init.d/{service} ] && /etc/init.d/{service} restart"
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
            command = (
                "sudo -n apt-get update && "
                "DEBIAN_FRONTEND=noninteractive sudo -n apt-get upgrade -y"
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


async def backup_configs(
    device: Device,
    credentials: dict[str, str],
    destination: Path,
) -> Path:
    connection = await _connect(device, credentials)
    remote_path = ""
    try:
        platform, _ = await detect_platform(connection)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", device.name).strip("_") or "device"
        destination.mkdir(parents=True, exist_ok=True)
        local_path = destination / f"{safe_name}-{platform}-{stamp}.tar.gz"
        remote_path = f"/tmp/ven4control-backup-{stamp}.tar.gz"
        if platform == "openwrt":
            command = f"sysupgrade -b {remote_path}"
        else:
            command = (
                "set -eu; files=''; "
                "for p in /etc/ssh /etc/systemd/system /etc/wireguard "
                "/etc/xray /opt/AdGuardHome/AdGuardHome.yaml; do "
                "[ -e \"$p\" ] && files=\"$files ${p#/}\"; done; "
                f"[ -n \"$files\" ] && sudo -n tar -czf {remote_path} -C / $files"
            )
        await _run(connection, command, timeout=300, check=True)
        async with connection.start_sftp_client() as sftp:
            await sftp.get(remote_path, str(local_path))
        return local_path
    finally:
        if remote_path:
            await _run(connection, f"rm -f {remote_path}", timeout=15)
        connection.close()
        await connection.wait_closed()
