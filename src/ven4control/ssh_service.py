import base64
import os
import socket
import subprocess
import tempfile
import time
from pathlib import Path

import asyncssh

from .models import Device
from .windows_identity import current_user_principal


def tcp_check(host: str, port: int, timeout: float = 2.5) -> tuple[bool, str]:
    try:
        started = time.perf_counter()
        with socket.create_connection((host, port), timeout=timeout):
            elapsed = time.perf_counter() - started
        return True, f"{max(1, round(elapsed * 1000))} мс"
    except OSError as error:
        return False, str(error)


def ensure_app_key(private_path: Path) -> tuple[Path, Path]:
    """Возвращает пути приватного и публичного ключа приложения.

    Существующий приватный ключ никогда не перезаписывается: он уже добавлен
    в `authorized_keys` устройств. Новая пара создаётся только если приватного
    ключа нет, а потерянный публичный ключ восстанавливается из приватного.
    """
    public_path = private_path.with_suffix(".pub")
    if not private_path.exists():
        private_path.parent.mkdir(parents=True, exist_ok=True)
        key = asyncssh.generate_private_key("ssh-ed25519")
        private_path.write_bytes(key.export_private_key("openssh"))
        public_path.write_bytes(key.export_public_key("openssh"))
    elif not public_path.exists():
        restore_public_key(private_path, public_path)
    secure_private_key_permissions(private_path)
    return private_path, public_path


def restore_public_key(private_path: Path, public_path: Path) -> bool:
    """Восстанавливает `.pub` из приватного ключа. Ошибку не поднимает."""
    try:
        key = asyncssh.read_private_key(private_path)
        public_path.write_bytes(key.export_public_key("openssh"))
    except (OSError, asyncssh.KeyImportError, asyncssh.KeyEncryptionError):
        return False
    return True


def secure_private_key_permissions(private_path: Path) -> None:
    if os.name != "nt":
        private_path.chmod(0o600)
        return
    if not os.environ.get("USERNAME"):
        raise RuntimeError("Не удалось определить текущего пользователя Windows")
    principal = current_user_principal()
    result = _apply_windows_private_key_acl(private_path, principal)
    if result.returncode == 0:
        return

    # Старые версии оставляли пользователю только чтение, после чего повторное
    # изменение DACL завершалось отказом в доступе. Создаём уже защищённый файл,
    # переносим в него ключ и атомарно заменяем старый файл.
    temporary_path: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            dir=private_path.parent,
            prefix=f".{private_path.name}.",
        )
        os.close(descriptor)
        temporary_path = Path(temporary_name)
        recovery = _apply_windows_private_key_acl(temporary_path, principal)
        if recovery.returncode != 0:
            raise RuntimeError(_acl_error(recovery))
        temporary_path.write_bytes(private_path.read_bytes())
        os.replace(temporary_path, private_path)
    except OSError as error:
        raise RuntimeError(f"Не удалось защитить SSH-ключ: {error}") from error
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _apply_windows_private_key_acl(
    private_path: Path,
    principal: str,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            [
                "icacls",
                str(private_path),
                "/inheritance:r",
                "/grant:r",
                f"{principal}:(F)",
            ],
            capture_output=True,
            text=True,
            encoding="oem",
            errors="replace",
        )
    except FileNotFoundError as error:
        raise RuntimeError(
            "Не удалось защитить SSH-ключ: команда icacls не найдена"
        ) from error


def _acl_error(result: subprocess.CompletedProcess[str]) -> str:
    detail = (result.stderr or result.stdout).strip()
    return f"Не удалось защитить SSH-ключ: {detail or 'icacls завершился с ошибкой'}"


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
