"""Настройки приложения: сейчас только выбранная тема интерфейса.

Хранится рядом с остальными данными Ven4Control (см. paths.py) — тот же
принцип «всё в одном дереве», что у devices.db и ключа приложения.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from ven4control.paths import APP_DIR
from ven4control.theme import DEFAULT_THEME


SETTINGS_PATH = APP_DIR / "settings.json"


@dataclass(slots=True)
class AppSettings:
    theme: str = DEFAULT_THEME


def load_settings(path: Path = SETTINGS_PATH) -> AppSettings:
    """Читает настройки. Отсутствующий или битый файл — тихо настройки по умолчанию.

    Тот же принцип отказоустойчивости, что у
    DeviceStorage._restore_missing_columns: старое или отсутствующее
    состояние не должно ронять запуск приложения.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return AppSettings()
    theme = raw.get("theme") if isinstance(raw, dict) else None
    return AppSettings(theme=theme if isinstance(theme, str) else DEFAULT_THEME)


def save_settings(settings: AppSettings, path: Path = SETTINGS_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(asdict(settings), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
