import asyncio
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal, Slot
from PySide6.QtGui import QAction, QCloseEvent, QIcon
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QHeaderView, QLabel, QMainWindow, QMessageBox,
    QPushButton, QTableWidget, QTableWidgetItem, QToolBar, QVBoxLayout, QWidget,
)

from ven4control.credentials import CredentialStore
from ven4control.dialogs import AddDeviceDialog, InstructionsDialog
from ven4control.models import Device
from ven4control.ssh_service import (
    ensure_app_key,
    install_public_key,
    probe_device,
    tcp_check,
)
from ven4control.storage import DeviceStorage


APP_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Ven4Control"
DB_PATH = APP_DIR / "devices.db"
APP_KEY_PATH = APP_DIR / "ssh" / "id_ed25519"


def resource_path(name: str) -> Path:
    bundle_dir = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return bundle_dir / name


class WorkerSignals(QObject):
    finished = Signal(object)
    failed = Signal(str)


class Worker(QRunnable):
    def __init__(self, function, *args):
        super().__init__()
        self.function = function
        self.args = args
        self.signals = WorkerSignals()

    @Slot()
    def run(self):
        try:
            result = self.function(*self.args)
            self.signals.finished.emit(result)
        except Exception as error:
            self.signals.failed.emit(str(error))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Ven4Control")
        self.resize(980, 600)
        self.storage = DeviceStorage(DB_PATH)
        self.credentials = CredentialStore()
        self.private_key, self.public_key = ensure_app_key(APP_KEY_PATH)
        self.devices: list[Device] = []
        self.pool = QThreadPool.globalInstance()
        self.workers: set[Worker] = set()

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["Устройство", "Адрес", "Пользователь", "Состояние", "Задержка"]
        )
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self._update_selection)

        action_panel = QWidget()
        action_panel.setMinimumWidth(190)
        action_panel.setMaximumWidth(230)
        action_layout = QVBoxLayout(action_panel)
        action_layout.addWidget(QLabel("Действия"))
        self.selected_label = QLabel("Устройство не выбрано")
        self.selected_label.setWordWrap(True)
        action_layout.addWidget(self.selected_label)
        self.terminal_button = QPushButton("Открыть терминал")
        self.terminal_button.clicked.connect(self.open_selected_terminal)
        self.forget_button = QPushButton("Удалить сохранённые данные")
        self.forget_button.clicked.connect(self.forget_selected_credentials)
        self.delete_button = QPushButton("Удалить устройство")
        self.delete_button.clicked.connect(self.delete_selected_device)
        action_layout.addWidget(self.terminal_button)
        action_layout.addWidget(self.forget_button)
        action_layout.addWidget(self.delete_button)
        action_layout.addStretch()

        toolbar = QToolBar()
        self.addToolBar(toolbar)
        add_action = QAction("Добавить", self)
        add_action.triggered.connect(self.add_device)
        refresh_action = QAction("Обновить", self)
        refresh_action.triggered.connect(self.refresh_statuses)
        instructions_action = QAction("Установка ключа", self)
        instructions_action.triggered.connect(self.show_instructions)
        tailscale_action = QAction("Импорт Tailscale", self)
        tailscale_action.triggered.connect(self.import_tailscale)
        toolbar.addAction(add_action)
        toolbar.addAction(refresh_action)
        toolbar.addAction(tailscale_action)
        toolbar.addAction(instructions_action)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.addWidget(QLabel("Ваши устройства"))
        content_layout = QHBoxLayout()
        content_layout.addWidget(action_panel)
        content_layout.addWidget(self.table, 1)
        layout.addLayout(content_layout)
        self.setCentralWidget(container)
        self._update_selection()
        self.reload()

    def reload(self) -> None:
        self.devices = self.storage.list_devices()
        self.table.setRowCount(len(self.devices))
        for row, device in enumerate(self.devices):
            self.table.setItem(row, 0, QTableWidgetItem(device.name))
            self.table.setItem(row, 1, QTableWidgetItem(f"{device.host}:{device.port}"))
            self.table.setItem(row, 2, QTableWidgetItem(device.username))
            self.table.setItem(row, 3, QTableWidgetItem("Проверка…"))
            self.table.setItem(row, 4, QTableWidgetItem("—"))
        self._update_selection()
        self.refresh_statuses()

    @Slot()
    def add_device(self) -> None:
        dialog = AddDeviceDialog(self)
        if dialog.exec() != AddDeviceDialog.DialogCode.Accepted:
            return
        device = dialog.device()
        if not device.host or not device.username:
            QMessageBox.warning(self, "Недостаточно данных", "Укажите адрес и пользователя.")
            return
        try:
            device = self.storage.save(device)
            if device.save_credentials and device.id is not None:
                self.credentials.save(
                    device.id,
                    password=dialog.password.text(),
                    passphrase=dialog.passphrase.text(),
                )
            if (
                device.auth_type == "password"
                and dialog.install_key.isChecked()
                and dialog.password.text()
            ):
                self._install_key(device, dialog.password.text())
            else:
                self.reload()
        except Exception as error:
            QMessageBox.critical(self, "Ошибка", str(error))

    def _install_key(self, device: Device, password: str) -> None:
        def operation():
            return asyncio.run(probe_device(device, password))

        worker = Worker(operation)
        worker.signals.finished.connect(
            lambda result: self._confirm_key_install(device, password, result)
        )
        worker.signals.failed.connect(
            lambda error: QMessageBox.warning(
                self, "Устройство добавлено, но ключ не установлен", error
            )
        )
        worker.signals.failed.connect(self.reload)
        self._start_worker(worker)

    def _confirm_key_install(
        self, device: Device, password: str, result: tuple[str, str]
    ) -> None:
        system, fingerprint = result
        answer = QMessageBox.question(
            self,
            "Подтверждение SSH fingerprint",
            f"Тип устройства: {system}\n\nFingerprint сервера:\n{fingerprint}\n\n"
            "Установить публичный ключ Ven4Control?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            self.reload()
            return

        def operation():
            return asyncio.run(
                install_public_key(device, password, self.public_key, fingerprint)
            )

        worker = Worker(operation)
        worker.signals.finished.connect(
            lambda detected: self._key_installed(
                device, str(detected), fingerprint
            )
        )
        worker.signals.failed.connect(
            lambda error: QMessageBox.warning(self, "Ключ не установлен", error)
        )
        worker.signals.failed.connect(self.reload)
        self._start_worker(worker)

    def _key_installed(self, device: Device, system: str, fingerprint: str) -> None:
        device.auth_type = "key"
        device.key_path = str(self.private_key)
        device.fingerprint = fingerprint
        self.storage.save(device)
        QMessageBox.information(
            self, "Ключ установлен", f"Устройство определено как {system}. Вход по ключу настроен."
        )
        self.reload()

    def refresh_statuses(self) -> None:
        for row, device in enumerate(self.devices):
            worker = Worker(tcp_check, device.host, device.port)
            worker.signals.finished.connect(
                lambda result, r=row: self._set_status(r, result)
            )
            worker.signals.failed.connect(
                lambda error, r=row: self._set_status(r, (False, error))
            )
            self._start_worker(worker)

    def _set_status(self, row: int, result: tuple[bool, str]) -> None:
        online, detail = result
        if row >= self.table.rowCount():
            return
        status = QTableWidgetItem("В сети" if online else "Не в сети")
        status.setForeground(Qt.GlobalColor.darkGreen if online else Qt.GlobalColor.red)
        self.table.setItem(row, 3, status)
        self.table.setItem(row, 4, QTableWidgetItem(detail if online else "—"))

    def _start_worker(self, worker: Worker) -> None:
        self.workers.add(worker)
        worker.signals.finished.connect(lambda _=None, w=worker: self.workers.discard(w))
        worker.signals.failed.connect(lambda _=None, w=worker: self.workers.discard(w))
        self.pool.start(worker)

    def open_terminal(self, device: Device) -> None:
        args = ["ssh", "-p", str(device.port)]
        if device.auth_type == "key" and device.key_path:
            args += ["-i", device.key_path]
        args.append(f"{device.username}@{device.host}")
        try:
            subprocess.Popen(["wt.exe", "new-tab", "--title", device.name, *args])
        except FileNotFoundError:
            subprocess.Popen(["powershell.exe", "-NoExit", "-Command", *args])

    def selected_device(self) -> Device | None:
        row = self.table.currentRow()
        if 0 <= row < len(self.devices):
            return self.devices[row]
        return None

    def _update_selection(self) -> None:
        device = self.selected_device()
        enabled = device is not None
        self.selected_label.setText(
            f"{device.name}\n{device.username}@{device.host}:{device.port}"
            if device else "Устройство не выбрано"
        )
        self.terminal_button.setEnabled(enabled)
        self.forget_button.setEnabled(enabled)
        self.delete_button.setEnabled(enabled)

    def open_selected_terminal(self) -> None:
        device = self.selected_device()
        if device:
            self.open_terminal(device)

    def forget_selected_credentials(self) -> None:
        device = self.selected_device()
        if device:
            self.forget_credentials(device)

    def delete_selected_device(self) -> None:
        device = self.selected_device()
        if device:
            self.delete_device(device)

    def forget_credentials(self, device: Device) -> None:
        if device.id is None:
            return
        self.credentials.delete(device.id)
        device.save_credentials = False
        self.storage.save(device)
        QMessageBox.information(self, "Данные удалены", "Сохранённые данные подключения удалены.")

    def delete_device(self, device: Device) -> None:
        if device.id is None:
            return
        answer = QMessageBox.question(
            self, "Удалить устройство", f"Удалить «{device.name}» и сохранённые данные?"
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.credentials.delete(device.id)
        self.storage.delete(device.id)
        self.reload()

    def show_instructions(self) -> None:
        InstructionsDialog(self.public_key.read_text(encoding="utf-8"), self).exec()

    def closeEvent(self, event: QCloseEvent) -> None:
        self.pool.waitForDone(5000)
        event.accept()

    def import_tailscale(self) -> None:
        try:
            completed = subprocess.run(
                ["tailscale", "status", "--json"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=10,
                check=True,
            )
            data = json.loads(completed.stdout)
            peers = list(data.get("Peer", {}).values())
            existing = {(device.host, device.port) for device in self.devices}
            candidates: list[Device] = []
            for peer in peers:
                addresses = peer.get("TailscaleIPs") or []
                if not addresses:
                    continue
                host = str(addresses[0])
                if host.startswith("fd"):
                    continue
                if (host, 22) in existing:
                    continue
                display = str(peer.get("HostName") or peer.get("DNSName") or host).rstrip(".")
                candidates.append(Device(None, display, host, 22, "root"))
            if not candidates:
                QMessageBox.information(
                    self, "Tailscale", "Новых устройств Tailscale не найдено."
                )
                return
            names = "\n".join(f"• {item.name} — {item.host}" for item in candidates)
            answer = QMessageBox.question(
                self,
                "Импорт Tailscale",
                f"Добавить найденные устройства?\n\n{names}",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            for device in candidates:
                self.storage.save(device)
            self.reload()
        except FileNotFoundError:
            QMessageBox.warning(self, "Tailscale", "Команда tailscale не найдена.")
        except Exception as error:
            QMessageBox.critical(self, "Ошибка импорта Tailscale", str(error))


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Ven4Control")
    icon_path = resource_path("ven4control.ico")
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
