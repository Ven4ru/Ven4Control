from collections.abc import Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QCompleter, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox,
    QStackedWidget, QTextEdit, QVBoxLayout, QWidget,
)

from .models import Device


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
        self.username = QLineEdit("root")
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
