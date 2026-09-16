import os
import subprocess
import tempfile
import unittest
from unittest import mock

from ven4control import windows_identity


class SystemPathTests(unittest.TestCase):
    """Системные программы вызываются по абсолютному пути.

    Имя без пути Windows ищет в том числе в рабочем каталоге процесса. Файл
    `powershell.exe`, случайно оказавшийся рядом с `Ven4Control.exe` в
    «Загрузках», выполнился бы с правами администратора — UAC пользователь уже
    подтвердил, а подтверждал он не это.
    """

    def test_path_is_built_from_system_root(self) -> None:
        with mock.patch.dict(
            "os.environ", {"SystemRoot": "D:\\Windows"}, clear=True
        ):
            self.assertEqual(
                os.path.join("D:\\Windows", "System32", "icacls.exe"),
                windows_identity.system32_path("icacls.exe"),
            )

    @unittest.skipUnless(os.name == "nt", "Проверка пути предназначена для Windows")
    def test_missing_system_root_falls_back_to_the_default(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True):
            path = windows_identity.system32_path("whoami.exe")
        self.assertTrue(path.lower().endswith("system32\\whoami.exe"), path)
        self.assertTrue(os.path.isabs(path), path)


class CurrentUserSidTests(unittest.TestCase):
    """Учётная запись определяется через SID, а не через составное имя.

    `КОМПЬЮТЕР\\пользователь` резолвится только вне домена: на машине в домене
    с доменным входом LSA ищет такое имя лишь в локальной SAM и отвечает «нет
    сопоставления имени с SID» — падали и `icacls`, и регистрация задачи.
    """

    LOCAL = '"desktop-7hb86m6\\vench","S-1-5-21-1689690341-478343966-1537186535-1001"\n'
    DOMAIN = '"CONTOSO\\jdoe","S-1-5-21-2052111302-1275210071-1801674531-1105"\n'

    def _whoami(self, stdout: str, returncode: int = 0):
        def run(args, **kwargs):
            self.seen = args
            return subprocess.CompletedProcess(args, returncode, stdout, "")

        return run

    def test_local_account_sid_is_parsed(self) -> None:
        with mock.patch.object(
            windows_identity.subprocess, "run", side_effect=self._whoami(self.LOCAL)
        ):
            self.assertEqual(
                "S-1-5-21-1689690341-478343966-1537186535-1001",
                windows_identity.current_user_sid(),
            )
        self.assertIn("/fo", self.seen)
        self.assertIn("csv", self.seen)

    def test_domain_account_sid_is_parsed(self) -> None:
        with mock.patch.object(
            windows_identity.subprocess, "run", side_effect=self._whoami(self.DOMAIN)
        ):
            self.assertEqual(
                "S-1-5-21-2052111302-1275210071-1801674531-1105",
                windows_identity.current_user_sid(),
            )

    def test_computer_name_is_never_used(self) -> None:
        with mock.patch.dict(
            "os.environ",
            {"COMPUTERNAME": "DESKTOP-P1097DP", "USERNAME": "venchwork"},
            clear=True,
        ), mock.patch.object(
            windows_identity.subprocess, "run", side_effect=self._whoami(self.DOMAIN)
        ):
            sid = windows_identity.current_user_sid()
        self.assertNotIn("DESKTOP-P1097DP", sid)
        self.assertNotIn("\\", sid)

    def test_failed_whoami_is_reported(self) -> None:
        with mock.patch.object(
            windows_identity.subprocess,
            "run",
            side_effect=self._whoami("Отказано в доступе", returncode=1),
        ), self.assertRaises(RuntimeError):
            windows_identity.current_user_sid()

    def test_answer_without_sid_is_reported(self) -> None:
        with mock.patch.object(
            windows_identity.subprocess, "run", side_effect=self._whoami("\n")
        ), self.assertRaises(RuntimeError):
            windows_identity.current_user_sid()

    def test_missing_whoami_is_reported(self) -> None:
        with mock.patch.object(
            windows_identity.subprocess, "run", side_effect=FileNotFoundError
        ), self.assertRaises(RuntimeError):
            windows_identity.current_user_sid()

    @unittest.skipUnless(os.name == "nt", "Проверка предназначена для Windows")
    def test_live_windows_answers_with_a_sid(self) -> None:
        self.assertRegex(windows_identity.current_user_sid(), r"^S-1-5-21-[\d-]+$")


class ResolveProgramTests(unittest.TestCase):
    """Внешние программы ищутся мимо каталога процесса и текущего каталога.

    Тот же класс проблемы, что и у системных программ выше, но для `ssh`,
    `mstsc`, `wt` и `tailscale`: подделка, положенная рядом с портативным
    `Ven4Control.exe`, получала бы адрес устройства, путь к приватному ключу
    или живой локальный конец RDP-туннеля.
    """

    def test_known_location_wins_over_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            preferred = os.path.join(directory, "tool.exe")
            open(preferred, "wb").close()
            with mock.patch.dict("os.environ", {"PATH": ""}, clear=True):
                self.assertEqual(
                    preferred, windows_identity.resolve_program("tool.exe", preferred)
                )

    def test_process_directory_is_never_searched(self) -> None:
        """Каталог рядом с EXE исключается, даже если он есть в PATH."""
        with tempfile.TemporaryDirectory() as directory:
            planted = os.path.join(directory, "tool.exe")
            open(planted, "wb").close()
            fake_exe = os.path.join(directory, "Ven4Control.exe")
            with mock.patch.dict("os.environ", {"PATH": directory}, clear=True), \
                    mock.patch.object(windows_identity.sys, "executable", fake_exe):
                self.assertIsNone(windows_identity.resolve_program("tool.exe"))

    def test_current_directory_is_never_searched(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            planted = os.path.join(directory, "tool.exe")
            open(planted, "wb").close()
            with mock.patch.dict("os.environ", {"PATH": directory}, clear=True), \
                    mock.patch.object(os, "getcwd", return_value=directory):
                self.assertIsNone(windows_identity.resolve_program("tool.exe"))

    def test_unrelated_path_entry_is_used(self) -> None:
        """Обычный каталог из PATH по-прежнему годится: совместимость сохранена."""
        with tempfile.TemporaryDirectory() as directory:
            planted = os.path.join(directory, "tool.exe")
            open(planted, "wb").close()
            with mock.patch.dict("os.environ", {"PATH": directory}, clear=True), \
                    mock.patch.object(os, "getcwd", return_value=os.path.sep), \
                    mock.patch.object(
                        windows_identity.sys, "executable", "C:\\python\\python.exe"
                    ):
                self.assertEqual(planted, windows_identity.resolve_program("tool.exe"))

    def test_missing_program_returns_none(self) -> None:
        with mock.patch.dict("os.environ", {"PATH": ""}, clear=True):
            self.assertIsNone(windows_identity.resolve_program("нет-такой.exe"))


if __name__ == "__main__":
    unittest.main()
