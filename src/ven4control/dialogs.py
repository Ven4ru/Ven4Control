from collections.abc import Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QCompleter, QDialog, QDialogButtonBox,
    QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QSpinBox, QStackedWidget, QTextEdit, QVBoxLayout, QWidget,
)

from ven4control.ansi_screen import set_default_colors as set_terminal_colors
from ven4control.settings import (
    TERMINAL_THEME_SYNC,
    AppSettings,
    save_settings,
    terminal_palette,
)
from ven4control.theme import THEME_LABELS, THEMES, apply_theme, build_palette

from .models import Device


# Подсказка поля «Пользователь». Значение по умолчанию здесь не ставится:
# заполненный `root` новичок с Ubuntu или Windows не замечает и получает
# Permission denied, не понимая причины.
USERNAME_PLACEHOLDER = "root — для OpenWrt, ваше имя — для Ubuntu/Windows"


class AddDeviceDialog(QDialog):
    def __init__(self, parent=None, groups: Sequence[str] = ()):
        super().__init__(parent)
        self.setWindowTitle("Добавить устройство")
        self.resize(560, 420)

        self.name = QLineEdit()
        self.group_name = QLineEdit()
        self.group_name.setPlaceholderText("Дом, Работа, Друзья — необязательно")
        if groups:
            # Подсказка по уже заведённым группам: одна и та же группа,
            # набранная по-разному, разошлась бы на две строки в списке.
            completer = QCompleter(list(groups), self)
            completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            self.group_name.setCompleter(completer)
        self.host = QLineEdit()
        self.host.setPlaceholderText("IP, имя Tailscale или домен")
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(22)
        self.username = QLineEdit()
        # Не предзаполненное значение, а подсказка: `root` верен для OpenWrt,
        # но на Ubuntu и Windows он давал незамеченный Permission denied —
        # пользователь не видел, что поле уже заполнено за него.
        self.username.setPlaceholderText(USERNAME_PLACEHOLDER)
        self.auth_type = QComboBox()
        self.auth_type.addItem("Логин и пароль", "password")
        self.auth_type.addItem("SSH-ключ", "key")
        self.auth_stack = QStackedWidget()

        password_page = QWidget()
        password_form = QFormLayout(password_page)
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        password_form.addRow("Пароль:", self.password)

        key_page = QWidget()
        key_form = QFormLayout(key_page)
        self.key_path = QLineEdit()
        browse = QPushButton("Выбрать…")
        browse.clicked.connect(self._browse_key)
        key_row = QHBoxLayout()
        key_row.addWidget(self.key_path)
        key_row.addWidget(browse)
        self.passphrase = QLineEdit()
        self.passphrase.setEchoMode(QLineEdit.EchoMode.Password)
        key_form.addRow("Приватный ключ:", key_row)
        key_form.addRow("Passphrase:", self.passphrase)

        self.auth_stack.addWidget(password_page)
        self.auth_stack.addWidget(key_page)
        self.auth_type.currentIndexChanged.connect(self.auth_stack.setCurrentIndex)
        self.save_credentials = QCheckBox("Сохранять введённые данные")
        self.install_key = QCheckBox("Установить ключ Ven4Control после входа по паролю")
        self.install_key.setChecked(True)

        form = QFormLayout()
        form.addRow("Название:", self.name)
        form.addRow("Группа:", self.group_name)
        form.addRow("Адрес:", self.host)
        form.addRow("SSH-порт:", self.port)
        form.addRow("Пользователь:", self.username)
        form.addRow("Способ входа:", self.auth_type)

        note = QLabel(
            "Сохранённые пароли помещаются в Windows Credential Manager, "
            "а не в базу приложения."
        )
        note.setWordWrap(True)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.auth_stack)
        layout.addWidget(self.save_credentials)
        layout.addWidget(self.install_key)
        layout.addWidget(note)
        layout.addStretch()
        layout.addWidget(buttons)

    def _browse_key(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Выберите приватный SSH-ключ")
        if path:
            self.key_path.setText(path)

    def device(self) -> Device:
        return Device(
            id=None,
            name=self.name.text().strip() or self.host.text().strip(),
            host=self.host.text().strip(),
            port=self.port.value(),
            username=self.username.text().strip(),
            group_name=self.group_name.text().strip(),
            auth_type=str(self.auth_type.currentData()),
            key_path=self.key_path.text().strip(),
            save_credentials=self.save_credentials.isChecked(),
        )


class InstructionsDialog(QDialog):
    def __init__(self, public_key: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Ручная установка SSH-ключа")
        self.resize(760, 560)
        encoded = public_key.strip()
        text = f"""OPENWRT:
mkdir -p /etc/dropbear
echo '{encoded}' >> /etc/dropbear/authorized_keys
chmod 600 /etc/dropbear/authorized_keys
/etc/init.d/dropbear restart

LINUX:
mkdir -p ~/.ssh
chmod 700 ~/.ssh
echo '{encoded}' >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys

WINDOWS OPENSSH:
New-Item "$HOME\\.ssh" -ItemType Directory -Force
Add-Content "$HOME\\.ssh\\authorized_keys" "{encoded}"
"""
        editor = QTextEdit()
        editor.setReadOnly(True)
        editor.setPlainText(text)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout = QVBoxLayout(self)
        layout.addWidget(editor)
        layout.addWidget(buttons)


class SettingsDialog(QDialog):
    """Выбор темы интерфейса и (независимо) темы терминала.

    Тема терминала по умолчанию — «Как в приложении»: следует за темой
    интерфейса, как в фазе 1. Выбор конкретной темы для терминала
    отвязывает его от темы приложения — дальнейшая смена темы интерфейса
    терминал больше не трогает, пока не выбрать «Как в приложении» снова.
    """

    def __init__(self, settings: AppSettings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Настройки")
        self.resize(380, 480)
        self.selected_theme = settings.theme
        self.selected_terminal_theme = settings.terminal_theme

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Тема интерфейса"))
        self._app_buttons: dict[str, QPushButton] = {}
        for theme in THEMES:
            button = self._theme_button(
                THEME_LABELS[theme],
                build_palette(theme)["accent_color"],
                theme == self.selected_theme,
            )
            button.clicked.connect(lambda _checked, t=theme: self._select_app_theme(t))
            self._app_buttons[theme] = button
            layout.addWidget(button)

        layout.addSpacing(12)
        layout.addWidget(QLabel("Тема терминала"))
        self._terminal_buttons: dict[str, QPushButton] = {}
        sync_button = self._theme_button(
            "Как в приложении", None, self.selected_terminal_theme == TERMINAL_THEME_SYNC
        )
        sync_button.clicked.connect(
            lambda _checked: self._select_terminal_theme(TERMINAL_THEME_SYNC)
        )
        self._terminal_buttons[TERMINAL_THEME_SYNC] = sync_button
        layout.addWidget(sync_button)
        for theme in THEMES:
            button = self._theme_button(
                THEME_LABELS[theme],
                build_palette(theme)["accent_color"],
                theme == self.selected_terminal_theme,
            )
            button.clicked.connect(lambda _checked, t=theme: self._select_terminal_theme(t))
            self._terminal_buttons[theme] = button
            layout.addWidget(button)

        layout.addStretch()
        buttons_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons_box.rejected.connect(self.reject)
        layout.addWidget(buttons_box)

    @staticmethod
    def _theme_button(label: str, accent: str | None, checked: bool) -> QPushButton:
        # Полоса акцентного цвета слева — тот же приём, что у логотипа в
        # Ven4Tools: кнопка сама показывает акцент, а не только название
        # текстом. У «Как в приложении» акцента нет — полоса прозрачная,
        # а не какого-то одного цвета темы, который был бы неверным намёком.
        button = QPushButton(label)
        button.setCheckable(True)
        button.setChecked(checked)
        border = f"4px solid {accent}" if accent else "4px solid transparent"
        button.setStyleSheet(
            f"QPushButton {{ border-left: {border}; text-align: left; padding: 10px; }}"
        )
        return button

    def _select_app_theme(self, theme: str) -> None:
        self.selected_theme = theme
        for name, button in self._app_buttons.items():
            button.setChecked(name == theme)
        app = QApplication.instance()
        if app is not None:
            apply_theme(app, theme)
        # Синхронизированный терминал следует за темой приложения; терминал
        # с явно выбранной темой этой сменой не затрагивается.
        self._apply_terminal_colors()
        self._save()

    def _select_terminal_theme(self, theme: str) -> None:
        self.selected_terminal_theme = theme
        for name, button in self._terminal_buttons.items():
            button.setChecked(name == theme)
        self._apply_terminal_colors()
        self._save()

    def _apply_terminal_colors(self) -> None:
        # Без этого терминал, открытый после смены темы в этом же запуске,
        # оставался бы в старых цветах до перезапуска приложения — main()
        # выставляет их только один раз, при старте.
        palette = terminal_palette(
            AppSettings(theme=self.selected_theme, terminal_theme=self.selected_terminal_theme)
        )
        set_terminal_colors(palette["content_background"], palette["text_primary"])

    def _save(self) -> None:
        save_settings(
            AppSettings(theme=self.selected_theme, terminal_theme=self.selected_terminal_theme)
        )
