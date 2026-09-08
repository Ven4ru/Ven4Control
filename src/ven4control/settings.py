"""Настройки приложения: тема интерфейса и (независимо) тема терминала.

Хранится рядом с остальными данными Ven4Control (см. paths.py) — тот же
принцип «всё в одном дереве», что у devices.db и ключа приложения.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from ven4control.paths import APP_DIR
from ven4control.theme import DEFAULT_THEME, build_palette


SETTINGS_PATH = APP_DIR / "settings.json"

# Терминал по умолчанию красится в тему приложения — этот же принцип уже
# был в фазе 1 (main() красил терминал в settings.theme), sync только
# делает его явным и переопределяемым, а не единственным поведением.
TERMINAL_THEME_SYNC = "sync"


@dataclass(slots=True)
class AppSettings:
    theme: str = DEFAULT_THEME
    terminal_theme: str = TERMINAL_THEME_SYNC


def load_settings(path: Path = SETTINGS_PATH) -> AppSettings:
    """Читает настройки. Отсутствующий или битый файл — тихо настройки по умолчанию.

    Тот же принцип отказоустойчивости, что у
    DeviceStorage._restore_missing_columns: старое или отсутствующее
    состояние не должно ронять запуск приложения. Файл, сохранённый до
    появления terminal_theme (фаза 1 хранила только theme), тоже читается
    нормально — отсутствующий ключ трактуется как TERMINAL_THEME_SYNC.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return AppSettings()
    if not isinstance(raw, dict):
        return AppSettings()
    theme = raw.get("theme")
    terminal_theme = raw.get("terminal_theme")
    return AppSettings(
        theme=theme if isinstance(theme, str) else DEFAULT_THEME,
        terminal_theme=(
            terminal_theme if isinstance(terminal_theme, str) else TERMINAL_THEME_SYNC
        ),
    )


def terminal_palette(settings: AppSettings) -> dict[str, str]:
    """Палитра терминала: своя тема, если выбрана явно, иначе — тема приложения."""
    theme = settings.theme if settings.terminal_theme == TERMINAL_THEME_SYNC else settings.terminal_theme
    return build_palette(theme)


def save_settings(settings: AppSettings, path: Path = SETTINGS_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(asdict(settings), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
