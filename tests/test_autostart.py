import os
import unittest

from ven4control import autostart


class StartupCommandTests(unittest.TestCase):
    def test_packed_application_starts_itself_in_tray(self) -> None:
        self.assertEqual(
            '"C:\\Program Files\\Ven4Control\\Ven4Control.exe" --tray',
            autostart.startup_command(
                r"C:\Program Files\Ven4Control\Ven4Control.exe", frozen=True
            ),
        )

    def test_sources_are_started_through_the_module(self) -> None:
        """Один только путь к python.exe открыл бы интерпретатор, а не окно."""
        command = autostart.startup_command(r"C:\Python\python.exe", frozen=False)
        self.assertEqual(
            '"C:\\Python\\python.exe" -m ven4control.app --tray', command
        )

    def test_startup_never_opens_the_window(self) -> None:
        """Вход в систему не должен разворачивать окно приложения."""
        for frozen in (True, False):
            with self.subTest(frozen=frozen):
                self.assertTrue(
                    autostart.startup_command("app.exe", frozen=frozen).endswith(
                        autostart.TRAY_ARGUMENT
                    )
                )

    def test_path_with_spaces_stays_quoted(self) -> None:
        command = autostart.startup_command(r"C:\Мои файлы\python.exe", frozen=False)
        self.assertTrue(command.startswith('"C:\\Мои файлы\\python.exe"'))


@unittest.skipUnless(os.name == "nt", "Автозапуск настраивается только в Windows")
class RegistryTests(unittest.TestCase):
    KEY_PATH = r"Software\Ven4Control\AutostartTests"
    VALUE_NAME = "Ven4ControlTest"

    def tearDown(self) -> None:
        import winreg

        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, self.KEY_PATH)
        except FileNotFoundError:
            pass

    def test_enable_and_disable_are_visible_in_registry(self) -> None:
        self.assertFalse(autostart.is_enabled(self.VALUE_NAME, self.KEY_PATH))

        written = autostart.enable("command", self.VALUE_NAME, self.KEY_PATH)

        self.assertEqual("command", written)
        self.assertTrue(autostart.is_enabled(self.VALUE_NAME, self.KEY_PATH))

        autostart.disable(self.VALUE_NAME, self.KEY_PATH)

        self.assertFalse(autostart.is_enabled(self.VALUE_NAME, self.KEY_PATH))

    def test_repeated_disable_is_harmless(self) -> None:
        autostart.disable(self.VALUE_NAME, self.KEY_PATH)
        autostart.disable(self.VALUE_NAME, self.KEY_PATH)

    def test_enable_replaces_previous_command(self) -> None:
        autostart.enable("первая", self.VALUE_NAME, self.KEY_PATH)
        autostart.enable("вторая", self.VALUE_NAME, self.KEY_PATH)

        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.KEY_PATH) as key:
            value, _ = winreg.QueryValueEx(key, self.VALUE_NAME)
        self.assertEqual("вторая", value)


if __name__ == "__main__":
    unittest.main()
