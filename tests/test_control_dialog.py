import unittest

from ven4control.control_dialog import ven4tools_confirmation
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


if __name__ == "__main__":
    unittest.main()
