import asyncio
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ven4control.models import Device
from ven4control.remote_control import (
    ServiceInfo,
    SystemOverview,
    backup_configs,
    check_openwrt_upgrade,
    collect_overview,
    install_openwrt_upgrade,
    list_services,
    read_logs,
    reboot_device,
    restart_service,
    update_packages,
)


class TaskSignals(QObject):
    finished = Signal(object)
    failed = Signal(str)


class AsyncTask(QRunnable):
    def __init__(self, operation: Callable[[], object]):
        super().__init__()
        self.operation = operation
        self.signals = TaskSignals()

    @Slot()
    def run(self) -> None:
        try:
            self.signals.finished.emit(self.operation())
        except Exception as error:
            self.signals.failed.emit(str(error))


class DeviceControlDialog(QDialog):
    def __init__(
        self,
        device: Device,
        credentials: dict[str, str],
        backup_dir: Path,
        parent=None,
    ):
        super().__init__(parent)
        self.device = device
        self.credentials = credentials
        self.backup_dir = backup_dir
        self.pool = QThreadPool()
        self.tasks: set[AsyncTask] = set()
        self.setWindowTitle(f"Управление — {device.name}")
        self.resize(900, 650)

        title = QLabel(
            f"<b>{device.name}</b> — {device.username}@{device.host}:{device.port}"
        )
        self.status = QLabel("Готово")
        self.tabs = QTabWidget()
        self.tabs.addTab(self._create_overview_tab(), "Обзор")
        self.tabs.addTab(self._create_services_tab(), "Сервисы")
        self.tabs.addTab(self._create_logs_tab(), "Логи")
        self.tabs.addTab(self._create_maintenance_tab(), "Обслуживание")

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(title)
        layout.addWidget(self.tabs, 1)
        layout.addWidget(self.status)
        layout.addWidget(buttons)
        self.refresh_overview()

    def _create_overview_tab(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        self.overview_labels: dict[str, QLabel] = {}
        fields = [
            ("system", "Система"),
            ("uptime", "Время работы"),
            ("cpu", "Загрузка CPU"),
            ("memory", "Память"),
            ("disk", "Диск"),
            ("tailscale", "Tailscale"),
            ("wireguard", "WireGuard"),
        ]
        for key, caption in fields:
            label = QLabel("—")
            label.setWordWrap(True)
            self.overview_labels[key] = label
            form.addRow(f"{caption}:", label)
        refresh = QPushButton("Обновить показатели")
        refresh.clicked.connect(self.refresh_overview)
        form.addRow(refresh)
        return page

    def _create_services_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.services_table = QTableWidget(0, 3)
        self.services_table.setHorizontalHeaderLabels(["Сервис", "Состояние", "Подробности"])
        self.services_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.services_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.services_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.services_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self.services_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch
        )

        controls = QHBoxLayout()
        refresh = QPushButton("Обновить список")
        refresh.clicked.connect(self.refresh_services)
        restart = QPushButton("Перезапустить выбранный")
        restart.clicked.connect(self.restart_selected_service)
        adguard = QPushButton("Перезапустить AdGuard")
        adguard.clicked.connect(lambda: self.restart_named_service("AdGuardHome"))
        xray = QPushButton("Перезапустить Xray")
        xray.clicked.connect(lambda: self.restart_named_service("xray"))
        controls.addWidget(refresh)
        controls.addWidget(restart)
        controls.addStretch()
        controls.addWidget(adguard)
        controls.addWidget(xray)
        layout.addLayout(controls)
        layout.addWidget(self.services_table)
        return page

    def _create_logs_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        controls = QHBoxLayout()
        self.log_source = QComboBox()
        self.log_source.addItem("Системный журнал", "system")
        self.log_source.addItem("Tailscale", "tailscale")
        self.log_source.addItem("AdGuard", "adguard")
        self.log_source.addItem("Xray", "xray")
        self.log_lines = QSpinBox()
        self.log_lines.setRange(20, 1000)
        self.log_lines.setValue(200)
        load = QPushButton("Загрузить")
        load.clicked.connect(self.load_logs)
        controls.addWidget(QLabel("Источник:"))
        controls.addWidget(self.log_source)
        controls.addWidget(QLabel("Строк:"))
        controls.addWidget(self.log_lines)
        controls.addWidget(load)
        controls.addStretch()
        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        layout.addLayout(controls)
        layout.addWidget(self.log_output)
        return page

    def _create_maintenance_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        note = QLabel(
            "Операции выполняются через проверенное SSH-соединение. "
            "Обновление пакетов и прошивки может изменить систему; "
            "перед выполнением требуется отдельное подтверждение."
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        package_update = QPushButton("Обновить пакеты")
        package_update.clicked.connect(self.confirm_package_update)
        openwrt_check = QPushButton("Проверить обновление OpenWrt")
        openwrt_check.clicked.connect(self.check_openwrt)
        openwrt_upgrade = QPushButton("Установить обновление OpenWrt")
        openwrt_upgrade.clicked.connect(self.confirm_openwrt_upgrade)
        backup = QPushButton("Создать резервную копию конфигов")
        backup.clicked.connect(self.create_backup)
        reboot = QPushButton("Перезагрузить устройство")
        reboot.clicked.connect(self.confirm_reboot)
        for button in (
            package_update,
            openwrt_check,
            openwrt_upgrade,
            backup,
            reboot,
        ):
            layout.addWidget(button)

        self.maintenance_output = QTextEdit()
        self.maintenance_output.setReadOnly(True)
        layout.addWidget(self.maintenance_output, 1)
        return page

    def _start(
        self,
        coroutine_factory: Callable[[], object],
        success: Callable[[object], None],
        message: str,
    ) -> None:
        self.status.setText(message)
        task = AsyncTask(lambda: asyncio.run(coroutine_factory()))
        self.tasks.add(task)

        def finished(result: object) -> None:
            self.tasks.discard(task)
            self.status.setText("Готово")
            success(result)

        def failed(error: str) -> None:
            self.tasks.discard(task)
            self.status.setText("Ошибка")
            QMessageBox.critical(self, "Ошибка управления", error)

        task.signals.finished.connect(finished)
        task.signals.failed.connect(failed)
        self.pool.start(task)

    def refresh_overview(self) -> None:
        self._start(
            lambda: collect_overview(self.device, self.credentials),
            self._show_overview,
            "Получение показателей…",
        )

    def _show_overview(self, value: object) -> None:
        overview = value
        if not isinstance(overview, SystemOverview):
            return
        self.overview_labels["system"].setText(
            f"{overview.description} ({overview.platform})"
        )
        self.overview_labels["uptime"].setText(overview.uptime)
        self.overview_labels["cpu"].setText(overview.cpu)
        self.overview_labels["memory"].setText(overview.memory)
        self.overview_labels["disk"].setText(overview.disk)
        self.overview_labels["tailscale"].setText(overview.tailscale)
        self.overview_labels["wireguard"].setText(overview.wireguard)

    def refresh_services(self) -> None:
        self._start(
            lambda: list_services(self.device, self.credentials),
            self._show_services,
            "Получение списка сервисов…",
        )

    def _show_services(self, value: object) -> None:
        _, services = value
        self.services_table.setRowCount(len(services))
        for row, service in enumerate(services):
            if not isinstance(service, ServiceInfo):
                continue
            self.services_table.setItem(row, 0, QTableWidgetItem(service.name))
            self.services_table.setItem(row, 1, QTableWidgetItem(service.state))
            self.services_table.setItem(row, 2, QTableWidgetItem(service.details))

    def restart_selected_service(self) -> None:
        row = self.services_table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Сервис", "Выберите сервис в списке.")
            return
        item = self.services_table.item(row, 0)
        if item:
            self.restart_named_service(item.text())

    def restart_named_service(self, service: str) -> None:
        answer = QMessageBox.question(
            self,
            "Перезапуск сервиса",
            f"Перезапустить сервис «{service}» на устройстве «{self.device.name}»?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._start(
            lambda: restart_service(self.device, self.credentials, service),
            lambda result: self._operation_done(str(result), refresh_services=True),
            f"Перезапуск {service}…",
        )

    def load_logs(self) -> None:
        source = str(self.log_source.currentData())
        lines = self.log_lines.value()
        self._start(
            lambda: read_logs(self.device, self.credentials, source, lines),
            lambda result: self.log_output.setPlainText(str(result)),
            "Загрузка журнала…",
        )

    def confirm_package_update(self) -> None:
        answer = QMessageBox.warning(
            self,
            "Обновление пакетов",
            "Будут обновлены индексы и все доступные пакеты. На OpenWrt массовое "
            "обновление пакетов может быть несовместимо с текущей прошивкой.\n\nПродолжить?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._start(
            lambda: update_packages(self.device, self.credentials),
            lambda result: self._operation_done(str(result)),
            "Обновление пакетов…",
        )

    def check_openwrt(self) -> None:
        self._start(
            lambda: check_openwrt_upgrade(self.device, self.credentials),
            lambda result: self.maintenance_output.setPlainText(str(result)),
            "Проверка обновления OpenWrt…",
        )

    def confirm_openwrt_upgrade(self) -> None:
        text, accepted = QInputDialog.getText(
            self,
            "Опасная операция",
            f"Обновление прошивки может перезагрузить устройство.\n"
            f"Для подтверждения введите название: {self.device.name}",
        )
        if not accepted or text.strip() != self.device.name:
            return
        self._start(
            lambda: install_openwrt_upgrade(self.device, self.credentials),
            lambda result: self._operation_done(str(result)),
            "Обновление OpenWrt…",
        )

    def create_backup(self) -> None:
        self._start(
            lambda: backup_configs(
                self.device,
                self.credentials,
                self.backup_dir,
            ),
            lambda result: self._operation_done(f"Резервная копия сохранена:\n{result}"),
            "Создание резервной копии…",
        )

    def confirm_reboot(self) -> None:
        answer = QMessageBox.warning(
            self,
            "Перезагрузка устройства",
            f"Перезагрузить «{self.device.name}»? SSH-соединение будет разорвано.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._start(
            lambda: reboot_device(self.device, self.credentials),
            lambda result: self._operation_done(str(result)),
            "Отправка команды перезагрузки…",
        )

    def _operation_done(self, message: str, refresh_services: bool = False) -> None:
        self.maintenance_output.setPlainText(message)
        QMessageBox.information(self, "Операция завершена", message)
        if refresh_services:
            self.refresh_services()

    def reject(self) -> None:
        if self.tasks:
            QMessageBox.information(
                self,
                "Операция выполняется",
                "Дождитесь завершения текущей операции.",
            )
            return
        super().reject()
