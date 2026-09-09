"""Учётная запись Windows и пути к системным программам.

Пользователь передаётся `icacls` и планировщику задач в виде SID. Строковое
имя резолвится не везде: `WORKGROUP\\пользователь` не сопоставляется с
учётной записью вне домена, а `КОМПЬЮТЕР\\пользователь` — на машине в домене,
где вход сделан доменной учёткой (LSA ищет такое имя только в локальной SAM).
Обе ошибки одинаковые: «нет сопоставления имени с SID», 0x80070534. SID
подходит в любом из этих случаев.
"""
from __future__ import annotations

import csv
import os
import subprocess


WHOAMI_TIMEOUT = 15


def system32_path(*parts: str) -> str:
    """Абсолютный путь к системной программе Windows.

    Имя без пути Windows ищет по стандартному порядку поиска, куда входит и
    рабочий каталог процесса: файл `powershell.exe`, случайно оказавшийся
    рядом с `Ven4Control.exe` в «Загрузках», запустился бы вместо системного —
    и под уже подтверждённым пользователем UAC.
    """
    root = os.environ.get("SystemRoot") or os.environ.get("windir") or "C:\\Windows"
    return os.path.join(root, "System32", *parts)


def current_user_sid() -> str:
    """SID текущего пользователя Windows.

    Берётся у `whoami` в формате CSV: он не зависит от языка системы, в
    отличие от таблицы по умолчанию, где меняются и заголовки, и разметка.
    Отдельная зависимость ради одного вызова (pywin32) не нужна — остальной
    проект точно так же шеллит системные команды.
    """
    try:
        result = subprocess.run(
            [system32_path("whoami.exe"), "/user", "/fo", "csv", "/nh"],
            capture_output=True,
            text=True,
            encoding="oem",
            errors="replace",
            timeout=WHOAMI_TIMEOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError(
            f"Не удалось определить учётную запись Windows: {error}"
        ) from error
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(
            "Не удалось определить учётную запись Windows: "
            f"{detail or 'whoami завершился с ошибкой'}"
        )
    return parse_user_sid(result.stdout)


def parse_user_sid(output: str) -> str:
    """Достаёт SID из ответа `whoami /user /fo csv /nh`.

    Ответ выглядит как `"КОМПЬЮТЕР\\пользователь","S-1-5-21-…-1001"`. Ищется
    поле, похожее на SID, а не второе по счёту: разбирать позицию незачем,
    ни имя учётной записи, ни имя компьютера с `S-1-` начаться не могут.
    """
    for row in csv.reader(output.splitlines()):
        for field in row:
            value = field.strip()
            if value.upper().startswith("S-1-"):
                return value
    raise RuntimeError("Windows не сообщила SID текущего пользователя")
