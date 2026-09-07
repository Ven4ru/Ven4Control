"""Реестр RDP-сессий, проброшенных внутрь доверенного SSH-канала.

Порт 3389 устройства наружу не открывается никогда: `mstsc` подключается
к 127.0.0.1 на этой машине, а трафик идёт по тому же SSH-соединению с
обязательной сверкой fingerprint, что и всё остальное управление.

Жизненный цикл устроен так же, как у фоновых сессий логирования: один
служебный поток с циклом asyncio на всё приложение (Qt своего цикла не
даёт), обращение к нему через `run_coroutine_threadsafe` и
`call_soon_threadsafe`. Общего с `LogSessionManager` кода намеренно нет:
совпадает только каркас служебного цикла, а содержимое сессий разное —
там переподключающийся стрим с экспортом, здесь порт-форвард, живущий
ровно столько, сколько открыто окно RDP.
"""
from __future__ import annotations

import asyncio
import subprocess
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass, field

from PySide6.QtCore import QObject, Signal

from ven4control.models import Device
from ven4control.remote_control import _connect


# Локальный конец туннеля. Слушаем только петлевой интерфейс: сессия должна
# быть доступна этой машине и никому больше.
LOOPBACK = "127.0.0.1"

# Как часто проверяется, не закрыл ли пользователь окно RDP.
POLL_INTERVAL = 1.0

# Сколько ждать закрытия окна после просьбы завершить сессию.
TERMINATE_TIMEOUT = 5.0

STATUS_CLOSED = "closed"
STATUS_STARTING = "starting"
STATUS_ACTIVE = "active"
STATUS_FAILED = "failed"

STATUS_LABELS: dict[str, str] = {
    STATUS_CLOSED: "закрыт",
    STATUS_STARTING: "подключение…",
    STATUS_ACTIVE: "открыт",
    STATUS_FAILED: "ошибка",
}


def tunnel_status_label(status: str) -> str:
    """Подпись состояния RDP-туннеля для интерфейса."""
    return STATUS_LABELS.get(status, status)


def mstsc_command(local_port: int) -> list[str]:
    """Аргументы `mstsc` для локального конца SSH-туннеля.

    Адрес всегда петлевой: обращаться к самому устройству по сети не нужно
    и не следует — весь RDP идёт внутри SSH-канала.
    """
    port = int(local_port)
    if not 1 <= port <= 65535:
        raise ValueError("Локальный порт туннеля вне допустимого диапазона")
    return ["mstsc.exe", f"/v:{LOOPBACK}:{port}"]


def closing_message(tunnel: "RdpTunnel") -> str:
    """Текст уведомления о закрытии сессии."""
    if tunnel.status == STATUS_FAILED:
        return f"RDP-сессия «{tunnel.device_name}» не открыта: {tunnel.error}"
    return f"RDP-сессия «{tunnel.device_name}» закрыта."


@dataclass(slots=True)
class RdpTunnel:
    device_id: int
    device_name: str
    remote_port: int
    local_port: int = 0
    status: str = STATUS_STARTING
    error: str = ""
    stop: asyncio.Event = field(default_factory=asyncio.Event, compare=False)
    future: Future | None = field(default=None, compare=False)


class RdpTunnelManager(QObject):
    """Открывает, учитывает и закрывает RDP-туннели устройств."""

    status_changed = Signal(int, str)
    tunnel_closed = Signal(int, str)

    def __init__(
        self,
        *,
        connect: Callable[[Device, dict[str, str]], object] | None = None,
        launch: Callable[[list[str]], object] | None = None,
        poll_interval: float = POLL_INTERVAL,
    ):
        super().__init__()
        self._connect = connect or _connect
        # Фолбэк не нужен: mstsc есть в любой Windows.
        self._launch = launch or subprocess.Popen
        self._poll_interval = poll_interval
        self._tunnels: dict[int, RdpTunnel] = {}
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
            name="ven4control-rdp",
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

    # --- управление туннелями ---------------------------------------------

    def start(self, device: Device, credentials: dict[str, str]) -> RdpTunnel:
        """Открывает туннель и запускает `mstsc`.

        Повторный вызов для того же устройства возвращает уже открытую
        сессию: вторая поверх первой ничего не даёт, а порт занимает.
        """
        if device.id is None:
            raise ValueError("Устройство не сохранено, туннель открывать нечего.")
        if not device.fingerprint:
            raise ValueError(
                "Для устройства не сохранён SSH fingerprint. "
                "Переустановите ключ Ven4Control и повторите."
            )
        with self._lock:
            existing = self._tunnels.get(device.id)
            if existing is not None:
                return existing
            tunnel = RdpTunnel(
                device_id=device.id,
                device_name=device.name,
                remote_port=device.rdp_port,
            )
            self._tunnels[device.id] = tunnel
            loop = self._ensure_loop()
            tunnel.future = asyncio.run_coroutine_threadsafe(
                self._run_tunnel(tunnel, device, dict(credentials)), loop
            )
        self.status_changed.emit(device.id, STATUS_STARTING)
        return tunnel

    def close(self, device_id: int) -> bool:
        """Просит сессию закрыться. Окно RDP закрывается вместе с туннелем."""
        with self._lock:
            tunnel = self._tunnels.get(device_id)
            loop = self._loop
        if tunnel is None or loop is None:
            return False
        try:
            loop.call_soon_threadsafe(tunnel.stop.set)
        except RuntimeError:
            # Цикл событий уже остановлен: закрывать нечего.
            return False
        return True

    def close_all(self, timeout: float = 10.0) -> None:
        with self._lock:
            tunnels = list(self._tunnels.values())
        for tunnel in tunnels:
            self.close(tunnel.device_id)
        deadline = time.monotonic() + timeout
        for tunnel in tunnels:
            if tunnel.future is None:
                continue
            try:
                tunnel.future.result(max(0.0, deadline - time.monotonic()))
            except Exception:
                # Приложение закрывается: неудача одной сессии не должна
                # мешать закрыть остальные.
                continue

    def shutdown(self, timeout: float = 10.0) -> None:
        self.close_all(timeout)
        loop = self._loop
        if loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(loop.stop)
        self._loop = None
        self._thread = None

    # --- сама сессия -------------------------------------------------------

    async def _run_tunnel(
        self,
        tunnel: RdpTunnel,
        device: Device,
        credentials: dict[str, str],
    ) -> None:
        connection = None
        listener = None
        process = None
        try:
            connection = await self._connect(device, credentials)
            # Порт 0 — свободный локальный порт назначает система; настоящий
            # номер читается у слушателя.
            listener = await connection.forward_local_port(
                LOOPBACK, 0, LOOPBACK, device.rdp_port
            )
            tunnel.local_port = int(listener.get_port())
            process = self._launch(mstsc_command(tunnel.local_port))
            self._set_status(tunnel, STATUS_ACTIVE)
            await self._wait_for_exit(process, tunnel.stop)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            tunnel.error = str(error)
            self._set_status(tunnel, STATUS_FAILED)
        finally:
            await self._release(connection, listener)
            with self._lock:
                self._tunnels.pop(tunnel.device_id, None)
            if tunnel.status != STATUS_FAILED:
                self._set_status(tunnel, STATUS_CLOSED)
            self.tunnel_closed.emit(tunnel.device_id, closing_message(tunnel))

    async def _wait_for_exit(self, process, stop: asyncio.Event) -> None:
        """Держит туннель, пока открыто окно RDP или не попросили закрыть."""
        while True:
            if process.poll() is not None:
                return
            try:
                await asyncio.wait_for(stop.wait(), self._poll_interval)
            except (TimeoutError, asyncio.TimeoutError):
                continue
            # Сессию закрывают снаружи: окно RDP без туннеля бесполезно.
            self._terminate(process)
            await self._wait_terminated(process)
            return

    def _terminate(self, process) -> None:
        try:
            process.terminate()
        except OSError:
            # Процесс уже завершился сам: закрывать нечего.
            return

    async def _wait_terminated(self, process) -> None:
        """Даёт окну RDP закрыться раньше, чем исчезнет туннель под ним."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + TERMINATE_TIMEOUT
        while process.poll() is None and loop.time() < deadline:
            await asyncio.sleep(0.05)

    async def _release(self, connection, listener) -> None:
        if listener is not None:
            listener.close()
            waiter = getattr(listener, "wait_closed", None)
            if waiter is not None:
                await waiter()
        if connection is not None:
            connection.close()
            await connection.wait_closed()

    def _set_status(self, tunnel: RdpTunnel, status: str) -> None:
        tunnel.status = status
        self.status_changed.emit(tunnel.device_id, status)

    # --- состояние ---------------------------------------------------------

    def is_active(self, device_id: int | None) -> bool:
        if device_id is None:
            return False
        with self._lock:
            return device_id in self._tunnels

    def status(self, device_id: int | None) -> str:
        if device_id is None:
            return STATUS_CLOSED
        with self._lock:
            tunnel = self._tunnels.get(device_id)
        return tunnel.status if tunnel else STATUS_CLOSED

    def tunnel(self, device_id: int | None) -> RdpTunnel | None:
        if device_id is None:
            return None
        with self._lock:
            return self._tunnels.get(device_id)

    def active_tunnels(self) -> list[RdpTunnel]:
        with self._lock:
            return list(self._tunnels.values())

    def active_count(self) -> int:
        with self._lock:
            return len(self._tunnels)


_manager: RdpTunnelManager | None = None


def tunnel_manager() -> RdpTunnelManager:
    """Единый на приложение реестр RDP-туннелей."""
    global _manager
    if _manager is None:
        _manager = RdpTunnelManager()
    return _manager
