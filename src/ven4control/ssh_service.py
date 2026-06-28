import asyncio
import base64
import hashlib
import os
import socket
import subprocess
import time
from pathlib import Path

import asyncssh

from .models import Device


def tcp_check(host: str, port: int, timeout: float = 2.5) -> tuple[bool, str]:
    try:
        started = time.perf_counter()
        with socket.create_connection((host, port), timeout=timeout):
            elapsed = time.perf_counter() - started
        return True, f"{max(1, round(elapsed * 1000))} мс"
    except OSError as error:
        return False, str(error)


def public_key_fingerprint(key_data: bytes) -> str:
    fields = key_data.strip().split()
    if len(fields) < 2:
        return ""
    raw = base64.b64decode(fields[1])
    digest = base64.b64encode(hashlib.sha256(raw).digest()).decode().rstrip("=")
    return f"SHA256:{digest}"


def ensure_app_key(private_path: Path) -> tuple[Path, Path]:
    public_path = private_path.with_suffix(".pub")
    if not private_path.exists():
        private_path.parent.mkdir(parents=True, exist_ok=True)
        key = asyncssh.generate_private_key("ssh-ed25519")
        private_path.write_bytes(key.export_private_key("openssh"))
        public_path.write_bytes(key.export_public_key("openssh"))
    secure_private_key_permissions(private_path)
    return private_path, public_path


def secure_private_key_permissions(private_path: Path) -> None:
    if os.name != "nt":
        private_path.chmod(0o600)
        return
    username = os.environ.get("USERNAME")
    if not username:
        raise RuntimeError("Не удалось определить текущего пользователя Windows")
    result = subprocess.run(
        [
            "icacls",
            str(private_path),
            "/inheritance:r",
            "/grant:r",
            f"{username}:(R)",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise RuntimeError(f"Не удалось защитить SSH-ключ: {result.stderr.strip()}")


async def install_public_key(
    device: Device,
    password: str,
    public_key_path: Path,
    expected_fingerprint: str,
) -> str:
    public_key = public_key_path.read_text(encoding="utf-8").strip()
    async with asyncssh.connect(
        device.host,
        port=device.port,
        username=device.username,
        password=password,
        known_hosts=None,
        login_timeout=10,
    ) as connection:
        actual_fingerprint = connection.get_server_host_key().get_fingerprint("sha256")
        if actual_fingerprint != expected_fingerprint:
            raise RuntimeError("SSH fingerprint изменился между проверкой и установкой ключа")
        probe = await connection.run(
            "if [ -f /etc/openwrt_release ]; then echo openwrt; "
            "elif [ \"$(uname -s)\" = Linux ]; then echo linux; else echo unknown; fi",
            check=True,
        )
        system = probe.stdout.strip()
        encoded = base64.b64encode(public_key.encode()).decode()
        if system == "openwrt":
            command = (
                "set -eu; mkdir -p /etc/dropbear /root/ven4control-backups; "
                "f=/etc/dropbear/authorized_keys; touch \"$f\"; "
                "cp -p \"$f\" \"/root/ven4control-backups/authorized_keys.$(date +%Y%m%d-%H%M%S)\"; "
                f"k=$(printf '%s' '{encoded}' | base64 -d); "
                "grep -qxF \"$k\" \"$f\" || printf '%s\\n' \"$k\" >> \"$f\"; chmod 600 \"$f\""
            )
        elif system == "linux":
            command = (
                "set -eu; mkdir -p ~/.ssh ~/ven4control-backups; chmod 700 ~/.ssh; "
                "f=~/.ssh/authorized_keys; touch \"$f\"; "
                "cp -p \"$f\" \"$HOME/ven4control-backups/authorized_keys.$(date +%Y%m%d-%H%M%S)\"; "
                f"k=$(printf '%s' '{encoded}' | base64 -d); "
                "grep -qxF \"$k\" \"$f\" || printf '%s\\n' \"$k\" >> \"$f\"; chmod 600 \"$f\""
            )
        else:
            raise RuntimeError("Автоматическая установка поддерживает Linux и OpenWrt")
        await connection.run(command, check=True)
    return system


async def probe_device(device: Device, password: str) -> tuple[str, str]:
    async with asyncssh.connect(
        device.host,
        port=device.port,
        username=device.username,
        password=password,
        known_hosts=None,
        login_timeout=10,
    ) as connection:
        key = connection.get_server_host_key()
        fingerprint = key.get_fingerprint("sha256")
        probe = await connection.run(
            "if [ -f /etc/openwrt_release ]; then echo openwrt; "
            "elif [ \"$(uname -s)\" = Linux ]; then echo linux; else echo unknown; fi",
            check=True,
        )
        return probe.stdout.strip(), fingerprint
