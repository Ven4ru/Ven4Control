"""Фоновый стриминг журнала устройства с переподключением после разрыва.

В отличие от разового чтения (`remote_control.read_logs`) воркер держит
открытый канал `logread -f` / `journalctl -f`, переживает перезагрузку
устройства и периодически дописывает в журнал снапшот показателей системы.
Подключение выполняется тем же путём, что и остальное управление:
обязательная сверка SSH fingerprint, ключ приложения, asyncssh.

Реализация асинхронная: одна корутина на сессию, поэтому несколько устройств
живут в одном цикле событий, а не в отдельных потоках с очередями.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence

import asyncssh

from ven4control.models import Device
from ven4control.remote_control import (
    LOG_SOURCES,
    METRICS_COMMAND,
    WINDOWS_UNSUPPORTED,
    FingerprintError,
    _connect,
    _run,
    detect_platform,
    parse_metrics,
)


class UnsupportedPlatformError(RuntimeError):
    """Платформа устройства не умеет того, что просит сессия.

    Как и `FingerprintError`, переживать переподключением бессмысленно:
    на Windows команда журнала завершается сразу же, а пустой поток
    неотличим от обрыва связи — без отдельного класса ошибки сессия
    уходила в вечный цикл переподключений, дописывая маркеры разрыва
    в файл журнала на диске.
    """


# Задержки между попытками переподключения, секунды. После исчерпания списка
# повторы продолжаются с последним значением: устройство может подниматься
# долго (перепрошивка, ожидание DHCP), но опрашивать его чаще смысла нет.
RECONNECT_DELAYS: tuple[int, ...] = (2, 5, 10, 20, 30)

SNAPSHOT_INTERVAL = 30.0

# Снапшот считает CPU и память той же командой, что и вкладка «Обзор»,
# и добавляет число соединений conntrack — оно показывает нагрузку на роутер.
SNAPSHOT_COMMAND = (
    METRICS_COMMAND
    + "printf 'CONNTRACK='\n"
    + "wc -l < /proc/net/nf_conntrack 2>/dev/null || echo 'нет данных'\n"
)

SNAPSHOT_FIELDS: tuple[tuple[str, str], ...] = (
    ("UPTIME", "Время работы"),
    ("CPU", "Загрузка CPU"),
    ("MEM", "Память"),
    ("DISK", "Диск"),
    ("TEMP", "Температура"),
    ("CONNTRACK", "Соединений conntrack"),
)

STATUS_STOPPED = "stopped"
STATUS_CONNECTING = "connecting"
STATUS_STREAMING = "streaming"
STATUS_RECONNECTING = "reconnecting"
STATUS_FAILED = "failed"

STATUS_LABELS: dict[str, str] = {
    STATUS_STOPPED: "остановлено",
    STATUS_CONNECTING: "подключение…",
    STATUS_STREAMING: "стримит",
    STATUS_RECONNECTING: "переподключение…",
    STATUS_FAILED: "ошибка",
}


def status_label(status: str) -> str:
    """Подпись состояния сессии для интерфейса."""
    return STATUS_LABELS.get(status, status)


def reconnect_delay(
    attempt: int,
    delays: Sequence[float] = RECONNECT_DELAYS,
) -> float:
    """Задержка перед попыткой номер `attempt` (нумерация с нуля)."""
    if not delays:
        raise ValueError("Список задержек переподключения пуст")
    return delays[min(max(attempt, 0), len(delays) - 1)]


def build_stream_command(platform: str, source: str = "system") -> str:
    """Собирает команду непрерывного чтения журнала.

    Платформа определяет только порядок проверки: обе ветки остаются в
    команде, поэтому Linux без journalctl (busybox-контейнер) и OpenWrt
    с systemd не остаются без журнала. Windows-аналога нет ни у одной из
    них — там сессию нужно останавливать, а не переподключать.
    """
    if platform == "windows":
        raise UnsupportedPlatformError(WINDOWS_UNSUPPORTED)
    if source not in LOG_SOURCES:
        raise ValueError("Неизвестный источник журнала")
    filter_word, unit = LOG_SOURCES[source]
    if filter_word:
        openwrt = f"logread -f | grep -i --line-buffered {filter_word}"
        systemd = f"journalctl -u {unit} -f -n 0 --no-pager"
    else:
        openwrt = "logread -f"
        systemd = "journalctl -f -n 0 --no-pager"
    first, second = (openwrt, systemd) if platform == "openwrt" else (systemd, openwrt)
    first_tool = "logread" if platform == "openwrt" else "journalctl"
    second_tool = "journalctl" if platform == "openwrt" else "logread"
    return (
        f"if command -v {first_tool} >/dev/null 2>&1; then {first}; "
        f"elif command -v {second_tool} >/dev/null 2>&1; then {second}; "
        "else echo 'Журнал недоступен: нет logread и journalctl' >&2; exit 127; fi"
    )


def format_gap(seconds: float) -> str:
    """Длительность разрыва связи словами."""
    total = max(0, int(round(seconds)))
    if total < 60:
        return f"{total} с"
    if total < 3600:
        return f"{total // 60} мин {total % 60} с"
    return f"{total // 3600} ч {(total % 3600) // 60} мин"


def connected_marker(device_name: str, host: str) -> str:
    return f"=== ПОДКЛЮЧЕНО к {host} ({device_name}) ==="


def disconnected_marker(reason: object) -> str:
    detail = str(reason).strip() or "соединение закрыто"
    return f"=== ОТКЛЮЧЕНО ({detail}) ==="


def reconnected_marker(gap_seconds: float) -> str:
    return f"=== ПЕРЕПОДКЛЮЧЕНО, разрыв длился {format_gap(gap_seconds)} ==="


def stopped_marker() -> str:
    return "=== СЕССИЯ ОСТАНОВЛЕНА ==="


def format_snapshot(output: str) -> list[str]:
    """Превращает вывод SNAPSHOT_COMMAND в строки для журнала."""
    values = parse_metrics(output)
    lines = ["=== СНАПШОТ ==="]
    lines += [
        f"{caption}: {values.get(key, 'нет данных')}"
        for key, caption in SNAPSHOT_FIELDS
    ]
    lines.append("=== /СНАПШОТ ===")
    return lines


def snapshot_failure(reason: object) -> list[str]:
    return [f"=== СНАПШОТ НЕ УДАЛСЯ: {reason} ==="]


class LogStreamWorker:
    """Одна фоновая сессия логирования устройства."""

    def __init__(
        self,
        device: Device,
        credentials: dict[str, str],
        writer,
        *,
        source: str = "system",
        snapshot_interval: float = SNAPSHOT_INTERVAL,
        reconnect_delays: Sequence[float] = RECONNECT_DELAYS,
        on_status: Callable[[str], None] | None = None,
        on_line: Callable[[str], None] | None = None,
        connect: Callable[[Device, dict[str, str]], Awaitable[object]] | None = None,
    ):
        self.device = device
        self.credentials = credentials
        # Писателю достаточно уметь write_line(text, prefix_timestamp=...):
        # воркер не знает про ротацию и формат файлов.
        self.writer = writer
        self.source = source
        self.snapshot_interval = snapshot_interval
        self.reconnect_delays = tuple(reconnect_delays)
        self.on_status = on_status
        self.on_line = on_line
        self._connect = connect or _connect
        self._stop = asyncio.Event()
        self.status = STATUS_STOPPED

    def request_stop(self) -> None:
        """Просит сессию завершиться. Безопасно вызывать повторно."""
        self._stop.set()

    @property
    def stopping(self) -> bool:
        return self._stop.is_set()

    def _set_status(self, status: str) -> None:
        self.status = status
        if self.on_status:
            self.on_status(status)

    def _emit(self, text: str, prefix_timestamp: bool = True) -> None:
        self.writer.write_line(text, prefix_timestamp=prefix_timestamp)
        if self.on_line:
            self.on_line(text)

    async def run(self) -> None:
        """Держит сессию до вызова `request_stop`.

        Разрыв связи не считается ошибкой сессии: воркер пишет маркер и
        переподключается. Останавливает сессию только несовпадение
        fingerprint — доверять такому устройству нельзя.
        """
        attempt = 0
        disconnected_at: float | None = None
        try:
            while not self._stop.is_set():
                try:
                    self._set_status(
                        STATUS_RECONNECTING if disconnected_at else STATUS_CONNECTING
                    )
                    connection = await self._connect(self.device, self.credentials)
                except FingerprintError as error:
                    self._emit(f"=== СЕССИЯ ОСТАНОВЛЕНА: {error} ===")
                    self._set_status(STATUS_FAILED)
                    return
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    if disconnected_at is None:
                        disconnected_at = asyncio.get_running_loop().time()
                        self._emit(disconnected_marker(error))
                    self._set_status(STATUS_RECONNECTING)
                    if await self._wait_before_retry(attempt):
                        break
                    attempt += 1
                    continue

                if disconnected_at is not None:
                    gap = asyncio.get_running_loop().time() - disconnected_at
                    self._emit(reconnected_marker(gap))
                    disconnected_at = None
                attempt = 0
                try:
                    await self._session(connection)
                except asyncio.CancelledError:
                    raise
                except UnsupportedPlatformError as error:
                    # Переподключение ничего не изменит: команды журнала на
                    # этой платформе нет вовсе.
                    self._emit(f"=== СЕССИЯ ОСТАНОВЛЕНА: {error} ===")
                    self._set_status(STATUS_FAILED)
                    return
                except Exception as error:
                    disconnected_at = asyncio.get_running_loop().time()
                    self._emit(disconnected_marker(error))
                    self._set_status(STATUS_RECONNECTING)
                finally:
                    connection.close()
                    await connection.wait_closed()
                if self._stop.is_set():
                    break
                if disconnected_at is not None and await self._wait_before_retry(attempt):
                    break
                attempt += 1
        finally:
            if self.status != STATUS_FAILED:
                self._emit(stopped_marker())
                self._set_status(STATUS_STOPPED)

    async def _wait_before_retry(self, attempt: int) -> bool:
        """Ждёт паузу перед повтором. True — сессию попросили остановить."""
        delay = reconnect_delay(attempt, self.reconnect_delays)
        try:
            await asyncio.wait_for(self._stop.wait(), delay)
        except (TimeoutError, asyncio.TimeoutError):
            return False
        return True

    async def _session(self, connection) -> None:
        platform, _ = await detect_platform(connection)
        command = build_stream_command(platform, self.source)
        self._emit(connected_marker(self.device.name, self.device.host))
        self._set_status(STATUS_STREAMING)
        async with connection.create_process(command) as process:
            reader = asyncio.create_task(self._read_lines(process))
            snapshots = asyncio.create_task(self._snapshot_loop(connection))
            stopped = asyncio.create_task(self._stop.wait())
            tasks = {reader, snapshots, stopped}
            try:
                done, pending = await asyncio.wait(
                    tasks, return_when=asyncio.FIRST_COMPLETED
                )
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
            for task in done:
                if task is not stopped:
                    # Ошибку чтения нужно поднять наверх: она означает разрыв.
                    task.result()

    async def _read_lines(self, process) -> None:
        async for line in process.stdout:
            text = line.rstrip("\r\n")
            if text:
                self._emit(text)
        raise ConnectionError("удалённая сторона закрыла поток журнала")

    async def _snapshot_loop(self, connection) -> None:
        while True:
            await asyncio.sleep(self.snapshot_interval)
            await self._write_snapshot(connection)

    async def _write_snapshot(self, connection) -> None:
        try:
            result = await _run(connection, SNAPSHOT_COMMAND, timeout=30, check=True)
        except asyncio.CancelledError:
            raise
        except (OSError, asyncssh.Error, RuntimeError) as error:
            # Неудачный снапшот не повод рвать поток журнала: канал мог быть
            # занят, а сам стрим при этом продолжает работать.
            for line in snapshot_failure(error):
                self._emit(line)
            return
        for line in format_snapshot(result.stdout):
            self._emit(line, prefix_timestamp=False)
