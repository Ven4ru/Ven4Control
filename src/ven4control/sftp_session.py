"""Просмотр и передача файлов устройства по SFTP.

Навигация по папкам — это десятки коротких действий одного пользователя за
одну сессию, поэтому соединение здесь долгоживущее: тот же служебный поток
с собственным циклом asyncio, что у туннелей, фоновых журналов и встроенного
терминала. Переоткрывать SSH на каждый клик по папке означало бы полный
handshake со сверкой fingerprint ради одного `readdir`.

Соединение и `SFTPClient` живут, пока открыт диалог; операции выполняются
строго по очереди под общей блокировкой — так листинг не влезает в середину
передачи файла, а операции, поданные до завершения подключения, дожидаются
его сами. Наружу уходят только сигналы Qt: трогать виджеты из служебного
потока нельзя.
"""
from __future__ import annotations

import asyncio
import posixpath
import stat
import threading
from collections.abc import Callable
from dataclasses import dataclass

import asyncssh
from PySide6.QtCore import QObject, Signal

from ven4control.models import Device
from ven4control.remote_control import _connect


# Начальная папка листинга: SFTP разворачивает точку в домашнюю папку
# пользователя, и это единственный путь, который есть и на роутере, и на
# Linux-сервере, и на Windows с OpenSSH.
DEFAULT_PATH = "."

STATUS_CLOSED = "closed"
STATUS_CONNECTING = "connecting"
STATUS_READY = "ready"
STATUS_FAILED = "failed"

STATUS_LABELS: dict[str, str] = {
    STATUS_CLOSED: "соединение закрыто",
    STATUS_CONNECTING: "подключение…",
    STATUS_READY: "подключено",
    STATUS_FAILED: "ошибка",
}

KIND_DIRECTORY = "dir"
KIND_FILE = "file"
KIND_LINK = "link"
KIND_UNKNOWN = "unknown"

KIND_LABELS: dict[str, str] = {
    KIND_DIRECTORY: "папка",
    KIND_FILE: "файл",
    KIND_LINK: "ссылка",
    KIND_UNKNOWN: "неизвестно",
}

UNKNOWN_VALUE = "нет данных"

SIZE_UNITS = ("Б", "КиБ", "МиБ", "ГиБ", "ТиБ")

# Насколько часто обновлять подпись прогресса: колбэк передачи вызывается
# на каждый блок, а перерисовывать строку состояния десятки раз в секунду
# незачем.
PROGRESS_STEP = 1 << 20

CONNECTION_LOST = "соединение с устройством потеряно"


def sftp_status_label(status: str) -> str:
    """Подпись состояния соединения для интерфейса."""
    return STATUS_LABELS.get(status, status)


def decode_name(value: object) -> str:
    """Имя файла в виде строки.

    Имена приходят байтами, если сервер не объявил кодировку; кириллица в
    именах на устройствах встречается, и падать на ней нельзя.
    """
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def kind_from_mode(mode: int | None) -> str:
    """Тип записи по режиму файла. None — сервер режим не прислал."""
    if mode is None:
        return KIND_UNKNOWN
    if stat.S_ISDIR(mode):
        return KIND_DIRECTORY
    if stat.S_ISLNK(mode):
        return KIND_LINK
    return KIND_FILE


def permissions_from_mode(mode: int | None) -> str:
    """Права в привычном виде `-rwxr-xr-x`. Пустая строка — режим неизвестен."""
    if mode is None:
        return ""
    return stat.filemode(mode)


def format_size(size: int | None) -> str:
    """Размер в человекочитаемом виде."""
    if size is None:
        return UNKNOWN_VALUE
    value = float(size)
    for unit in SIZE_UNITS:
        if value < 1024 or unit == SIZE_UNITS[-1]:
            if unit == SIZE_UNITS[0]:
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} {SIZE_UNITS[-1]}"


@dataclass(frozen=True, slots=True)
class RemoteEntry:
    """Одна запись удалённой папки в том виде, в каком её показывают."""

    name: str
    kind: str = KIND_FILE
    size: int | None = None
    permissions: str = ""

    @property
    def is_directory(self) -> bool:
        return self.kind == KIND_DIRECTORY

    @property
    def can_enter(self) -> bool:
        """Стоит ли пытаться войти внутрь по двойному щелчку.

        Симлинк и запись без режима могут оказаться папкой: узнать это
        заранее нельзя, а попытка листинга стоит одного запроса.
        """
        return self.kind in (KIND_DIRECTORY, KIND_LINK, KIND_UNKNOWN)

    @property
    def kind_label(self) -> str:
        return KIND_LABELS.get(self.kind, self.kind)

    @property
    def size_label(self) -> str:
        if self.is_directory:
            return "—"
        return format_size(self.size)

    @property
    def permissions_label(self) -> str:
        return self.permissions or UNKNOWN_VALUE


def entry_from_name(name: object) -> RemoteEntry:
    """Превращает запись листинга asyncssh в строку для таблицы."""
    attrs = getattr(name, "attrs", None)
    mode = getattr(attrs, "permissions", None)
    size = getattr(attrs, "size", None)
    return RemoteEntry(
        name=decode_name(getattr(name, "filename", "")),
        kind=kind_from_mode(mode),
        size=size if isinstance(size, int) else None,
        permissions=permissions_from_mode(mode),
    )


def parse_listing(names: object) -> list[RemoteEntry]:
    """Разбирает ответ `readdir` в список записей.

    Точка и две точки отбрасываются: подъём наверх сделан отдельной кнопкой,
    и строка «..» в таблице только сбивала бы сортировку.
    """
    entries: list[RemoteEntry] = []
    for name in names or ():
        entry = entry_from_name(name)
        if not entry.name or entry.name in (".", ".."):
            continue
        entries.append(entry)
    entries.sort(key=lambda item: (not item.is_directory, item.name.lower()))
    return entries


def normalize_path(path: str) -> str:
    """Приводит путь к каноничному виду без «..» и лишних слэшей."""
    normalized = posixpath.normpath(path or DEFAULT_PATH)
    # normpath намеренно сохраняет ведущий двойной слэш (POSIX разрешает
    # ему особый смысл), но SFTP-серверам он не нужен.
    if normalized.startswith("//"):
        normalized = "/" + normalized.lstrip("/")
    return normalized or DEFAULT_PATH


def child_path(current: str, name: str) -> str:
    """Путь вложенной записи."""
    return normalize_path(posixpath.join(current or DEFAULT_PATH, name))


def parent_path(current: str) -> str:
    """Путь на уровень выше. Выше корня подняться нельзя."""
    return normalize_path(posixpath.join(current or DEFAULT_PATH, ".."))


def is_connection_lost(error: BaseException) -> bool:
    """Разрыв связи или отказ операции при живом соединении."""
    if isinstance(error, (asyncssh.SFTPConnectionLost, asyncssh.SFTPNoConnection)):
        return True
    if isinstance(error, asyncssh.SFTPError):
        return False
    return isinstance(error, (ConnectionError, EOFError, asyncssh.Error))


def describe_error(error: BaseException) -> str:
    """Человеческая причина отказа."""
    if isinstance(error, asyncssh.SFTPPermissionDenied):
        return "нет прав доступа"
    if isinstance(error, (asyncssh.SFTPNoSuchFile, asyncssh.SFTPNoSuchPath)):
        return "файл или папка не найдены — возможно, их удалили с устройства"
    if isinstance(error, asyncssh.SFTPNotADirectory):
        return "это не папка"
    if is_connection_lost(error):
        return CONNECTION_LOST
    return str(error) or error.__class__.__name__


def failure_message(action: str, target: str, error: BaseException) -> str:
    """Текст отказа для строки состояния."""
    return f"Не удалось {action} «{target}»: {describe_error(error)}."


def start_message(action: str, name: str) -> str:
    return f"{action} «{name}»…"


def progress_message(action: str, name: str, done: int, total: int | None) -> str:
    """Подпись хода передачи."""
    if not total:
        return f"{action} «{name}»: передано {format_size(done)}"
    percent = min(100, int(done * 100 / total))
    return (
        f"{action} «{name}»: {percent}% "
        f"({format_size(done)} из {format_size(total)})"
    )


def is_reportable(done: int, total: int | None, last: int) -> bool:
    """Обновлять ли подпись прогресса после очередного блока."""
    if done <= last:
        return False
    if total and done >= total:
        return True
    return done - last >= PROGRESS_STEP


class SftpSession(QObject):
    """Одно SFTP-соединение с устройством на время работы с файлами."""

    listing_received = Signal(str, object)
    status_changed = Signal(str)
    operation_failed = Signal(str)
    progress_changed = Signal(str)
    transfer_finished = Signal(str)

    def __init__(
        self,
        device: Device,
        credentials: dict[str, str],
        *,
        connect: Callable[[Device, dict[str, str]], object] | None = None,
    ):
        super().__init__()
        self.device = device
        self.credentials = dict(credentials)
        self.status = STATUS_CLOSED
        self.error = ""
        self.path = DEFAULT_PATH
        self._connect = connect or _connect
        self._connection: object | None = None
        self._client: object | None = None
        self._started = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._loop_ready = threading.Event()
        # Блокировка создаётся заранее: в Python 3.10+ она не привязана
        # к циклу событий, а операции могут прийти раньше подключения.
        self._lock = asyncio.Lock()

    # --- служебный цикл событий -------------------------------------------

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is not None and not self._loop.is_closed():
            return self._loop
        self._loop_ready.clear()
        loop = asyncio.new_event_loop()
        self._loop = loop
        self._thread = threading.Thread(
            target=self._run_loop,
            args=(loop,),
            name="ven4control-sftp",
            daemon=True,
        )
        self._thread.start()
        self._loop_ready.wait(5)
        return loop

    def _run_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        asyncio.set_event_loop(loop)
        loop.call_soon(self._loop_ready.set)
        try:
            loop.run_forever()
        finally:
            loop.close()

    def _submit(self, factory: Callable[[], object]) -> bool:
        """Ставит операцию в очередь служебного цикла."""
        loop = self._loop
        if loop is None or loop.is_closed():
            return False
        try:
            asyncio.run_coroutine_threadsafe(factory(), loop)
        except RuntimeError:
            # Цикл уже остановлен: диалог закрывают.
            return False
        return True

    # --- управление соединением -------------------------------------------

    def start(self) -> None:
        """Открывает соединение. Повторный вызов ничего не делает."""
        if self._started:
            return
        if not self.device.fingerprint:
            raise ValueError(
                "Для устройства не сохранён SSH fingerprint. "
                "Переустановите ключ Ven4Control и повторите."
            )
        self._started = True
        loop = self._ensure_loop()
        self._set_status(STATUS_CONNECTING)
        asyncio.run_coroutine_threadsafe(self._open(), loop)

    def list_directory(self, path: str | None = None) -> bool:
        """Запрашивает содержимое папки (по умолчанию — текущей)."""
        target = self.path if path is None else path
        return self._submit(lambda: self._list(target))

    def download(self, name: str, local_path: str) -> bool:
        """Скачивает файл из текущей папки в указанный локальный путь."""
        return self._submit(lambda: self._download(name, local_path))

    def upload(self, local_path: str) -> bool:
        """Загружает локальный файл в текущую удалённую папку."""
        return self._submit(lambda: self._upload(local_path))

    def shutdown(self, timeout: float = 5.0) -> None:
        """Закрывает соединение и останавливает служебный цикл."""
        loop = self._loop
        if loop is not None and not loop.is_closed():
            future = asyncio.run_coroutine_threadsafe(self._close(), loop)
            try:
                future.result(timeout)
            except Exception:
                # Окно закрывают: неудача закрытия не должна мешать выходу.
                pass
            loop.call_soon_threadsafe(loop.stop)
        self._loop = None
        self._thread = None
        self._started = False

    @property
    def ready(self) -> bool:
        return self.status == STATUS_READY

    # --- операции ----------------------------------------------------------

    async def _open(self) -> None:
        async with self._lock:
            try:
                connection = await self._connect(self.device, self.credentials)
                self._connection = connection
                self._client = await connection.start_sftp_client()
            except Exception as error:
                self.error = describe_error(error)
                self._set_status(STATUS_FAILED)
                self.operation_failed.emit(
                    f"Файлы устройства недоступны: {self.error}."
                )
                return
            self._set_status(STATUS_READY)

    async def _list(self, path: str) -> None:
        async with self._lock:
            client = self._client
            if client is None:
                self._report_no_connection()
                return
            try:
                resolved = decode_name(await client.realpath(path))
                names = await client.readdir(resolved)
            except Exception as error:
                self._report_failure(failure_message("открыть", path, error), error)
                return
            self.path = resolved
        self.listing_received.emit(resolved, parse_listing(names))

    async def _download(self, name: str, local_path: str) -> None:
        async with self._lock:
            client = self._client
            if client is None:
                self._report_no_connection()
                return
            remote_path = child_path(self.path, name)
            self.progress_changed.emit(start_message("Скачивание", name))
            try:
                await client.get(
                    remote_path,
                    local_path,
                    progress_handler=self._progress("Скачивание", name),
                )
            except Exception as error:
                self._report_failure(failure_message("скачать", name, error), error)
                return
        self.transfer_finished.emit(f"Файл «{name}» сохранён: {local_path}")

    async def _upload(self, local_path: str) -> None:
        name = posixpath.basename(local_path.replace("\\", "/"))
        async with self._lock:
            client = self._client
            if client is None:
                self._report_no_connection()
                return
            remote_path = child_path(self.path, name)
            self.progress_changed.emit(start_message("Загрузка", name))
            try:
                await client.put(
                    local_path,
                    remote_path,
                    progress_handler=self._progress("Загрузка", name),
                )
            except Exception as error:
                self._report_failure(failure_message("загрузить", name, error), error)
                return
            current = self.path
        self.transfer_finished.emit(f"Файл «{name}» загружен: {remote_path}")
        # Обновление после блокировки: листинг берёт её сам.
        await self._list(current)

    async def _cancel_pending(self) -> None:
        """Снимает незавершённые операции: окно закрывают, ждать их некому.

        Без этого повисшее подключение к недоступному устройству остаётся
        в остановленном цикле и выпадает предупреждением при выходе.
        """
        current = asyncio.current_task()
        pending = [task for task in asyncio.all_tasks() if task is not current]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    async def _close(self) -> None:
        await self._cancel_pending()
        client, self._client = self._client, None
        connection, self._connection = self._connection, None
        if client is not None:
            try:
                client.exit()
            except (OSError, asyncssh.Error):
                # Соединение уже разорвано: закрывать нечего.
                pass
        if connection is not None:
            try:
                connection.close()
                await connection.wait_closed()
            except (OSError, asyncssh.Error):
                pass
        if self.status != STATUS_FAILED:
            self._set_status(STATUS_CLOSED)

    # --- вспомогательное ---------------------------------------------------

    def _progress(self, action: str, name: str) -> Callable[..., None]:
        """Колбэк передачи: подпись обновляется не чаще, чем раз в блок."""
        state = {"last": 0}

        def handler(_source, _target, done: int, total: int | None) -> None:
            if not is_reportable(done, total, state["last"]):
                return
            state["last"] = done
            self.progress_changed.emit(progress_message(action, name, done, total))

        return handler

    def _report_failure(self, message: str, error: BaseException) -> None:
        if is_connection_lost(error):
            self.error = describe_error(error)
            self._client = None
            self._set_status(STATUS_FAILED)
        self.operation_failed.emit(message)

    def _report_no_connection(self) -> None:
        reason = self.error or "соединение не открыто"
        self.operation_failed.emit(f"Нет соединения с устройством: {reason}.")

    def _set_status(self, status: str) -> None:
        self.status = status
        self.status_changed.emit(status)
