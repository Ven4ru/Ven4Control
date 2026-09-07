"""Реестр фоновых сессий логирования на уровне приложения.

Менеджер живёт дольше любого окна: сессия не должна обрываться от того, что
пользователь закрыл диалог управления или свернул главное окно в трей.
Все воркеры работают в одном цикле событий asyncio на отдельном служебном
потоке — Qt своего цикла asyncio не предоставляет, а поток на устройство
означал бы возврат к схеме, от которой уходили.
"""
from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import Future
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QSettings, Signal

from ven4control.log_storage import (
    EXPORT_FORMATS,
    RawLogWriter,
    export_session,
    session_base_name,
    session_directory,
)
from ven4control.log_worker import (
    STATUS_CONNECTING,
    STATUS_STOPPED,
    LogStreamWorker,
)
from ven4control.models import Device
from ven4control.paths import LOG_DIR


EXPORT_DIRECTORY_NAME = "export"

SETTINGS_SCOPE = ("Ven4Control", "Ven4Control")
EXPORT_FORMAT_KEY = "logging/export_format"
DEFAULT_EXPORT_FORMAT = "txt"


def preferred_export_format() -> str:
    """Формат экспорта, выбранный пользователем в прошлый раз."""
    stored = str(QSettings(*SETTINGS_SCOPE).value(EXPORT_FORMAT_KEY, DEFAULT_EXPORT_FORMAT))
    return stored if stored in EXPORT_FORMATS else DEFAULT_EXPORT_FORMAT


def set_preferred_export_format(export_format: str) -> None:
    if export_format not in EXPORT_FORMATS:
        raise ValueError(f"Неизвестный формат экспорта: {export_format}")
    QSettings(*SETTINGS_SCOPE).setValue(EXPORT_FORMAT_KEY, export_format)


@dataclass(slots=True)
class LogSession:
    device_id: int
    device_name: str
    source: str
    export_format: str
    directory: Path
    base_name: str
    worker: LogStreamWorker
    writer: RawLogWriter
    status: str = STATUS_CONNECTING
    future: Future | None = field(default=None, compare=False)


class LogSessionManager(QObject):
    """Запускает, останавливает и учитывает фоновые сессии."""

    status_changed = Signal(int, str)
    line_received = Signal(int, str)
    session_finished = Signal(int, str)

    def __init__(self, root: Path = LOG_DIR):
        super().__init__()
        self._root = Path(root)
        self._sessions: dict[int, LogSession] = {}
        self._lock = threading.RLock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._loop_ready = threading.Event()

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
            name="ven4control-logs",
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

    # --- управление сессиями ----------------------------------------------

    def start(
        self,
        device: Device,
        credentials: dict[str, str],
        *,
        source: str = "system",
        export_format: str = "txt",
    ) -> LogSession:
        """Запускает сессию для устройства. Повторный вызов ничего не меняет."""
        if device.id is None:
            raise ValueError("Устройство не сохранено, сессию запускать нечего.")
        with self._lock:
            existing = self._sessions.get(device.id)
            if existing is not None:
                return existing
            moment = datetime.now()
            directory = session_directory(device.name, moment, self._root, device.id)
            base_name = session_base_name(device.name, moment, device.id)
            writer = RawLogWriter(directory, base_name)
            device_id = device.id
            worker = LogStreamWorker(
                device,
                credentials,
                writer,
                source=source,
                on_status=lambda status: self._on_status(device_id, status),
                on_line=lambda text: self.line_received.emit(device_id, text),
            )
            session = LogSession(
                device_id=device_id,
                device_name=device.name,
                source=source,
                export_format=export_format,
                directory=directory,
                base_name=base_name,
                worker=worker,
                writer=writer,
            )
            self._sessions[device_id] = session
            loop = self._ensure_loop()
            session.future = asyncio.run_coroutine_threadsafe(
                self._run_session(session), loop
            )
        self.status_changed.emit(device.id, STATUS_CONNECTING)
        return session

    def stop(self, device_id: int) -> bool:
        """Просит сессию завершиться. Экспорт выполнится в фоне."""
        with self._lock:
            session = self._sessions.get(device_id)
            loop = self._loop
        if session is None or loop is None:
            return False
        try:
            loop.call_soon_threadsafe(session.worker.request_stop)
        except RuntimeError:
            # Цикл событий уже остановлен: останавливать нечего.
            return False
        return True

    def stop_all(self, timeout: float = 15.0) -> None:
        """Останавливает все сессии и ждёт, пока журналы будут выгружены."""
        with self._lock:
            sessions = list(self._sessions.values())
        for session in sessions:
            self.stop(session.device_id)
        deadline = time.monotonic() + timeout
        for session in sessions:
            if session.future is None:
                continue
            try:
                session.future.result(max(0.0, deadline - time.monotonic()))
            except Exception:
                # Приложение закрывается: не удавшийся экспорт одной сессии
                # не должен помешать остановить остальные.
                continue

    def shutdown(self, timeout: float = 15.0) -> None:
        self.stop_all(timeout)
        loop = self._loop
        if loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(loop.stop)
        self._loop = None
        self._thread = None

    async def _run_session(self, session: LogSession) -> None:
        try:
            await session.worker.run()
        finally:
            session.writer.close()
            message = await asyncio.to_thread(self._export, session)
            with self._lock:
                self._sessions.pop(session.device_id, None)
            session.status = STATUS_STOPPED
            self.status_changed.emit(session.device_id, STATUS_STOPPED)
            self.session_finished.emit(session.device_id, message)

    def _export(self, session: LogSession) -> str:
        destination = session.directory / EXPORT_DIRECTORY_NAME
        try:
            created = export_session(
                session.writer.parts,
                destination,
                session.export_format,
                session.base_name,
            )
        except Exception as error:
            return (
                f"Журнал сессии сохранён в {session.directory}, "
                f"но экспорт не выполнен: {error}"
            )
        return (
            f"Сессия «{session.device_name}» остановлена. "
            f"Строк: {session.writer.total_lines}. "
            f"Файлов создано: {len(created)} в {destination}"
        )

    def _on_status(self, device_id: int, status: str) -> None:
        with self._lock:
            session = self._sessions.get(device_id)
            if session is not None:
                session.status = status
        self.status_changed.emit(device_id, status)

    # --- состояние ---------------------------------------------------------

    def is_active(self, device_id: int | None) -> bool:
        if device_id is None:
            return False
        with self._lock:
            return device_id in self._sessions

    def status(self, device_id: int | None) -> str:
        if device_id is None:
            return STATUS_STOPPED
        with self._lock:
            session = self._sessions.get(device_id)
        return session.status if session else STATUS_STOPPED

    def session(self, device_id: int | None) -> LogSession | None:
        if device_id is None:
            return None
        with self._lock:
            return self._sessions.get(device_id)

    def active_sessions(self) -> list[LogSession]:
        with self._lock:
            return list(self._sessions.values())

    def active_count(self) -> int:
        with self._lock:
            return len(self._sessions)


_manager: LogSessionManager | None = None


def session_manager() -> LogSessionManager:
    """Единый на приложение реестр сессий."""
    global _manager
    if _manager is None:
        _manager = LogSessionManager()
    return _manager
