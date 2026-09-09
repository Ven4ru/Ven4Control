import base64
import os
import re
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from ven4control import scheduled_task


def decoded(arguments: list[str]) -> str:
    """Возвращает скрипт из аргументов powershell.exe."""
    index = arguments.index("-EncodedCommand")
    return base64.b64decode(arguments[index + 1]).decode("utf-16-le")


def error_path(script: str) -> str:
    """Путь файла, в который скрипт запишет причину отказа."""
    match = re.search(r"-FilePath '([^']*)'", script)
    assert match is not None
    return match.group(1)


class SplitCommandTests(unittest.TestCase):
    def test_quoted_path_stays_whole(self) -> None:
        self.assertEqual(
            (r"C:\Program Files\Ven4Control\Ven4Control.exe", "--tray"),
            scheduled_task.split_command(
                '"C:\\Program Files\\Ven4Control\\Ven4Control.exe" --tray'
            ),
        )

    def test_sources_command_keeps_all_arguments(self) -> None:
        executable, arguments = scheduled_task.split_command(
            '"C:\\Python\\python.exe" -m ven4control.app --tray'
        )
        self.assertEqual(r"C:\Python\python.exe", executable)
        self.assertEqual("-m ven4control.app --tray", arguments)

    def test_command_without_quotes_is_still_split(self) -> None:
        self.assertEqual(
            ("app.exe", "--tray"), scheduled_task.split_command("app.exe --tray")
        )

    def test_command_without_arguments(self) -> None:
        self.assertEqual(("app.exe", ""), scheduled_task.split_command('"app.exe"'))


class RegisterScriptTests(unittest.TestCase):
    def _script(self) -> str:
        return scheduled_task.build_register_script(
            '"C:\\Python\\python.exe" -m ven4control.app --tray',
            "DOMAIN\\user",
            "Ven4ControlTest",
        )

    def test_action_splits_executable_and_arguments(self) -> None:
        script = self._script()
        self.assertIn(
            "New-ScheduledTaskAction -Execute 'C:\\Python\\python.exe' "
            "-Argument '-m ven4control.app --tray'",
            script,
        )

    def test_trigger_fires_at_startup(self) -> None:
        """Смысл задачи — подняться до входа пользователя, а не при входе."""
        self.assertIn("New-ScheduledTaskTrigger -AtStartup", self._script())

    def test_principal_uses_s4u_without_password(self) -> None:
        self.assertIn(
            "New-ScheduledTaskPrincipal -UserId 'DOMAIN\\user' "
            "-LogonType S4U -RunLevel Limited",
            self._script(),
        )

    def test_settings_survive_batteries_and_missed_start(self) -> None:
        script = self._script()
        for option in (
            "-AllowStartIfOnBatteries",
            "-DontStopIfGoingOnBatteries",
            "-StartWhenAvailable",
        ):
            with self.subTest(option=option):
                self.assertIn(option, script)

    def test_task_is_registered_by_name(self) -> None:
        self.assertIn(
            "Register-ScheduledTask -TaskName 'Ven4ControlTest'", self._script()
        )

    def test_errors_are_terminating(self) -> None:
        """Иначе PowerShell вернул бы ноль при неудачной регистрации."""
        self.assertTrue(self._script().startswith("$ErrorActionPreference = 'Stop'"))

    def test_apostrophe_in_path_is_escaped(self) -> None:
        script = scheduled_task.build_register_script(
            "\"C:\\O'Brien\\app.exe\" --tray", "user", "Ven4ControlTest"
        )
        self.assertIn("-Execute 'C:\\O''Brien\\app.exe'", script)


class UnregisterScriptTests(unittest.TestCase):
    def test_missing_task_is_not_an_error(self) -> None:
        script = scheduled_task.build_unregister_script("Ven4ControlTest")
        self.assertIn(
            "Get-ScheduledTask -TaskName 'Ven4ControlTest' "
            "-ErrorAction SilentlyContinue",
            script,
        )
        self.assertIn(
            "Unregister-ScheduledTask -TaskName 'Ven4ControlTest' -Confirm:$false",
            script,
        )


class ArgumentsTests(unittest.TestCase):
    def test_script_travels_as_utf16_base64(self) -> None:
        arguments = scheduled_task.build_arguments("Write-Output 'кавычки \"и\"'")
        self.assertIn("-NoProfile", arguments)
        self.assertIn("-NonInteractive", arguments)
        self.assertEqual("Write-Output 'кавычки \"и\"'", decoded(arguments))


class ParseStateTests(unittest.TestCase):
    def test_marker_one_means_registered(self) -> None:
        self.assertTrue(scheduled_task.parse_state("TASK=1"))

    def test_marker_zero_means_absent(self) -> None:
        self.assertFalse(scheduled_task.parse_state("TASK=0"))

    def test_marker_is_found_among_other_output(self) -> None:
        self.assertTrue(scheduled_task.parse_state("предупреждение\nTASK=1\n"))

    def test_empty_output_means_absent(self) -> None:
        self.assertFalse(scheduled_task.parse_state(""))


class IsEnabledTests(unittest.TestCase):
    def _run(self, **kwargs: object) -> mock.MagicMock:
        completed = mock.MagicMock()
        completed.returncode = kwargs.get("returncode", 0)
        completed.stdout = kwargs.get("stdout", "")
        return completed

    def test_powershell_is_asked_about_the_task(self) -> None:
        with mock.patch.object(scheduled_task, "is_supported", return_value=True), \
                mock.patch.object(
                    subprocess, "run", return_value=self._run(stdout="TASK=1")
                ) as run:
            self.assertTrue(scheduled_task.is_enabled("Ven4ControlTest"))

        arguments = run.call_args.args[0]
        self.assertEqual(scheduled_task.POWERSHELL, arguments[0])
        script = decoded(arguments)
        self.assertIn("Get-ScheduledTask -TaskName 'Ven4ControlTest'", script)
        # Проверка состояния делается без прав администратора.
        self.assertNotIn("runas", script)

    def test_powershell_is_taken_from_system32(self) -> None:
        """Имя без пути Windows ищет и в рабочем каталоге процесса.

        Задача регистрируется с правами администратора, поэтому подложенный
        рядом с `Ven4Control.exe` файл `powershell.exe` выполнился бы под уже
        подтверждённым пользователем UAC.
        """
        self.assertTrue(os.path.isabs(scheduled_task.POWERSHELL))
        self.assertTrue(
            scheduled_task.POWERSHELL.lower().endswith(
                "system32\\windowspowershell\\v1.0\\powershell.exe"
            ),
            scheduled_task.POWERSHELL,
        )

    def test_absent_task_reads_as_disabled(self) -> None:
        with mock.patch.object(scheduled_task, "is_supported", return_value=True), \
                mock.patch.object(
                    subprocess, "run", return_value=self._run(stdout="TASK=0")
                ):
            self.assertFalse(scheduled_task.is_enabled("Ven4ControlTest"))

    def test_failed_powershell_does_not_raise(self) -> None:
        """Состояние спрашивается при каждом открытии меню трея."""
        with mock.patch.object(scheduled_task, "is_supported", return_value=True), \
                mock.patch.object(
                    subprocess, "run", return_value=self._run(returncode=1)
                ):
            self.assertFalse(scheduled_task.is_enabled("Ven4ControlTest"))

    def test_missing_powershell_does_not_raise(self) -> None:
        with mock.patch.object(scheduled_task, "is_supported", return_value=True), \
                mock.patch.object(subprocess, "run", side_effect=FileNotFoundError):
            self.assertFalse(scheduled_task.is_enabled("Ven4ControlTest"))

    def test_timeout_does_not_raise(self) -> None:
        with mock.patch.object(scheduled_task, "is_supported", return_value=True), \
                mock.patch.object(
                    subprocess,
                    "run",
                    side_effect=subprocess.TimeoutExpired("powershell", 30),
                ):
            self.assertFalse(scheduled_task.is_enabled("Ven4ControlTest"))

    def test_other_systems_have_no_task(self) -> None:
        with mock.patch.object(scheduled_task, "is_supported", return_value=False), \
                mock.patch.object(subprocess, "run") as run:
            self.assertFalse(scheduled_task.is_enabled("Ven4ControlTest"))
        run.assert_not_called()


class EnableDisableTests(unittest.TestCase):
    """Живой UAC юнит-тестом не проверить: проверяется всё до и после него."""

    def test_enable_reuses_autostart_command(self) -> None:
        with mock.patch.object(scheduled_task, "is_supported", return_value=True), \
                mock.patch.object(
                    scheduled_task, "current_user", return_value="DOMAIN\\user"
                ), \
                mock.patch.object(scheduled_task, "_run_elevated") as elevated:
            scheduled_task.enable(
                '"C:\\Python\\python.exe" -m ven4control.app --tray',
                "Ven4ControlTest",
            )

        script = decoded(elevated.call_args.args[0])
        self.assertIn("Register-ScheduledTask -TaskName 'Ven4ControlTest'", script)
        self.assertIn("-Argument '-m ven4control.app --tray'", script)

    def test_enable_takes_command_from_autostart_by_default(self) -> None:
        with mock.patch.object(scheduled_task, "is_supported", return_value=True), \
                mock.patch.object(
                    scheduled_task, "current_user", return_value="user"
                ), \
                mock.patch.object(
                    scheduled_task.autostart,
                    "startup_command",
                    return_value='"app.exe" --tray',
                ) as startup, \
                mock.patch.object(scheduled_task, "_run_elevated"):
            scheduled_task.enable(task_name="Ven4ControlTest")
        startup.assert_called_once()

    def test_disable_removes_the_task(self) -> None:
        with mock.patch.object(scheduled_task, "is_supported", return_value=True), \
                mock.patch.object(scheduled_task, "_run_elevated") as elevated:
            scheduled_task.disable("Ven4ControlTest")

        script = decoded(elevated.call_args.args[0])
        self.assertIn("Unregister-ScheduledTask -TaskName 'Ven4ControlTest'", script)

    def test_refused_elevation_is_reported(self) -> None:
        with mock.patch.object(scheduled_task, "is_supported", return_value=True), \
                mock.patch.object(
                    scheduled_task,
                    "_run_elevated",
                    side_effect=OSError("Запрос прав администратора отклонён"),
                ):
            with self.assertRaises(OSError):
                scheduled_task.enable("\"app.exe\" --tray", "Ven4ControlTest")

    def test_nonzero_exit_code_is_reported(self) -> None:
        with mock.patch.object(scheduled_task, "is_supported", return_value=True), \
                mock.patch.object(
                    scheduled_task,
                    "_run_elevated",
                    side_effect=OSError("PowerShell завершился с кодом 1"),
                ):
            with self.assertRaises(OSError):
                scheduled_task.disable("Ven4ControlTest")

    def test_other_systems_refuse_to_register(self) -> None:
        with mock.patch.object(scheduled_task, "is_supported", return_value=False), \
                mock.patch.object(scheduled_task, "_run_elevated") as elevated:
            with self.assertRaises(RuntimeError):
                scheduled_task.enable(task_name="Ven4ControlTest")
            with self.assertRaises(RuntimeError):
                scheduled_task.disable("Ven4ControlTest")
        elevated.assert_not_called()


class ErrorReportTests(unittest.TestCase):
    """Повышенный процесс не отдаёт вывод родителю: причина приходит файлом."""

    REASON = (
        "Нет сопоставления между именами учётных записей и кодами "
        "безопасности. (0x80070534)"
    )

    def test_script_writes_the_reason_to_the_named_file(self) -> None:
        script = scheduled_task.build_error_report(
            "Register-ScheduledTask", r"C:\Temp\ошибка.txt"
        )
        self.assertIn("try {", script)
        self.assertIn("Register-ScheduledTask", script)
        self.assertIn(
            "$_.Exception.Message | Out-File -FilePath 'C:\\Temp\\ошибка.txt'",
            script,
        )
        self.assertIn("exit 1", script)

    def _failing_run(self, reason: str, seen: list[str]):
        def run(arguments: list[str]) -> None:
            path = error_path(decoded(arguments))
            seen.append(path)
            if reason:
                Path(path).write_text(reason, encoding="utf-8-sig")
            raise OSError("PowerShell завершился с кодом 1")

        return run

    def _enable_with_failure(self, reason: str) -> tuple[str, list[str]]:
        seen: list[str] = []
        with mock.patch.object(scheduled_task, "is_supported", return_value=True), \
                mock.patch.object(
                    scheduled_task, "current_user", return_value="PC\\user"
                ), \
                mock.patch.object(
                    scheduled_task,
                    "_run_elevated",
                    side_effect=self._failing_run(reason, seen),
                ):
            with self.assertRaises(OSError) as raised:
                scheduled_task.enable('"app.exe" --tray', "Ven4ControlTest")
        return str(raised.exception), seen

    def test_reason_reaches_the_user(self) -> None:
        message, _ = self._enable_with_failure(self.REASON)
        self.assertIn(self.REASON, message)
        self.assertIn("кодом 1", message)

    def test_disable_reports_the_reason_too(self) -> None:
        seen: list[str] = []
        with mock.patch.object(scheduled_task, "is_supported", return_value=True), \
                mock.patch.object(
                    scheduled_task,
                    "_run_elevated",
                    side_effect=self._failing_run("Отказано в доступе", seen),
                ):
            with self.assertRaises(OSError) as raised:
                scheduled_task.disable("Ven4ControlTest")
        self.assertIn("Отказано в доступе", str(raised.exception))

    def test_empty_report_keeps_the_original_message(self) -> None:
        message, _ = self._enable_with_failure("")
        self.assertEqual("PowerShell завершился с кодом 1", message)

    def test_temporary_file_does_not_stay_behind(self) -> None:
        _, seen = self._enable_with_failure(self.REASON)
        self.assertFalse(Path(seen[0]).exists())


class CurrentUserTests(unittest.TestCase):
    """Регистрация задачи идёт от SID, а не от составного имени.

    Неправильная область отвечает 0x80070534 — «нет сопоставления имени с
    SID». Составное имя `КОМПЬЮТЕР\\пользователь` подходит только вне домена:
    на доменной машине с доменным входом оно ищется лишь в локальной SAM.
    """

    SID = "S-1-5-21-2052111302-1275210071-1801674531-1105"

    def test_user_is_the_sid(self) -> None:
        with mock.patch.dict(
            "os.environ",
            {"COMPUTERNAME": "DESKTOP-P1097DP", "USERNAME": "venchwork"},
            clear=True,
        ), mock.patch.object(
            scheduled_task, "current_user_sid", return_value=self.SID
        ):
            user = scheduled_task.current_user()
        self.assertEqual(self.SID, user)
        self.assertNotIn("DESKTOP-P1097DP", user)

    def test_registration_script_gets_the_sid(self) -> None:
        with mock.patch.object(scheduled_task, "is_supported", return_value=True), \
                mock.patch.object(
                    scheduled_task, "current_user_sid", return_value=self.SID
                ), \
                mock.patch.object(scheduled_task, "_run_elevated") as elevated:
            scheduled_task.enable('"app.exe" --tray', "Ven4ControlTest")

        script = decoded(elevated.call_args.args[0])
        self.assertIn(f"New-ScheduledTaskPrincipal -UserId '{self.SID}'", script)


if __name__ == "__main__":
    unittest.main()
