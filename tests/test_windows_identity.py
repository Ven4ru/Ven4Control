import os
import subprocess
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


if __name__ == "__main__":
    unittest.main()
