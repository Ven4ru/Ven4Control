"""Работа в фоне до входа пользователя через планировщик Windows.

Опция сверх автозапуска из `autostart.py`, а не замена ему: запись в
`HKCU\\...\\Run` срабатывает только при входе в систему, поэтому ночная
перезагрузка без входа оставляла бы фоновое логирование выключенным — как
раз в тот момент, ради которого оно и ведётся.

Планировщик вызывается через `powershell.exe`: pywin32/COM тянуть в сборку
ради четырёх команд не нужно.
"""
from __future__ import annotations

import base64
import os
import subprocess
import tempfile
from pathlib import Path

from ven4control import autostart
from ven4control.windows_identity import current_user_principal


TASK_NAME = "Ven4Control-Background"

# Метка, по которой ответ узнаётся среди прочего вывода PowerShell.
STATE_MARKER = "TASK"

# Задача регистрируется от имени текущего пользователя с S4U вместо
# сохранённого пароля: хранить нечего, при смене пароля входа ничего не
# протухает, а профиль всё равно загружается — Credential Manager с
# SSH-ключами доступен.
PRINCIPAL_OPTIONS = "-LogonType S4U -RunLevel Limited"

# StartWhenAvailable нужен ноутбуку: выключенная машина пропустит запуск, и
# задача должна наверстать его при следующей загрузке, а не потеряться.
SETTINGS_OPTIONS = (
    "-AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable"
)

POWERSHELL = "powershell.exe"

STATE_TIMEOUT = 30
ELEVATED_TIMEOUT_MS = 120_000

# Windows возвращает этот код, когда пользователь закрыл запрос UAC.
ERROR_CANCELLED = 1223


def is_supported() -> bool:
    return os.name == "nt"


def _require_windows() -> None:
    if not is_supported():
        raise RuntimeError("Планировщик задач настраивается только в Windows")


def current_user() -> str:
    """Имя текущего пользователя в виде, понятном планировщику.

    Регистрация задачи от неправильной области отвечает 0x80070534 — «нет
    сопоставления имени с SID». См. `windows_identity.current_user_principal`.
    """
    return current_user_principal()


def _quoted(value: str) -> str:
    """Строка PowerShell в одинарных кавычках: внутри удваивается только '."""
    return "'" + value.replace("'", "''") + "'"


def split_command(command: str) -> tuple[str, str]:
    """Делит команду запуска на исполняемый файл и аргументы.

    `New-ScheduledTaskAction` принимает их раздельно, а единственный источник
    команды — `autostart.startup_command()`, где путь уже взят в кавычки.
    """
    text = command.strip()
    if text.startswith('"'):
        closing = text.find('"', 1)
        if closing != -1:
            return text[1:closing], text[closing + 1 :].strip()
    executable, _, arguments = text.partition(" ")
    return executable, arguments.strip()


def build_register_script(
    command: str,
    user_id: str,
    task_name: str = TASK_NAME,
) -> str:
    executable, arguments = split_command(command)
    action = f"New-ScheduledTaskAction -Execute {_quoted(executable)}"
    if arguments:
        action += f" -Argument {_quoted(arguments)}"
    return (
        "$ErrorActionPreference = 'Stop'\n"
        f"$action = {action}\n"
        "$trigger = New-ScheduledTaskTrigger -AtStartup\n"
        f"$principal = New-ScheduledTaskPrincipal -UserId {_quoted(user_id)} "
        f"{PRINCIPAL_OPTIONS}\n"
        f"$settings = New-ScheduledTaskSettingsSet {SETTINGS_OPTIONS}\n"
        f"Register-ScheduledTask -TaskName {_quoted(task_name)} -Action $action "
        "-Trigger $trigger -Principal $principal -Settings $settings -Force "
        "| Out-Null\n"
    )


def build_unregister_script(task_name: str = TASK_NAME) -> str:
    name = _quoted(task_name)
    return (
        "$ErrorActionPreference = 'Stop'\n"
        f"if (Get-ScheduledTask -TaskName {name} -ErrorAction SilentlyContinue) "
        f"{{ Unregister-ScheduledTask -TaskName {name} -Confirm:$false }}\n"
    )


def build_state_script(task_name: str = TASK_NAME) -> str:
    return (
        f"if (Get-ScheduledTask -TaskName {_quoted(task_name)} "
        "-ErrorAction SilentlyContinue) "
        f"{{ Write-Output '{STATE_MARKER}=1' }} "
        f"else {{ Write-Output '{STATE_MARKER}=0' }}"
    )


def build_error_report(script: str, error_path: str) -> str:
    """Оборачивает скрипт записью причины отказа в файл.

    Повышенный процесс запускается через ShellExecuteEx и своего вывода
    родителю не отдаёт — это ограничение Windows. Без файла от неудачной
    регистрации оставался бы только код возврата, по которому нечего чинить.
    """
    return (
        "try {\n"
        f"{script}\n"
        "} catch {\n"
        f"$_.Exception.Message | Out-File -FilePath {_quoted(error_path)} "
        "-Encoding utf8\n"
        "exit 1\n"
        "}\n"
    )


def build_arguments(script: str) -> list[str]:
    """Аргументы powershell.exe для готового скрипта.

    Скрипт передаётся через `-EncodedCommand`: base64 от UTF-16LE не требует
    экранирования кавычек и переносов строк, которые иначе пришлось бы
    собирать руками дважды — для subprocess и для командной строки UAC.
    """
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return ["-NoProfile", "-NonInteractive", "-EncodedCommand", encoded]


def parse_state(output: str) -> bool:
    for line in output.splitlines():
        text = line.strip()
        if text.startswith(f"{STATE_MARKER}="):
            return text.split("=", 1)[1].strip() == "1"
    return False


def is_enabled(task_name: str = TASK_NAME) -> bool:
    """Есть ли задача в планировщике. Прав администратора не требует."""
    if not is_supported():
        return False
    try:
        result = subprocess.run(
            [POWERSHELL, *build_arguments(build_state_script(task_name))],
            capture_output=True,
            text=True,
            encoding="utf-8",
            # PowerShell пишет в поток ошибок служебный XML прогресса в
            # кодировке консоли: без замены байтов разбор ответа падал бы на
            # русских буквах, хотя нужная строка — чистый ASCII.
            errors="replace",
            timeout=STATE_TIMEOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        # Состояние спрашивается при каждом открытии меню трея: сбой проверки
        # не повод показывать ошибку, задача просто считается невключённой.
        return False
    if result.returncode != 0:
        return False
    return parse_state(result.stdout or "")


def _run_elevated(arguments: list[str]) -> None:
    """Запускает PowerShell с правами администратора и ждёт его завершения.

    ShellExecuteEx вместо subprocess: только он показывает запрос UAC.
    Флаг SEE_MASK_NOCLOSEPROCESS оставляет дескриптор процесса, без него
    нельзя было бы отличить успешную регистрацию от ошибки PowerShell.
    """
    import ctypes
    from ctypes import wintypes

    class ShellExecuteInfo(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("fMask", ctypes.c_ulong),
            ("hwnd", wintypes.HWND),
            ("lpVerb", wintypes.LPCWSTR),
            ("lpFile", wintypes.LPCWSTR),
            ("lpParameters", wintypes.LPCWSTR),
            ("lpDirectory", wintypes.LPCWSTR),
            ("nShow", ctypes.c_int),
            ("hInstApp", wintypes.HINSTANCE),
            ("lpIDList", ctypes.c_void_p),
            ("lpClass", wintypes.LPCWSTR),
            ("hkeyClass", wintypes.HKEY),
            ("dwHotKey", wintypes.DWORD),
            ("hIcon", wintypes.HANDLE),
            ("hProcess", wintypes.HANDLE),
        ]

    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    info = ShellExecuteInfo()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = 0x00000040 | 0x00000100  # NOCLOSEPROCESS | NOASYNC
    info.lpVerb = "runas"
    info.lpFile = POWERSHELL
    info.lpParameters = subprocess.list2cmdline(arguments)
    info.nShow = 0  # SW_HIDE: окно консоли пользователю ни к чему.

    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        code = ctypes.get_last_error()
        if code == ERROR_CANCELLED:
            raise OSError("Запрос прав администратора отклонён")
        raise OSError(f"Не удалось запустить PowerShell (код {code})")

    handle = info.hProcess
    try:
        if kernel32.WaitForSingleObject(handle, ELEVATED_TIMEOUT_MS) != 0:
            raise OSError("PowerShell не завершил настройку задачи вовремя")
        status = wintypes.DWORD()
        kernel32.GetExitCodeProcess(handle, ctypes.byref(status))
        if status.value != 0:
            raise OSError(f"PowerShell завершился с кодом {status.value}")
    finally:
        kernel32.CloseHandle(handle)


def _reported_error(path: str) -> str:
    try:
        # Out-File в Windows PowerShell пишет UTF-8 с меткой порядка байтов.
        text = Path(path).read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return ""
    return " ".join(text.split())


def _run_with_report(script: str) -> None:
    """Выполняет скрипт с правами администратора, сохраняя причину отказа."""
    handle, error_path = tempfile.mkstemp(prefix="ven4control-task-", suffix=".txt")
    os.close(handle)
    try:
        try:
            _run_elevated(build_arguments(build_error_report(script, error_path)))
        except OSError as error:
            detail = _reported_error(error_path)
            if detail:
                raise OSError(f"{error}. {detail}") from error
            raise
    finally:
        try:
            os.unlink(error_path)
        except OSError:
            # Файл создан повышенным процессом: неудача уборки не повод
            # сообщать об ошибке настройки задачи.
            pass


def enable(command: str | None = None, task_name: str = TASK_NAME) -> None:
    """Регистрирует задачу. Требует одного подтверждения UAC."""
    _require_windows()
    script = build_register_script(
        command or autostart.startup_command(), current_user(), task_name
    )
    _run_with_report(script)


def disable(task_name: str = TASK_NAME) -> None:
    _require_windows()
    _run_with_report(build_unregister_script(task_name))
