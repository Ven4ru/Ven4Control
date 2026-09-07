"""Автозапуск приложения при входе в Windows.

Используется ветка реестра `HKCU\\...\\Run`: она не требует прав
администратора и не оставляет задач в планировщике, которые пользователь
потом не найдёт. Запись хранит команду запуска, а не только путь, поэтому
работает и для сборки exe, и для запуска из исходников.
"""
from __future__ import annotations

import os
import sys


RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "Ven4Control"

# Запуск при входе в систему не должен открывать окно: приложению нужно лишь
# поднять фоновые сессии и остаться в трее.
TRAY_ARGUMENT = "--tray"


def is_supported() -> bool:
    return os.name == "nt"


def startup_command(
    executable: str | None = None,
    frozen: bool | None = None,
) -> str:
    """Команда, которую Windows выполнит при входе пользователя."""
    path = executable or sys.executable
    packed = getattr(sys, "frozen", False) if frozen is None else frozen
    if packed:
        return f'"{path}" {TRAY_ARGUMENT}'
    # Запуск из исходников: интерпретатор сам по себе приложение не откроет.
    return f'"{path}" -m ven4control.app {TRAY_ARGUMENT}'


def _require_windows() -> None:
    if not is_supported():
        raise RuntimeError("Автозапуск настраивается только в Windows")


def is_enabled(
    value_name: str = VALUE_NAME,
    key_path: str = RUN_KEY_PATH,
) -> bool:
    if not is_supported():
        return False
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ
        ) as key:
            winreg.QueryValueEx(key, value_name)
    except FileNotFoundError:
        return False
    return True


def enable(
    command: str | None = None,
    value_name: str = VALUE_NAME,
    key_path: str = RUN_KEY_PATH,
) -> str:
    """Включает автозапуск и возвращает записанную команду."""
    _require_windows()
    import winreg

    value = command or startup_command()
    with winreg.CreateKeyEx(
        winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE
    ) as key:
        winreg.SetValueEx(key, value_name, 0, winreg.REG_SZ, value)
    return value


def disable(
    value_name: str = VALUE_NAME,
    key_path: str = RUN_KEY_PATH,
) -> None:
    _require_windows()
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.DeleteValue(key, value_name)
    except FileNotFoundError:
        # Записи нет — отключать нечего.
        pass
