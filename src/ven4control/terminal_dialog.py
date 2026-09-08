"""Окно встроенного терминала: клавиатура, вывод и размер окна.

Виджет держит только представление. Сама оболочка живёт в
`TerminalSession` на служебном потоке, разбор ANSI — в `TerminalScreen`,
поэтому здесь остаются перевод нажатий в последовательности терминала,
отрисовка и передача нового размера окна на устройство.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFont, QFontMetricsF, QKeySequence, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QTextEdit,
    QVBoxLayout,
)

from ven4control.ansi_screen import (
    DEFAULT_BACKGROUND,
    DEFAULT_FOREGROUND,
    TerminalScreen,
    terminal_size,
)
from ven4control.models import Device
from ven4control.terminal_session import (
    STATUS_ACTIVE,
    TerminalSession,
    terminal_status_label,
)


ESC = "\x1b"

# Перерисовка не чаще, чем раз в этот интервал: вывод приходит чанками,
# а перерисовывать документ на каждый чанк дороже, чем показать его на
# три десятка миллисекунд позже.
REPAINT_INTERVAL = 30

# Изменение размера окна отправляется устройству с задержкой: во время
# перетаскивания рамки событие приходит на каждый пиксель.
RESIZE_DELAY = 150

# Сколько строк истории держать в документе.
MAX_HISTORY_BLOCKS = 5000

SPECIAL_KEYS: dict[int, str] = {
    int(Qt.Key.Key_Return): "\r",
    int(Qt.Key.Key_Enter): "\r",
    int(Qt.Key.Key_Backspace): "\x7f",
    int(Qt.Key.Key_Tab): "\t",
    int(Qt.Key.Key_Backtab): f"{ESC}[Z",
    int(Qt.Key.Key_Escape): ESC,
    int(Qt.Key.Key_Up): f"{ESC}[A",
    int(Qt.Key.Key_Down): f"{ESC}[B",
    int(Qt.Key.Key_Right): f"{ESC}[C",
    int(Qt.Key.Key_Left): f"{ESC}[D",
    int(Qt.Key.Key_Home): f"{ESC}[H",
    int(Qt.Key.Key_End): f"{ESC}[F",
    int(Qt.Key.Key_PageUp): f"{ESC}[5~",
    int(Qt.Key.Key_PageDown): f"{ESC}[6~",
    int(Qt.Key.Key_Insert): f"{ESC}[2~",
    int(Qt.Key.Key_Delete): f"{ESC}[3~",
    int(Qt.Key.Key_F1): f"{ESC}OP",
    int(Qt.Key.Key_F2): f"{ESC}OQ",
    int(Qt.Key.Key_F3): f"{ESC}OR",
    int(Qt.Key.Key_F4): f"{ESC}OS",
    int(Qt.Key.Key_F5): f"{ESC}[15~",
    int(Qt.Key.Key_F6): f"{ESC}[17~",
    int(Qt.Key.Key_F7): f"{ESC}[18~",
    int(Qt.Key.Key_F8): f"{ESC}[19~",
    int(Qt.Key.Key_F9): f"{ESC}[20~",
    int(Qt.Key.Key_F10): f"{ESC}[21~",
    int(Qt.Key.Key_F11): f"{ESC}[23~",
    int(Qt.Key.Key_F12): f"{ESC}[24~",
}

# Управляющие символы, которые не выводятся из «Ctrl + буква».
CONTROL_KEYS: dict[int, str] = {
    int(Qt.Key.Key_Space): "\x00",
    int(Qt.Key.Key_BracketLeft): "\x1b",
    int(Qt.Key.Key_Backslash): "\x1c",
    int(Qt.Key.Key_BracketRight): "\x1d",
    int(Qt.Key.Key_AsciiCircum): "\x1e",
    int(Qt.Key.Key_Underscore): "\x1f",
    int(Qt.Key.Key_Minus): "\x1f",
    int(Qt.Key.Key_Backspace): "\x08",
}


def key_sequence(key: int, modifiers, text: str) -> str:
    """Переводит нажатие клавиши в последовательность для устройства.

    Пустая строка означает, что отправлять нечего: нажали только модификатор
    или клавишу, которой у терминала нет соответствия.
    """
    code = int(key)
    control = bool(modifiers & Qt.KeyboardModifier.ControlModifier)
    alt = bool(modifiers & Qt.KeyboardModifier.AltModifier)
    if control:
        if int(Qt.Key.Key_A) <= code <= int(Qt.Key.Key_Z):
            return chr(code - int(Qt.Key.Key_A) + 1)
        sequence = CONTROL_KEYS.get(code)
        if sequence:
            return sequence
    special = SPECIAL_KEYS.get(code)
    if special:
        return special
    if not text or text < " ":
        return ""
    # Alt работает как meta: та же клавиша с префиксом Escape.
    return f"{ESC}{text}" if alt else text


class TerminalView(QTextEdit):
    """Поле вывода терминала, забирающее себе клавиатуру."""

    key_pressed = Signal(str)
    size_changed = Signal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setUndoRedoEnabled(False)
        self.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        font = QFont()
        font.setFamilies(["Cascadia Mono", "Consolas", "Courier New"])
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setFixedPitch(True)
        font.setPointSize(10)
        self.setFont(font)
        self.document().setDefaultFont(font)
        self.setStyleSheet(
            "QTextEdit {"
            f"background-color: {DEFAULT_BACKGROUND};"
            f"color: {DEFAULT_FOREGROUND};"
            "border: none;"
            "}"
        )
        self._live_start = 0
        self._size = (0, 0)
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(RESIZE_DELAY)
        self._resize_timer.timeout.connect(self._announce_size)

    # --- вывод -------------------------------------------------------------

    def update_content(self, history: list[str], live: str) -> None:
        """Дописывает готовые строки и заменяет живую часть экрана."""
        scrollbar = self.verticalScrollBar()
        at_bottom = scrollbar.value() >= scrollbar.maximum() - 4
        cursor = QTextCursor(self.document())
        cursor.setPosition(min(self._live_start, self.document().characterCount() - 1))
        cursor.movePosition(
            QTextCursor.MoveOperation.End, QTextCursor.MoveMode.KeepAnchor
        )
        cursor.removeSelectedText()
        for line in history:
            cursor.insertHtml(line)
            cursor.insertBlock()
        self._live_start = cursor.position()
        if live:
            cursor.insertHtml(live)
        self._trim_history()
        if at_bottom:
            scrollbar.setValue(scrollbar.maximum())

    def _trim_history(self) -> None:
        """Держит документ в разумном размере, не сдвигая живую часть."""
        document = self.document()
        extra = document.blockCount() - MAX_HISTORY_BLOCKS
        if extra <= 0:
            return
        cursor = QTextCursor(document)
        cursor.setPosition(0)
        cursor.movePosition(
            QTextCursor.MoveOperation.NextBlock,
            QTextCursor.MoveMode.KeepAnchor,
            extra,
        )
        removed = cursor.selectionEnd() - cursor.selectionStart()
        cursor.removeSelectedText()
        self._live_start = max(0, self._live_start - removed)

    # --- клавиатура --------------------------------------------------------

    def keyPressEvent(self, event) -> None:
        if event.matches(QKeySequence.StandardKey.Copy) and self.textCursor().hasSelection():
            super().keyPressEvent(event)
            return
        if event.matches(QKeySequence.StandardKey.Paste):
            clipboard = QApplication.clipboard()
            if clipboard is not None:
                self.key_pressed.emit(clipboard.text())
            event.accept()
            return
        sequence = key_sequence(event.key(), event.modifiers(), event.text())
        if not sequence:
            event.ignore()
            return
        self.key_pressed.emit(sequence)
        event.accept()

    # --- размер окна -------------------------------------------------------

    def current_size(self) -> tuple[int, int]:
        metrics = QFontMetricsF(self.font())
        return terminal_size(
            self.viewport().width(),
            self.viewport().height(),
            metrics.horizontalAdvance("M"),
            metrics.height(),
        )

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._resize_timer.start()

    def _announce_size(self) -> None:
        size = self.current_size()
        if size == self._size:
            return
        self._size = size
        self.size_changed.emit(*size)


class TerminalDialog(QDialog):
    """Интерактивная оболочка устройства внутри приложения."""

    def __init__(self, device: Device, credentials: dict[str, str], parent=None):
        super().__init__(parent)
        self.device = device
        self.setWindowTitle(f"Терминал — {device.name}")
        self.resize(900, 560)

        self.view = TerminalView(self)
        self.status = QLabel()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                f"<b>{device.name}</b> — {device.username}@{device.host}:{device.port}"
            )
        )
        layout.addWidget(self.view, 1)
        layout.addWidget(self.status)
        layout.addWidget(buttons)

        self.screen = TerminalScreen()
        self.session = TerminalSession(device, credentials)
        self._started = False
        self._closed = False
        self._repaint = QTimer(self)
        self._repaint.setSingleShot(True)
        self._repaint.setInterval(REPAINT_INTERVAL)
        self._repaint.timeout.connect(self._repaint_view)

        self.view.key_pressed.connect(self.session.send)
        self.view.size_changed.connect(self._size_changed)
        self.session.output_received.connect(self._output)
        self.session.status_changed.connect(self._status_changed)
        self.session.session_finished.connect(self._finished)
        self._show_status()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._started:
            return
        self._started = True
        # Размер известен только после раскладки: до неё виджет нулевой,
        # и устройство получило бы окно по умолчанию вместо настоящего.
        columns, rows = self.view.current_size()
        self.screen.resize(columns, rows)
        self.session.resize(columns, rows)
        try:
            self.session.start()
        except Exception as error:
            self._write_notice(str(error))
            self._show_status()
            return
        self.view.setFocus()

    # --- обмен с сессией ---------------------------------------------------

    def _output(self, text: str) -> None:
        self.screen.feed(text)
        if not self._repaint.isActive():
            self._repaint.start()

    def _repaint_view(self) -> None:
        self.view.update_content(self.screen.take_scrollback(), self.screen.render_screen())

    def _size_changed(self, columns: int, rows: int) -> None:
        self.screen.resize(columns, rows)
        self.session.resize(columns, rows)
        self._repaint_view()

    def _status_changed(self, _status: str) -> None:
        self._show_status()

    def _finished(self, message: str) -> None:
        self._write_notice(message)
        self._show_status()

    def _write_notice(self, message: str) -> None:
        """Пишет сообщение приложения в поток вывода терминала."""
        self.screen.feed(f"\r\n=== {message} ===\r\n")
        self._repaint_view()

    def _show_status(self) -> None:
        label = terminal_status_label(self.session.status)
        if self.session.status == STATUS_ACTIVE:
            label += ". Ctrl+C уходит на устройство, Ctrl+V вставляет из буфера"
        self.status.setText(f"Состояние: {label}")

    # --- закрытие ----------------------------------------------------------

    def reject(self) -> None:
        self._close_session()
        super().reject()

    def closeEvent(self, event) -> None:
        self._close_session()
        super().closeEvent(event)

    def _close_session(self) -> None:
        """Оболочка не должна пережить окно: сессию закрываем полностью."""
        # Закрытие окна приходит дважды — событием и через `reject`, а повторная
        # отписка от сигналов уже отписанного диалога только сыплет warning.
        if self._closed:
            return
        self._closed = True
        self._repaint.stop()
        self.session.session_finished.disconnect(self._finished)
        self.session.status_changed.disconnect(self._status_changed)
        self.session.output_received.disconnect(self._output)
        self.session.shutdown()
