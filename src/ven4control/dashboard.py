"""Дашборд: живые графики загрузки CPU и памяти на выбранных устройствах.

Каждое устройство — одна плитка с двумя графиками за последние минуты.
Сетка плиток подстраивается и под число устройств, и под ширину окна.

Данные идут потоком, а не опросом: на каждое устройство открывается одно
SSH-соединение (тот же `_connect` с обязательной сверкой отпечатка, что и
во всём приложении), а внутри него — одна долгоживущая команда, которая раз
в несколько секунд печатает сырые счётчики. Проценты считаются здесь, по
разнице двух соседних замеров: так на устройстве не нужен `sleep 1` внутри
каждой команды и не создаётся по каналу SSH на каждый замер.

Windows: CIM/WMI в неинтерактивном канале SSH рвёт соединение (см.
`remote_control.WINDOWS_PLATFORM_COMMAND`), поэтому счётчики берутся
напрямую из kernel32 (GetSystemTimes, GlobalMemoryStatusEx). Скрипт
передаётся через `powershell -EncodedCommand`, чтобы работать и там, где
оболочкой OpenSSH по умолчанию служит cmd.exe, а не PowerShell.

Все соединения живут в одном цикле asyncio на служебном потоке, как у
фонового логирования; Qt-сигналы доставляют замеры в поток интерфейса.
"""
from __future__ import annotations

import asyncio
import base64
import math
import threading
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

import asyncssh
from PySide6.QtCore import QObject, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ven4control.models import Device
from ven4control.remote_control import FingerprintError, _connect, detect_platform

SAMPLE_INTERVAL = 2  # секунды между замерами на устройстве
HISTORY_POINTS = 90  # 90 точек × 2 с = 3 минуты на графике
RECONNECT_DELAYS: tuple[int, ...] = (3, 5, 10, 20, 30)
MIN_TILE_WIDTH = 340
TILE_HEIGHT = 300

# Linux и OpenWrt: первая строка /proc/stat и нужные строки /proc/meminfo,
# затем маркер конца замера. Busybox-совместимо: только head, grep, echo, sleep.
POSIX_STREAM_COMMAND = (
    "while :; do head -n1 /proc/stat; "
    "grep -E '^(MemTotal|MemFree|MemAvailable|Buffers|Cached):' /proc/meminfo; "
    f"echo '#END'; sleep {SAMPLE_INTERVAL}; done"
)

_WINDOWS_SCRIPT = (
    "$ErrorActionPreference='Stop';"
    "Add-Type -TypeDefinition @'\n"
    "using System; using System.Runtime.InteropServices;\n"
    "public static class V4CStat {\n"
    "  [DllImport(\"kernel32.dll\")] public static extern bool GetSystemTimes(out long idle, out long kernel, out long user);\n"
    "  [StructLayout(LayoutKind.Sequential)] public class Mem {\n"
    "    public uint Length = 64; public uint Load; public ulong TotalPhys; public ulong AvailPhys;\n"
    "    public ulong TotalPage; public ulong AvailPage; public ulong TotalVirtual; public ulong AvailVirtual; public ulong AvailExt; }\n"
    "  [DllImport(\"kernel32.dll\")] public static extern bool GlobalMemoryStatusEx([In, Out] Mem m);\n"
    "}\n"
    "'@\n"
    "while ($true) {"
    " [long]$i = 0; [long]$k = 0; [long]$u = 0;"
    " [void][V4CStat]::GetSystemTimes([ref]$i, [ref]$k, [ref]$u);"
    " $m = New-Object 'V4CStat+Mem'; [void][V4CStat]::GlobalMemoryStatusEx($m);"
    " [Console]::Out.WriteLine(('WIN {0} {1} {2} {3} {4}' -f $i, $k, $u, $m.TotalPhys, $m.AvailPhys));"
    " [Console]::Out.Flush();"
    f" Start-Sleep -Seconds {SAMPLE_INTERVAL} }}"
)


def windows_stream_command() -> str:
    """Команда для Windows: скрипт в Base64 (UTF-16LE), как требует -EncodedCommand."""
    encoded = base64.b64encode(_WINDOWS_SCRIPT.encode("utf-16-le")).decode("ascii")
    return f"powershell -NoProfile -NonInteractive -EncodedCommand {encoded}"


# ---------------------------------------------------------------- расчёты ---

@dataclass(slots=True)
class RawSample:
    """Сырые счётчики одного замера: CPU — накопленные такты, память — байты."""

    cpu_total: int
    cpu_idle: int
    mem_total: int
    mem_available: int


def parse_posix_sample(lines: Iterable[str]) -> RawSample | None:
    """Разбирает один замер POSIX: строку `cpu …` и строки /proc/meminfo.

    Простой CPU = idle + iowait; «всего» — сумма первых восьми полей (guest
    уже входит в user, его прибавлять нельзя). Если ядро не знает
    MemAvailable (старые прошивки), доступная память считается как
    MemFree + Buffers + Cached.
    """
    cpu: list[int] | None = None
    mem: dict[str, int] = {}
    for line in lines:
        parts = line.split()
        if not parts:
            continue
        if parts[0] == "cpu":
            try:
                cpu = [int(value) for value in parts[1:9]]
            except ValueError:
                return None
        elif parts[0].endswith(":") and len(parts) >= 2:
            try:
                mem[parts[0][:-1]] = int(parts[1]) * 1024
            except ValueError:
                continue
    if not cpu or len(cpu) < 4 or "MemTotal" not in mem:
        return None
    idle = cpu[3] + (cpu[4] if len(cpu) > 4 else 0)
    available = mem.get("MemAvailable")
    if available is None:
        available = mem.get("MemFree", 0) + mem.get("Buffers", 0) + mem.get("Cached", 0)
    return RawSample(sum(cpu), idle, mem["MemTotal"], available)


def parse_windows_sample(line: str) -> RawSample | None:
    """Разбирает строку `WIN idle kernel user totalPhys availPhys`.

    В GetSystemTimes время ядра уже включает простой, поэтому «всего» —
    это kernel + user, а не idle + kernel + user.
    """
    parts = line.split()
    if len(parts) != 6 or parts[0] != "WIN":
        return None
    try:
        idle, kernel, user, total, available = (int(value) for value in parts[1:])
    except ValueError:
        return None
    return RawSample(kernel + user, idle, total, available)


def cpu_percent(previous: RawSample | None, current: RawSample) -> float | None:
    """Загрузка CPU между двумя замерами; None — для первого замера."""
    if previous is None:
        return None
    total = current.cpu_total - previous.cpu_total
    idle = current.cpu_idle - previous.cpu_idle
    if total <= 0:
        return None
    return max(0.0, min(100.0, 100.0 * (total - idle) / total))


def memory_percent(sample: RawSample) -> float:
    if sample.mem_total <= 0:
        return 0.0
    used = sample.mem_total - sample.mem_available
    return max(0.0, min(100.0, 100.0 * used / sample.mem_total))


def format_memory(sample: RawSample) -> str:
    """«1,2 из 1,9 ГБ» или «820 из 980 МБ» — что короче читается."""
    used = max(0, sample.mem_total - sample.mem_available)
    if sample.mem_total >= 2 * 1024**3:
        unit, divisor = "ГБ", 1024**3
        text = f"{used / divisor:.1f} из {sample.mem_total / divisor:.1f} {unit}"
    else:
        unit, divisor = "МБ", 1024**2
        text = f"{used / divisor:.0f} из {sample.mem_total / divisor:.0f} {unit}"
    return text.replace(".", ",")


def grid_columns(count: int, available_width: int, min_tile_width: int = MIN_TILE_WIDTH) -> int:
    """Число колонок сетки: квадратная раскладка, но не уже минимальной плитки.

    1 устройство — одна плитка; 2–4 — две колонки; 5–9 — три; и так далее.
    Узкое окно уменьшает число колонок, плитки уходят на новые строки.
    """
    if count <= 0:
        return 1
    square = math.ceil(math.sqrt(count))
    fits = max(1, available_width // max(1, min_tile_width))
    return max(1, min(count, square, fits))


# ------------------------------------------------------------ сбор данных ---

@dataclass(slots=True)
class _Session:
    device: Device
    credentials: dict[str, str]
    task: asyncio.Future | None = None
    previous: RawSample | None = None
    buffer: list[str] = field(default_factory=list)


class DashboardPoller(QObject):
    """Держит по одному потоку замеров на устройство в служебном цикле asyncio."""

    sample = Signal(int, float, float, str)  # id, cpu %, память %, «x из y»
    state = Signal(int, str)  # id, текст состояния («подключение…», ошибка)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, name="ven4control-dashboard", daemon=True)
        self._sessions: dict[int, _Session] = {}
        self._stopping = False
        self._thread.start()

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def start(self, device: Device, credentials: dict[str, str]) -> None:
        if device.id is None or device.id in self._sessions:
            return
        session = _Session(device, credentials)
        self._sessions[device.id] = session
        session.task = asyncio.run_coroutine_threadsafe(self._watch(session), self._loop)

    def stop(self, timeout: float = 5.0) -> None:
        """Закрывает все соединения и останавливает служебный поток."""
        if self._stopping:
            return
        self._stopping = True

        async def cancel_all() -> None:
            tasks = [t for t in asyncio.all_tasks(self._loop) if t is not asyncio.current_task()]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

        try:
            asyncio.run_coroutine_threadsafe(cancel_all(), self._loop).result(timeout)
        except Exception:
            pass
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout)

    async def _watch(self, session: _Session) -> None:
        device_id = session.device.id
        assert device_id is not None
        attempt = 0
        while not self._stopping:
            self.state.emit(device_id, "подключение…")
            try:
                await self._stream(session)
                attempt = 0
                reason = "соединение закрыто"
            except asyncio.CancelledError:
                return
            except FingerprintError as error:
                self.state.emit(device_id, str(error))
                return
            except (OSError, asyncssh.Error, RuntimeError, asyncio.TimeoutError) as error:
                reason = str(error) or error.__class__.__name__
            delay = RECONNECT_DELAYS[min(attempt, len(RECONNECT_DELAYS) - 1)]
            attempt += 1
            self.state.emit(device_id, f"нет связи: {reason[:120]}. Повтор через {delay} с")
            session.previous = None
            try:
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                return

    async def _stream(self, session: _Session) -> None:
        device_id = session.device.id
        connection = await asyncio.wait_for(_connect(session.device, session.credentials), 25)
        try:
            platform, _ = await asyncio.wait_for(detect_platform(connection), 30)
            windows = platform == "windows"
            command = windows_stream_command() if windows else POSIX_STREAM_COMMAND
            process = await connection.create_process(command, errors="replace")
            self.state.emit(device_id, "")
            try:
                while not self._stopping:
                    line = await asyncio.wait_for(process.stdout.readline(), SAMPLE_INTERVAL * 6 + 20)
                    if not line:
                        stderr = (await process.stderr.read()).strip() if process.stderr else ""
                        raise RuntimeError(stderr[:160] or "устройство перестало присылать данные")
                    self._consume(session, line.rstrip("\r\n"), windows)
            finally:
                process.close()
        finally:
            connection.close()

    def _consume(self, session: _Session, line: str, windows: bool) -> None:
        if windows:
            raw = parse_windows_sample(line) if line.startswith("WIN ") else None
            if raw is None:
                return
        else:
            if line.strip() != "#END":
                session.buffer.append(line)
                return
            raw = parse_posix_sample(session.buffer)
            session.buffer.clear()
            if raw is None:
                return
        cpu = cpu_percent(session.previous, raw)
        session.previous = raw
        if cpu is None:
            return
        self.sample.emit(session.device.id, cpu, memory_percent(raw), format_memory(raw))


# --------------------------------------------------------------- интерфейс ---

class MetricChart(QWidget):
    """Линейный график 0–100 % с заливкой; последняя точка — справа."""

    def __init__(self, color: str, palette: dict[str, str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._values: deque[float] = deque(maxlen=HISTORY_POINTS)
        self._color = QColor(color)
        self._grid = QColor(palette.get("border_color", "#444444"))
        self._text = QColor(palette.get("text_secondary", "#999999"))
        self.setMinimumHeight(70)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def add(self, value: float) -> None:
        self._values.append(value)
        self.update()

    def clear(self) -> None:
        self._values.clear()
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 — имя задаёт Qt
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(28, 4, -4, -4)
        font = QFont(self.font())
        font.setPointSizeF(max(7.0, font.pointSizeF() - 2))
        painter.setFont(font)
        for level in (0, 50, 100):
            y = rect.bottom() - rect.height() * level / 100
            painter.setPen(QPen(self._grid, 1, Qt.PenStyle.DashLine if level == 50 else Qt.PenStyle.SolidLine))
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            painter.setPen(self._text)
            painter.drawText(QRectF(0, y - 8, 24, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, f"{level}")
        if len(self._values) < 2:
            return
        step = rect.width() / (HISTORY_POINTS - 1)
        start_x = rect.right() - step * (len(self._values) - 1)
        points = [
            QPointF(start_x + step * index, rect.bottom() - rect.height() * value / 100)
            for index, value in enumerate(self._values)
        ]
        line = QPainterPath(points[0])
        for point in points[1:]:
            line.lineTo(point)
        area = QPainterPath(line)
        area.lineTo(points[-1].x(), rect.bottom())
        area.lineTo(points[0].x(), rect.bottom())
        area.closeSubpath()
        fill = QColor(self._color)
        fill.setAlpha(55)
        painter.fillPath(area, fill)
        painter.setPen(QPen(self._color, 2))
        painter.drawPath(line)


class DeviceTile(QFrame):
    """Плитка устройства: имя, состояние и два графика."""

    def __init__(self, device: Device, palette: dict[str, str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("dashboardTile")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setMinimumSize(MIN_TILE_WIDTH - 20, TILE_HEIGHT)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(4)
        title = QLabel(device.name)
        title.setTextFormat(Qt.TextFormat.PlainText)
        title.setStyleSheet("font-weight: 600;")
        host = QLabel(device.host)
        host.setTextFormat(Qt.TextFormat.PlainText)
        host.setProperty("secondary", True)
        self.status = QLabel("подключение…")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        self.status.setProperty("secondary", True)
        self.cpu_label = QLabel("CPU: —")
        self.mem_label = QLabel("RAM: —")
        self.cpu_chart = MetricChart(palette.get("accent_color", "#4CC2FF"), palette)
        # Память — тёплым цветом статуса «предупреждение»: в части тем info и
        # акцент почти одного оттенка, и графики CPU и RAM сливались.
        self.mem_chart = MetricChart(palette.get("status_warning", "#F5A524"), palette)
        layout.addWidget(title)
        layout.addWidget(host)
        layout.addWidget(self.status)
        layout.addWidget(self.cpu_label)
        layout.addWidget(self.cpu_chart, 1)
        layout.addWidget(self.mem_label)
        layout.addWidget(self.mem_chart, 1)

    def show_sample(self, cpu: float, memory: float, memory_text: str) -> None:
        self.status.setVisible(False)
        self.cpu_label.setText(f"CPU: {cpu:.0f} %")
        self.mem_label.setText(f"RAM: {memory:.0f} % ({memory_text})")
        self.cpu_chart.add(cpu)
        self.mem_chart.add(memory)

    def show_state(self, text: str) -> None:
        self.status.setText(text)
        self.status.setVisible(bool(text))


class DashboardDialog(QDialog):
    """Окно дашборда. Немодальное: со списком устройств можно работать дальше."""

    def __init__(
        self,
        devices: list[Device],
        credentials_for: Callable[[Device], dict[str, str]],
        palette: dict[str, str],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Дашборд — устройств: {len(devices)}")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._tiles: dict[int, DeviceTile] = {}
        self._order: list[DeviceTile] = []
        self._columns = 0

        hint = QLabel(f"CPU и RAM обновляются каждые {SAMPLE_INTERVAL} с, на графике последние "
                      f"{HISTORY_POINTS * SAMPLE_INTERVAL // 60} мин.")
        hint.setProperty("secondary", True)
        self._grid_host = QWidget()
        self._grid = QGridLayout(self._grid_host)
        self._grid.setSpacing(10)
        self._grid.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(self._grid_host)
        self._scroll = scroll
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.addWidget(hint)
        layout.addWidget(scroll, 1)

        self._poller = DashboardPoller(self)
        self._poller.sample.connect(self._on_sample)
        self._poller.state.connect(self._on_state)
        for device in devices:
            if device.id is None:
                continue
            tile = DeviceTile(device, palette)
            self._tiles[device.id] = tile
            self._order.append(tile)
            if not device.fingerprint:
                tile.show_state("Отпечаток SSH не сохранён: подключитесь к устройству один раз из главного окна.")
                continue
            self._poller.start(device, credentials_for(device))

        columns = grid_columns(len(self._order), 10_000)
        rows = math.ceil(len(self._order) / columns) if self._order else 1
        self.resize(min(1600, columns * (MIN_TILE_WIDTH + 30) + 40), min(1000, rows * (TILE_HEIGHT + 14) + 80))
        self._relayout()

    def _relayout(self) -> None:
        # Ширина окна, а не области прокрутки: до первого показа viewport ещё
        # не растянут и почти нулевой — сетка схлопывалась в одну колонку.
        width = self.width() - 2 * 14 - 20
        columns = grid_columns(len(self._order), width)
        if columns == self._columns:
            return
        self._columns = columns
        for tile in self._order:
            self._grid.removeWidget(tile)
        for index, tile in enumerate(self._order):
            self._grid.addWidget(tile, index // columns, index % columns)
        for column in range(self._grid.columnCount()):
            self._grid.setColumnStretch(column, 1 if column < columns else 0)

    def resizeEvent(self, event) -> None:  # noqa: N802 — имя задаёт Qt
        super().resizeEvent(event)
        self._relayout()

    def showEvent(self, event) -> None:  # noqa: N802 — имя задаёт Qt
        super().showEvent(event)
        self._columns = 0
        self._relayout()

    def _on_sample(self, device_id: int, cpu: float, memory: float, memory_text: str) -> None:
        tile = self._tiles.get(device_id)
        if tile is not None:
            tile.show_sample(cpu, memory, memory_text)

    def _on_state(self, device_id: int, text: str) -> None:
        tile = self._tiles.get(device_id)
        if tile is not None:
            tile.show_state(text)

    def closeEvent(self, event) -> None:  # noqa: N802 — имя задаёт Qt
        self._poller.stop()
        super().closeEvent(event)
