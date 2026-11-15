"""Расчёты дашборда: разбор замеров, проценты и раскладка сетки."""
import base64

from ven4control.dashboard import (
    POSIX_STREAM_COMMAND,
    RawSample,
    cpu_percent,
    format_memory,
    grid_columns,
    memory_percent,
    parse_posix_sample,
    parse_windows_sample,
    windows_stream_command,
)

LINUX_SAMPLE = [
    "cpu  100 0 50 800 50 0 0 0 0 0",
    "MemTotal:        2000000 kB",
    "MemFree:          500000 kB",
    "MemAvailable:    1500000 kB",
    "Buffers:          100000 kB",
    "Cached:           300000 kB",
]


def test_parse_posix_sample_uses_memavailable():
    sample = parse_posix_sample(LINUX_SAMPLE)
    assert sample == RawSample(cpu_total=1000, cpu_idle=850, mem_total=2_048_000_000, mem_available=1_536_000_000)


def test_parse_posix_sample_without_memavailable_falls_back():
    lines = [line for line in LINUX_SAMPLE if not line.startswith("MemAvailable")]
    sample = parse_posix_sample(lines)
    assert sample is not None
    assert sample.mem_available == (500000 + 100000 + 300000) * 1024


def test_parse_posix_sample_rejects_incomplete():
    assert parse_posix_sample(["MemTotal: 100 kB"]) is None
    assert parse_posix_sample(["cpu 1 2 3 4"]) is None


def test_cpu_percent_between_samples():
    first = RawSample(1000, 850, 1, 0)
    second = RawSample(1200, 900, 1, 0)
    assert cpu_percent(None, second) is None
    assert cpu_percent(first, second) == 75.0
    assert cpu_percent(second, second) is None


def test_parse_windows_sample_kernel_includes_idle():
    sample = parse_windows_sample("WIN 900 1000 200 8589934592 4294967296")
    assert sample == RawSample(cpu_total=1200, cpu_idle=900, mem_total=8589934592, mem_available=4294967296)
    assert parse_windows_sample("garbage") is None
    assert parse_windows_sample("WIN 1 2 x 4 5") is None


def test_memory_percent_and_format():
    sample = RawSample(0, 0, 4 * 1024**3, 1 * 1024**3)
    assert memory_percent(sample) == 75.0
    assert format_memory(sample) == "3,0 из 4,0 ГБ"
    small = RawSample(0, 0, 1000 * 1024**2, 250 * 1024**2)
    assert format_memory(small) == "750 из 1000 МБ"


def test_grid_columns_square_and_width_limited():
    assert grid_columns(1, 2000) == 1
    assert grid_columns(2, 2000) == 2
    assert grid_columns(4, 2000) == 2
    assert grid_columns(5, 2000) == 3
    assert grid_columns(10, 2000) == 4
    assert grid_columns(10, 700) == 2
    assert grid_columns(3, 100) == 1
    assert grid_columns(0, 2000) == 1


def test_stream_commands():
    assert "/proc/stat" in POSIX_STREAM_COMMAND and "#END" in POSIX_STREAM_COMMAND
    command = windows_stream_command()
    assert command.startswith("powershell -NoProfile -NonInteractive -EncodedCommand ")
    script = base64.b64decode(command.rsplit(" ", 1)[1]).decode("utf-16-le")
    assert "GetSystemTimes" in script and "GlobalMemoryStatusEx" in script
