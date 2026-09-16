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
import sys


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


def _same_dir(left: str, right: str) -> bool:
    try:
        return os.path.normcase(os.path.abspath(left)) == os.path.normcase(
            os.path.abspath(right)
        )
    except OSError:
        return False


def unsafe_search_dirs() -> list[str]:
    """Каталоги, которые нельзя использовать для поиска внешних программ.

    Windows ищет имя без пути начиная с каталога самого процесса и текущего
    каталога. Приложение раздаётся портативным onefile-EXE, который обычно
    запускают прямо из «Загрузок», — то есть оба этих каталога содержат что
    угодно, скачанное пользователем.
    """
    dirs = [os.getcwd(), os.path.dirname(os.path.abspath(sys.executable))]
    bundle = getattr(sys, "_MEIPASS", "")
    if bundle:
        dirs.append(bundle)
    return [d for d in dirs if d]


def resolve_program(name: str, *preferred: str) -> str | None:
    """Абсолютный путь к внешней программе или None, если её нет.

    Сначала проверяются известные места установки, затем PATH, из которого
    исключены каталог процесса и текущий каталог (см. `unsafe_search_dirs`).
    Возврат None означает «программа не найдена» — вызывающий код обязан
    сказать об этом пользователю, а не подставлять голое имя: именно голое
    имя и приводило к запуску файла, лежащего рядом с Ven4Control.exe.
    """
    for candidate in preferred:
        if candidate and os.path.isfile(candidate):
            return candidate
    unsafe = unsafe_search_dirs()
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        entry = entry.strip().strip('"')
        if not entry or any(_same_dir(entry, bad) for bad in unsafe):
            continue
        candidate = os.path.join(entry, name)
        if os.path.isfile(candidate):
            return candidate
    return None


def ssh_path() -> str | None:
    """Путь к клиенту OpenSSH, поставляемому с Windows."""
    return resolve_program("ssh.exe", system32_path("OpenSSH", "ssh.exe"))


def mstsc_path() -> str | None:
    """Путь к клиенту подключения к удалённому рабочему столу."""
    return resolve_program("mstsc.exe", system32_path("mstsc.exe"))


def windows_terminal_path() -> str | None:
    """Путь к Windows Terminal: он ставится из Store в WindowsApps."""
    local = os.environ.get("LOCALAPPDATA", "")
    preferred = (
        os.path.join(local, "Microsoft", "WindowsApps", "wt.exe") if local else ""
    )
    return resolve_program("wt.exe", preferred)


def tailscale_path() -> str | None:
    """Путь к консольному клиенту Tailscale."""
    preferred = [
        os.path.join(root, "Tailscale", "tailscale.exe")
        for root in (
            os.environ.get("ProgramFiles", ""),
            os.environ.get("ProgramFiles(x86)", ""),
        )
        if root
    ]
    return resolve_program("tailscale.exe", *preferred)


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
