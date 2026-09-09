import unittest
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox

from ven4control import app as app_module
from ven4control import control_dialog as control_dialog_module
from ven4control.message_box import (
    device_critical,
    device_information,
    device_warning,
    show_device_message,
)


class RecordingBox:
    """Подмена QMessageBox: показ не нужен, важны icon/title/textFormat."""

    instances: list["RecordingBox"] = []

    Icon = QMessageBox.Icon

    def __init__(self, parent=None) -> None:
        self.parent = parent
        self.icon = None
        self.title = ""
        self.text = ""
        self.text_format = None
        self.executed = False
        RecordingBox.instances.append(self)

    def setIcon(self, icon) -> None:
        self.icon = icon

    def setWindowTitle(self, title: str) -> None:
        self.title = title

    def setText(self, text: str) -> None:
        self.text = text

    def setTextFormat(self, text_format) -> None:
        self.text_format = text_format

    def exec(self) -> None:
        self.executed = True


class DeviceMessageTests(unittest.TestCase):
    def setUp(self) -> None:
        RecordingBox.instances = []
        patcher = patch("ven4control.message_box.QMessageBox", RecordingBox)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _last(self) -> RecordingBox:
        return RecordingBox.instances[-1]

    def test_text_from_a_device_is_shown_as_plain_text(self) -> None:
        show_device_message(
            None,
            QMessageBox.Icon.Warning,
            "Операция завершена",
            "<a href='https://phish.example'>Обновите пароль</a>",
        )
        box = self._last()
        self.assertEqual(Qt.TextFormat.PlainText, box.text_format)
        self.assertIn("<a href=", box.text)
        self.assertTrue(box.executed)

    def test_helpers_pass_their_icons(self) -> None:
        device_information(None, "Готово", "ответ")
        self.assertEqual(QMessageBox.Icon.Information, self._last().icon)
        device_warning(None, "Внимание", "ответ")
        self.assertEqual(QMessageBox.Icon.Warning, self._last().icon)
        device_critical(None, "Ошибка", "ответ")
        self.assertEqual(QMessageBox.Icon.Critical, self._last().icon)

    def test_title_and_text_are_not_swapped(self) -> None:
        device_warning(None, "Заголовок", "Текст с устройства")
        box = self._last()
        self.assertEqual("Заголовок", box.title)
        self.assertEqual("Текст с устройства", box.text)


class DeviceMessageUsageTests(unittest.TestCase):
    """Вывод удалённых операций идёт через безопасный диалог, а не напрямую."""

    def test_app_uses_the_helper_for_remote_output(self) -> None:
        self.assertTrue(hasattr(app_module, "device_warning"))
        self.assertTrue(hasattr(app_module, "device_critical"))
        self.assertTrue(hasattr(app_module, "device_information"))

    def test_control_dialog_uses_the_helper_for_remote_output(self) -> None:
        self.assertTrue(hasattr(control_dialog_module, "device_warning"))
        self.assertTrue(hasattr(control_dialog_module, "device_critical"))
        self.assertTrue(hasattr(control_dialog_module, "device_information"))


if __name__ == "__main__":
    unittest.main()
