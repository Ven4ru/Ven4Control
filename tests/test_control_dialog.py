import unittest

from ven4control.control_dialog import safe_local_name, ven4tools_confirmation
from ven4control.remote_control import VEN4TOOLS_INSTALL_PATH, VEN4TOOLS_REPO


class Ven4ToolsConfirmationTests(unittest.TestCase):
    """Установка чужого кода на устройство требует осознанного согласия."""

    def _text(self) -> str:
        return ven4tools_confirmation("Домашний ПК")

    def test_device_is_named(self) -> None:
        self.assertIn("Домашний ПК", self._text())

    def test_separate_product_is_stated(self) -> None:
        text = self._text()
        self.assertIn("отдельн", text)
        self.assertIn("Ven4Tools", text)

    def test_source_and_destination_are_stated(self) -> None:
        text = self._text()
        self.assertIn(VEN4TOOLS_REPO, text)
        self.assertIn(VEN4TOOLS_INSTALL_PATH, text)

    def test_integrity_check_is_mentioned(self) -> None:
        self.assertIn("контрольной сумм", self._text())


class SafeLocalNameTests(unittest.TestCase):
    """Имя файла из SFTP-листинга не должно уводить диалог сохранения."""

    def test_plain_name_is_unchanged(self) -> None:
        self.assertEqual("config.tar.gz", safe_local_name("config.tar.gz"))

    def test_windows_absolute_path_becomes_a_bare_name(self) -> None:
        self.assertEqual(
            "hosts", safe_local_name("C:\\Windows\\System32\\drivers\\etc\\hosts")
        )

    def test_windows_parent_traversal_is_stripped(self) -> None:
        self.assertEqual("evil.exe", safe_local_name("..\\..\\Startup\\evil.exe"))

    def test_posix_path_becomes_a_bare_name(self) -> None:
        self.assertEqual("passwd", safe_local_name("/etc/passwd"))

    def test_posix_parent_traversal_is_stripped(self) -> None:
        self.assertEqual("evil.sh", safe_local_name("../../evil.sh"))

    def test_only_dots_fall_back_to_a_safe_name(self) -> None:
        for name in ("..", ".", "...", ""):
            with self.subTest(name=name):
                self.assertEqual("файл", safe_local_name(name))

    def test_leading_dot_is_dropped(self) -> None:
        # Скрытое имя вроде `.bashrc` останется читаемым, а не начнётся
        # с точки, из-за которой файл потеряется в проводнике.
        self.assertEqual("bashrc", safe_local_name(".bashrc"))

    def test_trailing_separator_falls_back(self) -> None:
        self.assertEqual("файл", safe_local_name("/etc/"))


if __name__ == "__main__":
    unittest.main()
