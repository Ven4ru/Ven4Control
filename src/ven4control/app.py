import asyncio
import json
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal, Slot
from PySide6.QtGui import QAction, QCloseEvent, QIcon
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QHeaderView, QLabel, QMainWindow, QMenu,
    QMessageBox, QPushButton, QStyle, QSystemTrayIcon, QTableWidget,
    QTableWidgetItem, QToolBar, QVBoxLayout, QWidget,
)

from ven4control import autostart
from ven4control.control_dialog import DeviceControlDialog
from ven4control.credentials import CredentialStore
from ven4control.dialogs import AddDeviceDialog, InstructionsDialog
from ven4control.log_sessions import preferred_export_format, session_manager
from ven4control.log_worker import status_label
from ven4control.models import Device
from ven4control.paths import APP_KEY_PATH, BACKUP_DIR, DB_PATH
from ven4control.rdp_tunnel import (
    STATUS_ACTIVE as TUNNEL_ACTIVE,
    RdpTunnel,
    tunnel_manager,
    tunnel_status_label,
)
from ven4control.remote_control import RdpStatus, check_rdp, enable_rdp
from ven4control.ssh_service import (
    ensure_app_key,
    install_public_key,
    probe_device,
    tcp_check,
)
from ven4control.storage import DeviceStorage


def resource_path(name: str) -> Path:
    bundle_dir = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    bundled = bundle_dir / name
    if bundled.exists():
        return bundled
    # Запуск из репозитория: ресурсы лежат рядом с исходниками.
    return Path(__file__).resolve().parents[2] / "assets" / name


def terminal_command(device: Device) -> list[str]:
    """Аргументы ssh для запуска терминала к устройству."""
    args = ["ssh", "-p", str(device.port)]
    if device.auth_type == "key" and device.key_path:
        args += ["-i", device.key_path]
    args.append(f"{device.username}@{device.host}")
    return args


# Состояния RDP для панели действий.
RDP_UNKNOWN = "unknown"          # ещё не проверяли
RDP_UNSUPPORTED = "unsupported"  # не Windows: RDP неприменим, а не выключен
RDP_DISABLED = "disabled"        # Windows, приём подключений запрещён
RDP_ENABLED = "enabled"          # Windows, можно открывать туннель


def apply_rdp_result(device: Device, available: bool) -> bool:
    """Запоминает результат проверки RDP в устройстве.

    Возвращает True, если состояние изменилось и запись нужно сохранить
    в базу; повторная проверка с тем же результатом ничего не переписывает.
    """
    if device.rdp_checked and device.rdp_available == available:
        return False
    device.rdp_checked = True
    device.rdp_available = available
    return True


def rdp_state(device: Device | None, platform: str | None = None) -> str:
    """Состояние RDP устройства для панели действий.

    Платформа известна только после проверки в текущем запуске: в базе
    хранится лишь признак «RDP включён». Поэтому свежий ответ о платформе
    всегда важнее запомненного признака.
    """
    if device is None:
        return RDP_UNKNOWN
    if platform is not None and platform != "windows":
        return RDP_UNSUPPORTED
    if device.rdp_available:
        return RDP_ENABLED
    if platform == "windows":
        return RDP_DISABLED
    return RDP_UNKNOWN


def rdp_check_label(device: Device | None) -> str:
    """Текст кнопки проверки RDP для трёх состояний устройства."""
    if device is not None and device.rdp_checked and not device.rdp_available:
        return "RDP выключен — проверить снова"
    return "Проверить RDP"


def rdp_cell_text(tunnel: RdpTunnel | None) -> str:
    """Текст столбца RDP в списке устройств."""
    if tunnel is None:
        return "—"
    if tunnel.status == TUNNEL_ACTIVE and tunnel.local_port:
        return f"туннель 127.0.0.1:{tunnel.local_port}"
    return tunnel_status_label(tunnel.status)


def tailscale_candidates(
    status: dict,
    existing: set[tuple[str, int]],
) -> list[Device]:
    """Отбирает пиров Tailscale, которых ещё нет в списке устройств."""
    candidates: list[Device] = []
    seen = set(existing)
    for peer in (status.get("Peer") or {}).values():
        if not isinstance(peer, dict):
            continue
        addresses = peer.get("TailscaleIPs") or []
        if not addresses:
            continue
        host = str(addresses[0])
        if host.startswith("fd"):
            continue
        if (host, 22) in seen:
            continue
        seen.add((host, 22))
        display = str(peer.get("HostName") or peer.get("DNSName") or host).rstrip(".")
        candidates.append(Device(None, display, host, 22, "root"))
    return candidates


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
        # Реестр сессий живёт на уровне приложения: закрытие окна и диалогов
        # не должно прерывать фоновое логирование.
        self.sessions = session_manager()
        self.sessions.status_changed.connect(self._session_status_changed)
        self.sessions.session_finished.connect(self._session_finished)
        # RDP-туннели живут там же, на уровне приложения: свернули окно —
        # открытая сессия продолжает работать.
        self.tunnels = tunnel_manager()
        self.tunnels.status_changed.connect(self._tunnel_status_changed)
        self.tunnels.tunnel_closed.connect(self._tunnel_closed)
        self.tray: QSystemTrayIcon | None = None
        self._quitting = False
        self._tray_hint_shown = False
        self.pool = QThreadPool.globalInstance()
        self.workers: set[Worker] = set()
        # Устройства, для которых сейчас идёт проверка RDP: без этого набора
        # обновление панели действий возвращало кнопке исходный текст.
        self.rdp_checking: set[int] = set()
        # Платформа, определённая проверкой в текущем запуске. В базе её нет:
        # там хранится только признак «RDP включён», а различать «выключен»
        # и «неприменим» нужно для выбора кнопки.
        self.rdp_platforms: dict[int, str] = {}
        # Счётчик перезагрузок списка: ответы старых проверок отбрасываются,
        # иначе состояние попадало в строку уже другого устройства.
        self.status_generation = 0

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            [
                "Устройство", "Адрес", "Пользователь", "Состояние", "Задержка",
                "Логирование", "RDP",
            ]
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
        self.control_button = QPushButton("Управление устройством")
        self.control_button.clicked.connect(self.control_selected_device)
        self.logging_button = QPushButton("Логировать в фоне")
        self.logging_button.clicked.connect(self.toggle_selected_logging)
        self.rdp_check_button = QPushButton(rdp_check_label(None))
        self.rdp_check_button.clicked.connect(self.check_selected_rdp)
        self.rdp_enable_button = QPushButton("Включить RDP")
        self.rdp_enable_button.clicked.connect(self.enable_selected_rdp)
        self.rdp_button = QPushButton("🖥️ RDP")
        self.rdp_button.clicked.connect(self.open_selected_rdp)
        self.forget_button = QPushButton("Удалить сохранённые данные")
        self.forget_button.clicked.connect(self.forget_selected_credentials)
        self.delete_button = QPushButton("Удалить устройство")
        self.delete_button.clicked.connect(self.delete_selected_device)
        action_layout.addWidget(self.control_button)
        action_layout.addWidget(self.logging_button)
        action_layout.addWidget(self.terminal_button)
        action_layout.addWidget(self.rdp_check_button)
        action_layout.addWidget(self.rdp_enable_button)
        action_layout.addWidget(self.rdp_button)
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
        self._create_tray()
        self._update_selection()
        self.reload()
        self.restore_background_sessions()

    def _create_tray(self) -> None:
        """Создаёт значок в трее: без него окно закрывалось бы насовсем."""
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        icon = QIcon(str(resource_path("ven4control.ico")))
        if icon.isNull():
            icon = self.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)
        self.tray = QSystemTrayIcon(icon, self)
        menu = QMenu(self)
        show_action = menu.addAction("Показать окно")
        show_action.triggered.connect(self.show_from_tray)
        self.autostart_action = menu.addAction("Запускать при входе в Windows")
        self.autostart_action.setCheckable(True)
        self.autostart_action.setEnabled(autostart.is_supported())
        self.autostart_action.setChecked(autostart.is_enabled())
        self.autostart_action.toggled.connect(self.toggle_autostart)
        menu.addSeparator()
        quit_action = menu.addAction("Выйти из Ven4Control")
        quit_action.triggered.connect(self.quit_application)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._tray_activated)
        self.tray.show()
        self._update_tray()
        # Скрытое окно не должно завершать приложение: сессии продолжают идти.
        application = QApplication.instance()
        if application is not None:
            application.setQuitOnLastWindowClosed(False)

    def _tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.show_from_tray()

    def show_from_tray(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def toggle_autostart(self, enabled: bool) -> None:
        try:
            if enabled:
                autostart.enable()
            else:
                autostart.disable()
        except OSError as error:
            QMessageBox.warning(
                self, "Автозапуск не изменён", f"Не удалось записать в реестр: {error}"
            )
            if hasattr(self, "autostart_action"):
                self.autostart_action.setChecked(autostart.is_enabled())

    def _update_tray(self) -> None:
        if self.tray is None:
            return
        active = self.sessions.active_sessions()
        tunnels = self.tunnels.active_tunnels()
        lines: list[str] = []
        if active:
            names = ", ".join(
                f"{item.device_name} — {status_label(item.status)}" for item in active
            )
            lines.append(f"фоновых сессий: {len(active)}\n{names}")
        if tunnels:
            names = ", ".join(item.device_name for item in tunnels)
            lines.append(f"RDP-сессий: {len(tunnels)}\n{names}")
        self.tray.setToolTip(
            "Ven4Control — " + ("; ".join(lines) if lines else "фоновых сессий нет")
        )

    def _logging_text(self, device: Device) -> str:
        if self.sessions.is_active(device.id):
            return status_label(self.sessions.status(device.id))
        return "включено при запуске" if device.log_background else "выключено"

    def _rdp_text(self, device: Device) -> str:
        return rdp_cell_text(self.tunnels.tunnel(device.id))

    def _session_status_changed(self, device_id: int, status: str) -> None:
        self._update_tray()
        row = self._row_of(device_id)
        if row is not None and row < self.table.rowCount():
            self.table.setItem(row, 5, QTableWidgetItem(self._logging_text(self.devices[row])))
        selected = self.selected_device()
        if selected is not None and selected.id == device_id:
            self._update_selection()

    def _session_finished(self, device_id: int, message: str) -> None:
        self._update_tray()
        if self.tray is not None:
            self.tray.showMessage(
                "Ven4Control", message, QSystemTrayIcon.MessageIcon.Information
            )

    def _tunnel_status_changed(self, device_id: int, _status: str) -> None:
        row = self._row_of(device_id)
        if row is not None and row < self.table.rowCount():
            self.table.setItem(row, 6, QTableWidgetItem(self._rdp_text(self.devices[row])))
        selected = self.selected_device()
        if selected is not None and selected.id == device_id:
            self._update_selection()

    def _tunnel_closed(self, device_id: int, message: str) -> None:
        row = self._row_of(device_id)
        if row is not None and row < self.table.rowCount():
            self.table.setItem(row, 6, QTableWidgetItem(self._rdp_text(self.devices[row])))
        self._update_selection()
        if self.tray is not None:
            self.tray.showMessage(
                "Ven4Control", message, QSystemTrayIcon.MessageIcon.Information
            )

    def quit_application(self) -> None:
        active = self.sessions.active_count()
        tunnels = self.tunnels.active_count()
        if active or tunnels:
            parts = []
            if active:
                parts.append(f"фоновых сессий логирования: {active}")
            if tunnels:
                parts.append(f"RDP-сессий: {tunnels}")
            answer = QMessageBox.question(
                self,
                "Выход из Ven4Control",
                f"Сейчас работают {', '.join(parts)}. Закрыть их и выйти?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._quitting = True
        if self.tray is not None:
            self.tray.hide()
        self.close()
        QApplication.quit()

    def reload(self) -> None:
        selected = self.selected_device()
        selected_id = selected.id if selected else None
        self.status_generation += 1
        self.devices = self.storage.list_devices()
        self.table.setRowCount(len(self.devices))
        for row, device in enumerate(self.devices):
            self.table.setItem(row, 0, QTableWidgetItem(device.name))
            self.table.setItem(row, 1, QTableWidgetItem(f"{device.host}:{device.port}"))
            self.table.setItem(row, 2, QTableWidgetItem(device.username))
            self.table.setItem(row, 3, QTableWidgetItem("Проверка…"))
            self.table.setItem(row, 4, QTableWidgetItem("—"))
            self.table.setItem(row, 5, QTableWidgetItem(self._logging_text(device)))
            self.table.setItem(row, 6, QTableWidgetItem(self._rdp_text(device)))
        restored = self._row_of(selected_id)
        if restored is not None:
            self.table.selectRow(restored)
        self._update_selection()
        self.refresh_statuses()

    def _row_of(self, device_id: int | None) -> int | None:
        if device_id is None:
            return None
        for row, device in enumerate(self.devices):
            if device.id == device_id:
                return row
        return None

    @Slot()
    def add_device(self) -> None:
        dialog = AddDeviceDialog(self)
        if dialog.exec() != AddDeviceDialog.DialogCode.Accepted:
            return
        device = dialog.device()
        if not device.host or not device.username:
            QMessageBox.warning(self, "Недостаточно данных", "Укажите адрес и пользователя.")
            return
        if device.auth_type == "key" and not device.key_path:
            QMessageBox.warning(
                self, "Недостаточно данных", "Укажите путь к приватному SSH-ключу."
            )
            return
        if device.auth_type == "key" and not Path(device.key_path).exists():
            QMessageBox.warning(
                self, "Ключ не найден", f"Файл {device.key_path} не найден."
            )
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
        try:
            self.storage.save(device)
        except Exception as error:
            # Ключ на устройстве уже установлен, поэтому о несохранённых
            # настройках нужно сообщить явно.
            QMessageBox.critical(
                self,
                "Настройки входа не сохранены",
                f"Ключ установлен на устройстве, но запись в базе не обновлена:\n"
                f"{error}\n\nДобавьте устройство заново.",
            )
            self.reload()
            return
        QMessageBox.information(
            self, "Ключ установлен", f"Устройство определено как {system}. Вход по ключу настроен."
        )
        self.reload()

    def refresh_statuses(self) -> None:
        generation = self.status_generation
        for device in self.devices:
            worker = Worker(tcp_check, device.host, device.port)
            worker.signals.finished.connect(
                lambda result, i=device.id, g=generation: self._set_status(i, g, result)
            )
            worker.signals.failed.connect(
                lambda error, i=device.id, g=generation: self._set_status(
                    i, g, (False, error)
                )
            )
            self._start_worker(worker)

    def _set_status(
        self, device_id: int | None, generation: int, result: tuple[bool, str]
    ) -> None:
        if generation != self.status_generation:
            return
        row = self._row_of(device_id)
        if row is None or row >= self.table.rowCount():
            return
        online, detail = result
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
        args = terminal_command(device)
        try:
            subprocess.Popen(["wt.exe", "new-tab", "--title", device.name, *args])
            return
        except FileNotFoundError:
            pass
        try:
            # Windows Terminal не установлен: запускаем ssh в отдельном окне.
            # Передача аргументов в powershell -Command ломала пути с пробелами.
            subprocess.Popen(args, creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
        except OSError as error:
            QMessageBox.warning(
                self, "Терминал не запущен", f"Не удалось запустить ssh: {error}"
            )

    def open_rdp(self, device: Device) -> None:
        """Открывает RDP внутри SSH-туннеля.

        Прямого сетевого подключения к порту устройства не происходит:
        `mstsc` идёт на локальный конец туннеля, поэтому порт 3389 наружу
        открывать не нужно.
        """
        if device.id is None:
            return
        if self.tunnels.is_active(device.id):
            QMessageBox.information(
                self,
                "RDP уже открыт",
                f"Для «{device.name}» уже работает RDP-сессия. "
                "Вторая поверх первой ничего не даст.",
            )
            return
        try:
            self.tunnels.start(device, self.device_credentials(device))
        except Exception as error:
            QMessageBox.critical(self, "RDP не запущен", str(error))
            return
        self._update_selection()

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
        self.control_button.setEnabled(enabled)
        self.forget_button.setEnabled(enabled)
        self.delete_button.setEnabled(enabled)
        self.logging_button.setEnabled(enabled)
        active = device is not None and self.sessions.is_active(device.id)
        self.logging_button.setText(
            "Остановить логирование" if active else "Логировать в фоне"
        )
        checking = device is not None and device.id in self.rdp_checking
        state = rdp_state(
            device,
            self.rdp_platforms.get(device.id) if device is not None else None,
        )
        # У роутера или Linux-сервера RDP не выключен, а отсутствует: там
        # нечего проверять и нечего включать.
        self.rdp_check_button.setVisible(enabled and state != RDP_UNSUPPORTED)
        self.rdp_check_button.setEnabled(enabled and not checking)
        self.rdp_check_button.setText(
            "Проверка RDP…" if checking else rdp_check_label(device)
        )
        self.rdp_enable_button.setVisible(state == RDP_DISABLED)
        self.rdp_enable_button.setEnabled(enabled and not checking)
        # Кнопка запуска появляется только после успешной проверки: пока RDP
        # не подтверждён, у устройства остаётся путь через SSH-терминал.
        tunnelled = device is not None and self.tunnels.is_active(device.id)
        self.rdp_button.setVisible(state == RDP_ENABLED)
        self.rdp_button.setEnabled(enabled and not tunnelled)
        self.rdp_button.setText("RDP-сессия открыта" if tunnelled else "🖥️ RDP")

    def open_selected_terminal(self) -> None:
        device = self.selected_device()
        if device:
            self.open_terminal(device)

    def open_selected_rdp(self) -> None:
        device = self.selected_device()
        if device:
            self.open_rdp(device)

    def check_selected_rdp(self) -> None:
        """Спрашивает состояние RDP по SSH, не подключаясь к порту 3389."""
        device = self.selected_device()
        if device is None or device.id is None:
            return
        if device.id in self.rdp_checking:
            return
        if not self._require_fingerprint(device, "Проверка RDP недоступна"):
            return
        credentials = self.device_credentials(device)
        self.rdp_checking.add(device.id)
        self._update_selection()
        generation = self.status_generation

        def operation(target=device, secrets=credentials):
            return asyncio.run(check_rdp(target, secrets))

        worker = Worker(operation)
        worker.signals.finished.connect(
            lambda result, i=device.id, g=generation: self._set_rdp_result(i, g, result)
        )
        worker.signals.failed.connect(
            lambda error, i=device.id, g=generation: self._rdp_check_failed(i, g, error)
        )
        self._start_worker(worker)

    def _require_fingerprint(self, device: Device, title: str) -> bool:
        """RDP идёт по тому же доверенному каналу: без fingerprint нельзя."""
        if device.fingerprint:
            return True
        QMessageBox.warning(
            self,
            title,
            "Для устройства не сохранён SSH fingerprint. "
            "Переустановите ключ Ven4Control и повторите.",
        )
        return False

    def _set_rdp_result(
        self, device_id: int, generation: int, status: object
    ) -> None:
        self.rdp_checking.discard(device_id)
        # Список мог быть перезагружен: результат относился бы к другой записи.
        if generation != self.status_generation:
            return
        if not isinstance(status, RdpStatus):
            return
        self.rdp_platforms[device_id] = status.platform
        row = self._row_of(device_id)
        if row is None:
            return
        device = self.devices[row]
        if apply_rdp_result(device, status.supported and status.enabled):
            try:
                self.storage.save(device)
            except Exception as error:
                QMessageBox.warning(
                    self,
                    "Результат проверки не сохранён",
                    f"RDP проверен, но запись в базу не обновлена: {error}",
                )
        self._update_selection()
        if not status.supported:
            QMessageBox.information(
                self,
                "RDP неприменим",
                f"Устройство определено как {status.description} "
                f"({status.platform}). Удалённый рабочий стол есть только "
                "у Windows.",
            )

    def _rdp_check_failed(self, device_id: int, generation: int, error: str) -> None:
        self.rdp_checking.discard(device_id)
        if generation != self.status_generation:
            return
        self._update_selection()
        QMessageBox.warning(self, "Проверка RDP не выполнена", error)

    def enable_selected_rdp(self) -> None:
        """Разрешает приём RDP на устройстве. Файрвол при этом не трогается."""
        device = self.selected_device()
        if device is None or device.id is None:
            return
        if not self._require_fingerprint(device, "Включение RDP недоступно"):
            return
        answer = QMessageBox.warning(
            self,
            "Включение RDP",
            f"На устройстве «{device.name}» будет разрешён приём входящих "
            "RDP-подключений. Порт наружу не открывается: подключение пойдёт "
            "внутри SSH-туннеля.\n\nПродолжить?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        credentials = self.device_credentials(device)
        self.rdp_checking.add(device.id)
        self._update_selection()
        generation = self.status_generation

        def operation(target=device, secrets=credentials):
            return asyncio.run(enable_rdp(target, secrets))

        worker = Worker(operation)
        worker.signals.finished.connect(
            lambda _result, i=device.id, g=generation: self._rdp_enabled(i, g)
        )
        worker.signals.failed.connect(
            lambda error, i=device.id, g=generation: self._rdp_enable_failed(i, g, error)
        )
        self._start_worker(worker)

    def _rdp_enabled(self, device_id: int, generation: int) -> None:
        self.rdp_checking.discard(device_id)
        if generation != self.status_generation:
            return
        self.rdp_platforms[device_id] = "windows"
        row = self._row_of(device_id)
        if row is None:
            return
        device = self.devices[row]
        if apply_rdp_result(device, True):
            try:
                self.storage.save(device)
            except Exception as error:
                QMessageBox.warning(
                    self,
                    "Настройка не сохранена",
                    f"RDP включён, но запись в базу не обновлена: {error}",
                )
        self._update_selection()
        QMessageBox.information(
            self,
            "RDP включён",
            f"Устройство «{device.name}» принимает RDP-подключения. "
            "Кнопка «🖥️ RDP» откроет сессию внутри SSH-туннеля.",
        )

    def _rdp_enable_failed(self, device_id: int, generation: int, error: str) -> None:
        self.rdp_checking.discard(device_id)
        if generation != self.status_generation:
            return
        self._update_selection()
        QMessageBox.critical(self, "RDP не включён", error)

    def device_credentials(self, device: Device, silent: bool = False) -> dict[str, str]:
        credentials = {"password": "", "passphrase": ""}
        if device.id is None or not device.save_credentials:
            return credentials
        try:
            return self.credentials.load(device.id)
        except Exception as error:
            if not silent:
                QMessageBox.warning(
                    self,
                    "Данные подключения недоступны",
                    f"Не удалось прочитать сохранённые данные: {error}\n"
                    "Подключение продолжится с проверкой ключа.",
                )
        return credentials

    def control_selected_device(self) -> None:
        device = self.selected_device()
        if not device:
            return
        DeviceControlDialog(
            device,
            self.device_credentials(device),
            BACKUP_DIR,
            self,
        ).exec()

    def toggle_selected_logging(self) -> None:
        device = self.selected_device()
        if device:
            self.toggle_logging(device)

    def toggle_logging(self, device: Device) -> None:
        """Включает и выключает фоновую сессию для устройства."""
        if device.id is None:
            return
        if self.sessions.is_active(device.id):
            self.sessions.stop(device.id)
            self._remember_logging(device, False)
            self._update_selection()
            return
        if not device.fingerprint:
            QMessageBox.warning(
                self,
                "Фоновое логирование недоступно",
                "Для устройства не сохранён SSH fingerprint. "
                "Переустановите ключ Ven4Control и повторите.",
            )
            return
        try:
            self.sessions.start(
                device,
                self.device_credentials(device),
                export_format=preferred_export_format(),
            )
        except Exception as error:
            QMessageBox.critical(self, "Сессия не запущена", str(error))
            return
        self._remember_logging(device, True)
        self._update_selection()

    def _remember_logging(self, device: Device, enabled: bool) -> None:
        """Запоминает выбор, чтобы сессия поднялась при следующем запуске."""
        if device.log_background == enabled:
            return
        device.log_background = enabled
        try:
            self.storage.save(device)
        except Exception as error:
            QMessageBox.warning(
                self,
                "Настройка не сохранена",
                f"Сессия запущена, но выбор не записан в базу: {error}",
            )

    def restore_background_sessions(self) -> None:
        """Поднимает сессии устройств, отмеченных для фонового логирования."""
        skipped: list[str] = []
        for device in self.devices:
            if not device.log_background or device.id is None:
                continue
            if not device.fingerprint:
                skipped.append(f"{device.name} — нет сохранённого fingerprint")
                continue
            try:
                self.sessions.start(
                    device,
                    self.device_credentials(device, silent=True),
                    export_format=preferred_export_format(),
                )
            except Exception as error:
                skipped.append(f"{device.name} — {error}")
        if skipped and self.tray is not None:
            self.tray.showMessage(
                "Ven4Control",
                "Фоновое логирование не запущено:\n" + "\n".join(skipped),
                QSystemTrayIcon.MessageIcon.Warning,
            )
        self._update_selection()

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
        try:
            self.storage.save(device)
        except Exception as error:
            QMessageBox.critical(self, "Ошибка", str(error))
            self.reload()
            return
        QMessageBox.information(self, "Данные удалены", "Сохранённые данные подключения удалены.")

    def delete_device(self, device: Device) -> None:
        if device.id is None:
            return
        answer = QMessageBox.question(
            self, "Удалить устройство", f"Удалить «{device.name}» и сохранённые данные?"
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        # Сессия удалённого устройства осталась бы висеть в реестре и писать
        # журнал в папку уже несуществующей записи.
        self.sessions.stop(device.id)
        self.tunnels.close(device.id)
        self.credentials.delete(device.id)
        self.storage.delete(device.id)
        self.rdp_platforms.pop(device.id, None)
        self.reload()

    def show_instructions(self) -> None:
        try:
            public_key = self.public_key.read_text(encoding="utf-8")
        except OSError as error:
            QMessageBox.warning(
                self,
                "Публичный ключ недоступен",
                f"Не удалось прочитать {self.public_key}: {error}",
            )
            return
        InstructionsDialog(public_key, self).exec()

    def closeEvent(self, event: QCloseEvent) -> None:
        if not self._quitting and self.tray is not None:
            # Крестик прячет окно: фоновые сессии продолжают писать журнал.
            event.ignore()
            self.hide()
            if not self._tray_hint_shown:
                self.tray.showMessage(
                    "Ven4Control",
                    "Приложение свёрнуто в трей и продолжает работать. "
                    "Полный выход — через меню значка.",
                    QSystemTrayIcon.MessageIcon.Information,
                )
                self._tray_hint_shown = True
            return
        self.pool.waitForDone(5000)
        self.tunnels.shutdown()
        self.sessions.shutdown()
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
            # Список устройств читается из базы: он мог измениться
            # в другом окне или после неудачного добавления.
            existing = {
                (device.host, device.port) for device in self.storage.list_devices()
            }
            candidates = tailscale_candidates(data, existing)
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
            skipped: list[str] = []
            for device in candidates:
                try:
                    self.storage.save(device)
                except Exception as error:
                    # Одна проблемная запись не должна прерывать импорт.
                    skipped.append(f"{device.name} — {error}")
            self.reload()
            if skipped:
                QMessageBox.warning(
                    self,
                    "Часть устройств не добавлена",
                    "\n".join(skipped),
                )
        except FileNotFoundError:
            QMessageBox.warning(self, "Tailscale", "Команда tailscale не найдена.")
        except subprocess.TimeoutExpired:
            QMessageBox.warning(self, "Tailscale", "Команда tailscale не ответила.")
        except subprocess.CalledProcessError as error:
            detail = (error.stderr or error.stdout or "").strip()
            QMessageBox.warning(
                self,
                "Tailscale",
                detail or f"Команда tailscale завершилась с кодом {error.returncode}.",
            )
        except json.JSONDecodeError:
            QMessageBox.warning(
                self, "Tailscale", "Не удалось разобрать ответ команды tailscale."
            )
        except Exception as error:
            QMessageBox.critical(self, "Ошибка импорта Tailscale", str(error))


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Ven4Control")
    icon_path = resource_path("ven4control.ico")
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    try:
        window = MainWindow()
    except Exception as error:
        # Без окна traceback уходил в никуда: сборка запускается без консоли.
        QMessageBox.critical(None, "Ven4Control не запустился", str(error))
        return 1
    # Запуск при входе в Windows поднимает фоновые сессии и остаётся в трее.
    if autostart.TRAY_ARGUMENT not in sys.argv or window.tray is None:
        window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
