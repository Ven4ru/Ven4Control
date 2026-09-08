import asyncio
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal, Slot
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
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

from ven4control.log_sessions import (
    preferred_export_format,
    session_manager,
    set_preferred_export_format,
)
from ven4control.log_storage import EXPORT_FORMATS
from ven4control.log_worker import status_label
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
from ven4control.sftp_session import (
    RemoteEntry,
    SftpSession,
    child_path,
    parent_path,
    sftp_status_label,
)


# Сколько строк живого просмотра держать в памяти: журнал целиком пишется
# в файл сессии, окно нужно только для наблюдения.
MAX_LIVE_LINES = 2000


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
        # Сессия принадлежит приложению, а не диалогу: закрытие окна её
        # не останавливает.
        self.sessions = session_manager()
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
        self.tabs.addTab(self._create_background_tab(), "Фоновый журнал")
        self.files_page = self._create_files_tab()
        self.tabs.addTab(self.files_page, "Файлы")
        self.tabs.addTab(self._create_maintenance_tab(), "Обслуживание")
        # SFTP-соединение открывается только когда его действительно
        # попросили: за обзором и логами пользователь на вкладку файлов
        # может не зайти ни разу.
        self.tabs.currentChanged.connect(self._tab_changed)

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
            ("temperature", "Температура"),
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

    def _create_background_tab(self) -> QWidget:
        """Вкладка непрерывного журнала: сессия переживает закрытие диалога."""
        page = QWidget()
        layout = QVBoxLayout(page)
        note = QLabel(
            "Фоновая сессия держит открытый канал журнала, переподключается "
            "после разрыва и раз в полминуты дописывает показатели системы. "
            "Журнал пишется в папку приложения и выгружается в выбранный "
            "формат при остановке."
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        controls = QHBoxLayout()
        self.background_source = QComboBox()
        self.background_source.addItem("Системный журнал", "system")
        self.background_source.addItem("Tailscale", "tailscale")
        self.background_source.addItem("AdGuard", "adguard")
        self.background_source.addItem("Xray", "xray")
        self.background_format = QComboBox()
        for export_format in EXPORT_FORMATS:
            self.background_format.addItem(export_format.upper(), export_format)
        stored = self.background_format.findData(preferred_export_format())
        if stored >= 0:
            self.background_format.setCurrentIndex(stored)
        self.background_button = QPushButton("Начать фоновое логирование")
        self.background_button.clicked.connect(self.toggle_background_logging)
        controls.addWidget(QLabel("Источник:"))
        controls.addWidget(self.background_source)
        controls.addWidget(QLabel("Формат экспорта:"))
        controls.addWidget(self.background_format)
        controls.addWidget(self.background_button)
        controls.addStretch()
        layout.addLayout(controls)

        self.background_status = QLabel()
        self.background_status.setWordWrap(True)
        layout.addWidget(self.background_status)

        self.background_output = QTextEdit()
        self.background_output.setReadOnly(True)
        self.background_output.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        layout.addWidget(self.background_output, 1)

        self.sessions.status_changed.connect(self._background_status_changed)
        self.sessions.line_received.connect(self._background_line)
        self._update_background_controls()
        return page

    def toggle_background_logging(self) -> None:
        if self.device.id is None:
            QMessageBox.warning(
                self, "Сессия недоступна", "Устройство не сохранено в базе."
            )
            return
        if self.sessions.is_active(self.device.id):
            self.sessions.stop(self.device.id)
            self._update_background_controls()
            return
        if not self.device.fingerprint:
            QMessageBox.warning(
                self,
                "Фоновое логирование недоступно",
                "Для устройства не сохранён SSH fingerprint. "
                "Переустановите ключ Ven4Control и повторите.",
            )
            return
        export_format = str(self.background_format.currentData())
        try:
            set_preferred_export_format(export_format)
            self.sessions.start(
                self.device,
                self.credentials,
                source=str(self.background_source.currentData()),
                export_format=export_format,
            )
        except Exception as error:
            QMessageBox.critical(self, "Сессия не запущена", str(error))
            return
        self.background_output.clear()
        self._update_background_controls()

    def _background_status_changed(self, device_id: int, _status: str) -> None:
        if device_id == self.device.id:
            self._update_background_controls()

    def _background_line(self, device_id: int, text: str) -> None:
        if device_id != self.device.id:
            return
        self.background_output.append(text)
        document = self.background_output.document()
        # Живой просмотр не должен расти бесконечно: журнал целиком лежит
        # в файле сессии.
        if document.blockCount() > MAX_LIVE_LINES:
            cursor = self.background_output.textCursor()
            cursor.movePosition(cursor.MoveOperation.Start)
            cursor.movePosition(
                cursor.MoveOperation.Down,
                cursor.MoveMode.KeepAnchor,
                document.blockCount() - MAX_LIVE_LINES,
            )
            cursor.removeSelectedText()

    def _update_background_controls(self) -> None:
        active = self.sessions.is_active(self.device.id)
        session = self.sessions.session(self.device.id)
        self.background_button.setText(
            "Остановить фоновое логирование" if active else "Начать фоновое логирование"
        )
        self.background_source.setEnabled(not active)
        self.background_format.setEnabled(not active)
        if session is not None:
            self.background_status.setText(
                f"Состояние: {status_label(session.status)}. "
                f"Строк: {session.writer.total_lines}. Папка: {session.directory}"
            )
        else:
            self.background_status.setText("Состояние: сессия не запущена")

    def _create_files_tab(self) -> QWidget:
        """Вкладка файлов: листинг папок устройства и передача одного файла."""
        page = QWidget()
        layout = QVBoxLayout(page)

        self.files_path = QLabel("—")
        self.files_path.setWordWrap(True)
        # Имена и пути приходят с устройства: как разметку их читать нельзя.
        self.files_path.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.files_path)

        controls = QHBoxLayout()
        self.files_up_button = QPushButton("Наверх")
        self.files_up_button.clicked.connect(self.open_parent_directory)
        self.files_refresh_button = QPushButton("Обновить")
        self.files_refresh_button.clicked.connect(self.refresh_files)
        self.files_download_button = QPushButton("Скачать выбранный")
        self.files_download_button.clicked.connect(self.download_selected_file)
        self.files_upload_button = QPushButton("Загрузить файл")
        self.files_upload_button.clicked.connect(self.upload_file)
        controls.addWidget(self.files_up_button)
        controls.addWidget(self.files_refresh_button)
        controls.addStretch()
        controls.addWidget(self.files_download_button)
        controls.addWidget(self.files_upload_button)
        layout.addLayout(controls)

        self.files_table = QTableWidget(0, 4)
        self.files_table.setHorizontalHeaderLabels(["Имя", "Тип", "Размер", "Права"])
        self.files_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.files_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.files_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.files_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self.files_table.doubleClicked.connect(lambda _index: self.open_selected_entry())
        layout.addWidget(self.files_table, 1)

        self.files_status = QLabel("Соединение откроется при переходе на вкладку.")
        self.files_status.setWordWrap(True)
        self.files_status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.files_status)

        self.files_entries: list[RemoteEntry] = []
        self.files_started = False
        self.files_busy = False
        self.files = SftpSession(self.device, self.credentials)
        self.files.listing_received.connect(self._show_listing)
        self.files.status_changed.connect(self._files_status_changed)
        self.files.operation_failed.connect(self._files_failed)
        self.files.progress_changed.connect(self.files_status.setText)
        self.files.transfer_finished.connect(self._files_transfer_finished)
        self._update_files_controls()
        return page

    def _tab_changed(self, index: int) -> None:
        if self.tabs.widget(index) is self.files_page:
            self._start_files_session()

    def _start_files_session(self) -> None:
        if self.files_started:
            return
        self.files_started = True
        try:
            self.files.start()
        except Exception as error:
            self.files_status.setText(str(error))
            QMessageBox.warning(self, "Файлы недоступны", str(error))
            return
        self.files_status.setText("Подключение к устройству…")
        self.files.list_directory()

    def refresh_files(self) -> None:
        if self.files.list_directory():
            self.files_status.setText("Чтение папки…")

    def open_parent_directory(self) -> None:
        target = parent_path(self.files.path)
        if target == self.files.path:
            self.files_status.setText("Это корневая папка устройства.")
            return
        if self.files.list_directory(target):
            self.files_status.setText("Чтение папки…")

    def open_selected_entry(self) -> None:
        entry = self._selected_entry()
        if entry is None:
            return
        if not entry.can_enter:
            self.files_status.setText(
                f"«{entry.name}» — это файл. Используйте «Скачать выбранный»."
            )
            return
        if self.files.list_directory(child_path(self.files.path, entry.name)):
            self.files_status.setText("Чтение папки…")

    def download_selected_file(self) -> None:
        entry = self._selected_entry()
        if entry is None:
            QMessageBox.information(self, "Файлы", "Выберите файл в списке.")
            return
        if entry.is_directory:
            QMessageBox.information(
                self, "Файлы", "Скачивание папок целиком не поддерживается."
            )
            return
        target, _filter = QFileDialog.getSaveFileName(
            self, "Сохранить файл", str(Path.home() / entry.name)
        )
        if not target:
            return
        if not self.files.download(entry.name, target):
            self.files_status.setText("Соединение закрыто, скачивание не начато.")
            return
        self.files_busy = True
        self._update_files_controls()

    def upload_file(self) -> None:
        if not self.files.ready:
            QMessageBox.information(
                self, "Файлы", "Соединение с устройством ещё не открыто."
            )
            return
        source, _filter = QFileDialog.getOpenFileName(
            self, "Выберите файл для загрузки", str(Path.home())
        )
        if not source:
            return
        if not self.files.upload(source):
            self.files_status.setText("Соединение закрыто, загрузка не начата.")
            return
        self.files_busy = True
        self._update_files_controls()

    def _selected_entry(self) -> RemoteEntry | None:
        row = self.files_table.currentRow()
        if 0 <= row < len(self.files_entries):
            return self.files_entries[row]
        return None

    def _show_listing(self, path: str, entries: object) -> None:
        items = list(entries) if isinstance(entries, list) else []
        self.files_entries = items
        self.files_path.setText(f"Папка: {path}")
        self.files_table.setRowCount(len(items))
        for row, entry in enumerate(items):
            self.files_table.setItem(row, 0, QTableWidgetItem(entry.name))
            self.files_table.setItem(row, 1, QTableWidgetItem(entry.kind_label))
            self.files_table.setItem(row, 2, QTableWidgetItem(entry.size_label))
            self.files_table.setItem(row, 3, QTableWidgetItem(entry.permissions_label))
        self.files_status.setText(
            f"Объектов: {len(items)}" if items else "Папка пуста"
        )
        self._update_files_controls()

    def _files_status_changed(self, status: str) -> None:
        self.files_status.setText(f"Состояние: {sftp_status_label(status)}")
        self._update_files_controls()

    def _files_failed(self, message: str) -> None:
        was_busy = self.files_busy
        self.files_busy = False
        self.files_status.setText(message)
        self._update_files_controls()
        # Отказ листинга виден в строке состояния, а прерванная передача —
        # это уже потерянная работа пользователя, о ней говорим отдельно.
        if was_busy:
            QMessageBox.warning(self, "Передача не выполнена", message)

    def _files_transfer_finished(self, message: str) -> None:
        self.files_busy = False
        self.files_status.setText(message)
        self._update_files_controls()
        QMessageBox.information(self, "Передача завершена", message)

    def _update_files_controls(self) -> None:
        available = self.files.ready and not self.files_busy
        for button in (
            self.files_up_button,
            self.files_refresh_button,
            self.files_download_button,
            self.files_upload_button,
        ):
            button.setEnabled(available)
        self.files_table.setEnabled(not self.files_busy)

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
        self.overview_labels["temperature"].setText(overview.temperature)
        self.overview_labels["tailscale"].setText(overview.tailscale)
        self.overview_labels["wireguard"].setText(overview.wireguard)

    def refresh_services(self) -> None:
        self._start(
            lambda: list_services(self.device, self.credentials),
            self._show_services,
            "Получение списка сервисов…",
        )

    def _show_services(self, value: object) -> None:
        if not isinstance(value, tuple) or len(value) != 2:
            return
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
        if self.files_busy:
            QMessageBox.information(
                self,
                "Передача выполняется",
                "Дождитесь завершения передачи файла.",
            )
            return
        self._disconnect_sessions()
        super().reject()

    def _disconnect_sessions(self) -> None:
        """Отписывается от менеджера: он переживёт этот диалог."""
        for signal, slot in (
            (self.sessions.status_changed, self._background_status_changed),
            (self.sessions.line_received, self._background_line),
            (self.files.listing_received, self._show_listing),
            (self.files.status_changed, self._files_status_changed),
            (self.files.operation_failed, self._files_failed),
            (self.files.progress_changed, self.files_status.setText),
            (self.files.transfer_finished, self._files_transfer_finished),
        ):
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                # Подписки уже нет: диалог закрывают повторно.
                pass
        # SFTP-соединение принадлежит окну и не должно его пережить;
        # отписка идёт первой, чтобы ответы уже закрытой сессии не пришли
        # в уничтоженные виджеты.
        self.files.shutdown()
