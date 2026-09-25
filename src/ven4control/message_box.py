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

import html

from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor, QTextDocument
from PySide6.QtWidgets import QMessageBox, QTextEdit, QWidget

from ven4control.models import Device


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


def device_heading(device: Device) -> str:
    """Заголовок окна устройства в разметке: имя жирным, адрес рядом.

    Имя при импорте из Tailscale приходит от владельца соседнего устройства
    в tailnet, поэтому всё, кроме собственной разметки, экранируется: иначе
    чужой `<img src=...>` в имени Qt попытался бы загрузить как локальный
    файл, а на Windows — и как путь `\\\\сервер\\ресурс` по сети.
    """
    return (
        f"<b>{html.escape(device.name)}</b> — "
        f"{html.escape(device.username)}@{html.escape(device.host)}:{device.port}"
    )


def append_device_line(view: QTextEdit, text: str) -> None:
    """Дописывает строку с устройства в поле вывода как обычный текст.

    `QTextEdit.append` сам решает, разметка перед ним или нет: строка
    журнала с тегом (например, «Invalid user <img src=...>» от sshd — имя
    пользователя там выбирает любой, кто стучится на порт) отрисовалась бы
    как HTML с загрузкой картинки. Прокрутка ведёт себя как у `append`:
    окно следует за выводом, только если уже было у нижнего края.
    """
    scrollbar = view.verticalScrollBar()
    at_bottom = scrollbar.value() >= scrollbar.maximum()
    append_plain_block(view.document(), text)
    if at_bottom:
        scrollbar.setValue(scrollbar.maximum())


def append_plain_block(document: QTextDocument, text: str) -> None:
    """Новый абзац в конце документа — строго как текст, без разбора разметки."""
    cursor = QTextCursor(document)
    cursor.movePosition(QTextCursor.MoveOperation.End)
    if not document.isEmpty():
        cursor.insertBlock()
    cursor.insertText(text)
