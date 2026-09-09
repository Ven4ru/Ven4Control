import os
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


if __name__ == "__main__":
    unittest.main()
