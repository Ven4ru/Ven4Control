"""Учётная запись Windows и пути к системным программам.

Область берётся из имени компьютера, а не из `USERDOMAIN`: на машине вне
домена там лежит имя рабочей группы (`WORKGROUP`), которое ни `icacls`, ни
планировщик задач не сопоставляют с учётной записью. Имя компьютера
совпадает с тем, что показывает `whoami`, и резолвится всегда.
"""
from __future__ import annotations

import os


def system32_path(*parts: str) -> str:
    """Абсолютный путь к системной программе Windows.

    Имя без пути Windows ищет по стандартному порядку поиска, куда входит и
    рабочий каталог процесса: файл `powershell.exe`, случайно оказавшийся
    рядом с `Ven4Control.exe` в «Загрузках», запустился бы вместо системного —
    и под уже подтверждённым пользователем UAC.
    """
    root = os.environ.get("SystemRoot") or os.environ.get("windir") or "C:\\Windows"
    return os.path.join(root, "System32", *parts)


def current_user_principal() -> str:
    name = os.environ.get("USERNAME", "")
    if not name:
        import getpass

        name = getpass.getuser()
    computer = os.environ.get("COMPUTERNAME", "")
    if computer:
        return f"{computer}\\{name}"
    return name
