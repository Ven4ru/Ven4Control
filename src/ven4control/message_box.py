"""Диалоги для текста, пришедшего с устройства.

`QMessageBox` по умолчанию сам решает, разметка перед ним или нет, и
отрисовывает HTML. Вывод команды и текст ошибки SSH приходят с устройства,
поэтому скомпрометированное устройство нарисовало бы в модальном окне
поддельную ссылку или чужое сообщение. Выполнить код так нельзя — Qt не
подгружает в `QMessageBox` внешние ресурсы, — но обмануть человека можно,
поэтому такой текст показывается только как обычный текст.

Диалоги с текстом самого приложения по-прежнему вызывают `QMessageBox`
напрямую: там подменять нечего.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox, QWidget


def show_device_message(
    parent: QWidget | None,
    icon: QMessageBox.Icon,
    title: str,
    text: str,
) -> None:
    box = QMessageBox(parent)
    box.setIcon(icon)
    box.setWindowTitle(title)
    box.setTextFormat(Qt.TextFormat.PlainText)
    box.setText(text)
    box.exec()


def device_information(parent: QWidget | None, title: str, text: str) -> None:
    show_device_message(parent, QMessageBox.Icon.Information, title, text)


def device_warning(parent: QWidget | None, title: str, text: str) -> None:
    show_device_message(parent, QMessageBox.Icon.Warning, title, text)


def device_critical(parent: QWidget | None, title: str, text: str) -> None:
    show_device_message(parent, QMessageBox.Icon.Critical, title, text)
