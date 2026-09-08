"""Интерактивная SSH-сессия с PTY внутри приложения.

В отличие от кнопки «Открыть терминал», запускающей внешний `ssh.exe`,
здесь оболочка устройства живёт в самом приложении: тот же ключ, та же
обязательная сверка fingerprint и тот же asyncssh, что и у остального
управления. Наружу не запускается ни одного процесса.

Жизненный цикл устроен как у RDP-туннелей и фоновых сессий логирования:
служебный поток с собственным циклом asyncio (Qt своего не даёт),
обращение к нему через `run_coroutine_threadsafe` и `call_soon_threadsafe`,
а вывод уходит в интерфейс сигналами Qt — трогать виджеты из фонового
потока нельзя. Общего кода с ними намеренно нет: совпадает только каркас
цикла, а содержимое разное — там порт-форвард и стрим журнала, здесь
двусторонний канал с клавиатурой.
"""
from __future__ import annotations

import asyncio
import codecs
import threading
from collections.abc import Callable

import asyncssh
from PySide6.QtCore import QObject, Signal

from ven4control.ansi_screen import DEFAULT_COLUMNS, DEFAULT_ROWS, normalize_size
from ven4control.models import Device
from ven4control.remote_control import _connect


TERM_TYPE = "xterm-256color"

# Сколько байт забирать из канала за раз.
READ_CHUNK = 65536

STATUS_CLOSED = "closed"
STATUS_CONNECTING = "connecting"
STATUS_ACTIVE = "active"
STATUS_FAILED = "failed"

STATUS_LABELS: dict[str, str] = {
    STATUS_CLOSED: "сессия закрыта",
    STATUS_CONNECTING: "подключение…",
    STATUS_ACTIVE: "подключено",
    STATUS_FAILED: "ошибка",
}


def terminal_status_label(status: str) -> str:
    """Подпись состояния сессии для интерфейса."""
    return STATUS_LABELS.get(status, status)


def pty_options(columns: int, rows: int) -> dict[str, object]:
    """Параметры интерактивного канала с псевдотерминалом.

    Кодировка отключена намеренно: декодировать нужно нарастающим
    декодером на стороне приложения, иначе UTF-8, разрезанный между
    пакетами, поднимал бы ошибку прямо в середине вывода.
    """
    safe_columns, safe_rows = normalize_size(columns, rows)
    return {
        "term_type": TERM_TYPE,
        "term_size": (safe_columns, safe_rows),
        "encoding": None,
    }


def closing_message(session: "TerminalSession") -> str:
    """Текст, которым сессия сообщает о своём завершении."""
    if session.status == STATUS_FAILED:
        return f"Терминал «{session.device_name}» не открыт: {session.error}"
    return f"Терминал «{session.device_name}» закрыт."


class TerminalSession(QObject):
    """Одна интерактивная оболочка устройства."""

    output_received = Signal(str)
    status_changed = Signal(str)
    session_finished = Signal(str)

    def __init__(
        self,
        device: Device,
        credentials: dict[str, str],
        *,
        columns: int = DEFAULT_COLUMNS,
        rows: int = DEFAULT_ROWS,
        connect: Callable[[Device, dict[str, str]], object] | None = None,
    ):
        super().__init__()
        self.device = device
        self.device_name = device.name
        self.credentials = dict(credentials)
        self.status = STATUS_CLOSED
        self.error = ""
        self._connect = connect or _connect
        self._columns, self._rows = normalize_size(columns, rows)
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._process: object | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._loop_ready = threading.Event()
        # Событие создаётся заранее: окно могут закрыть раньше, чем корутина
        # успеет запуститься, и тогда просьбу закрыться некуда было бы деть.
        self._stop = asyncio.Event()
        self._future = None

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
            name="ven4control-terminal",
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

    # --- управление сессией ------------------------------------------------

    def start(self) -> None:
        """Открывает оболочку. Повторный вызов ничего не делает."""
        if self._future is not None:
            return
        if not self.device.fingerprint:
            raise ValueError(
                "Для устройства не сохранён SSH fingerprint. "
                "Переустановите ключ Ven4Control и повторите."
            )
        loop = self._ensure_loop()
        self._set_status(STATUS_CONNECTING)
        self._future = asyncio.run_coroutine_threadsafe(self._run(), loop)

    def send(self, text: str) -> bool:
        """Отправляет нажатия пользователя в оболочку устройства."""
        if not text:
            return False
        loop = self._loop
        if loop is None or self._process is None:
            return False
        try:
            loop.call_soon_threadsafe(self._write, text.encode("utf-8"))
        except RuntimeError:
            # Цикл событий уже остановлен: отправлять некуда.
            return False
        return True

    def resize(self, columns: int, rows: int) -> bool:
        """Передаёт новый размер окна устройству (SSH window-change)."""
        safe_columns, safe_rows = normalize_size(columns, rows)
        if (safe_columns, safe_rows) == (self._columns, self._rows):
            return False
        self._columns, self._rows = safe_columns, safe_rows
        loop = self._loop
        if loop is None or self._process is None:
            return False
        try:
            loop.call_soon_threadsafe(self._change_size, safe_columns, safe_rows)
        except RuntimeError:
            return False
        return True

    def close(self) -> bool:
        """Просит сессию завершиться."""
        loop = self._loop
        if loop is None:
            return False
        try:
            loop.call_soon_threadsafe(self._stop.set)
        except RuntimeError:
            return False
        return True

    def shutdown(self, timeout: float = 5.0) -> None:
        """Закрывает сессию и останавливает служебный цикл."""
        self.close()
        future = self._future
        if future is not None:
            try:
                future.result(timeout)
            except Exception:
                # Окно закрывают: неудача завершения не должна мешать выходу.
                pass
        loop = self._loop
        if loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(loop.stop)
        self._loop = None
        self._thread = None

    @property
    def active(self) -> bool:
        return self.status in (STATUS_CONNECTING, STATUS_ACTIVE)

    # --- сама сессия -------------------------------------------------------

    async def _run(self) -> None:
        connection = None
        process = None
        try:
            connection = await self._connect(self.device, self.credentials)
            process = await connection.create_process(
                **pty_options(self._columns, self._rows)
            )
            self._process = process
            self._set_status(STATUS_ACTIVE)
            await self._pump(process)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self.error = str(error) or error.__class__.__name__
            self._set_status(STATUS_FAILED)
        finally:
            self._process = None
            await self._release(process, connection)
            if self.status != STATUS_FAILED:
                self._set_status(STATUS_CLOSED)
            self.session_finished.emit(closing_message(self))

    async def _pump(self, process) -> None:
        """Держит канал, пока идёт вывод и пока сессию не попросили закрыться."""
        reader = asyncio.create_task(self._read(process))
        stopped = asyncio.create_task(self._stop.wait())
        tasks = {reader, stopped}
        try:
            done, _pending = await asyncio.wait(
                tasks, return_when=asyncio.FIRST_COMPLETED
            )
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        if reader in done:
            # Ошибку чтения нужно поднять наверх: она означает разрыв связи.
            reader.result()

    async def _read(self, process) -> None:
        while True:
            data = await process.stdout.read(READ_CHUNK)
            if not data:
                return
            text = self._decoder.decode(data)
            if text:
                self.output_received.emit(text)

    def _write(self, data: bytes) -> None:
        process = self._process
        if process is None:
            return
        try:
            process.stdin.write(data)
        except (OSError, asyncssh.Error):
            # Канал закрылся между нажатием клавиши и записью.
            return

    def _change_size(self, columns: int, rows: int) -> None:
        process = self._process
        if process is None:
            return
        try:
            process.change_terminal_size(columns, rows)
        except (OSError, asyncssh.Error):
            return

    async def _release(self, process, connection) -> None:
        if process is not None:
            process.close()
            waiter = getattr(process, "wait_closed", None)
            if waiter is not None:
                try:
                    await waiter()
                except (OSError, asyncssh.Error):
                    # Соединение уже разорвано: ждать больше нечего.
                    pass
        if connection is not None:
            connection.close()
            await connection.wait_closed()

    def _set_status(self, status: str) -> None:
        self.status = status
        self.status_changed.emit(status)
