# Тема интерфейса (фаза 1: цвета и настройки) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Перенести систему тем Ven4Tools (4 палитры, производные цвета по WCAG-контрасту) в Ven4Control как модуль `theme.py` + хранилище выбора `settings.py` + диалог «Настройки», и подключить их — тема применяется при старте приложения, встроенный терминал берёт цвета из активной темы.

**Architecture:** `theme.py` — чистые функции (палитра → QSS-строка), без обращения к Qt-объектам напрямую (кроме `apply_theme`, которая просто вызывает `setStyleSheet`). `settings.py` — JSON-файл рядом с остальными данными приложения (`paths.py::APP_DIR`), тот же принцип отказоустойчивости к битому/отсутствующему файлу, что у `DeviceStorage`. `SettingsDialog` в `dialogs.py` — тонкий UI поверх `theme.py`/`settings.py`.

**Tech Stack:** Python 3.12, PySide6 (QSS вместо WPF `DynamicResource`), unittest.

## Global Constraints

- Палитра — точные HEX-значения из `Ven4Tools/Services/ThemeService.cs` (4 темы × 13 базовых цветов), не пересчитывать заново.
- Дефолтная тема — `teal` (проверено: `Ven4Tools/Models/UserProfile.cs:11`).
- Код и UI-строки — на русском (докстринги/комментарии по-русски, идентификаторы кода на английском), как во всём проекте. Комментарии — только там, где решение неочевидно.
- Ни одного упоминания Claude/Anthropic/AI нигде — ни в коде, ни в commit message. Commit message — обычный текст в стиле `git log` этого репозитория, без трейлеров.
- Полный набор тестов (`.venv/Scripts/python.exe -m pytest -q`, venv в корне репозитория) остаётся зелёным на каждом коммите, включая все существующие 330.
- Тесты на Qt-объекты в этом проекте не создают настоящий `QApplication` (весь набор его избегает — только `QCoreApplication` для доставки сигналов, см. `tests/test_sftp_session.py::application()`). Функции, принимающие `QApplication`, тестируются через простую фейковую замену с методом `setStyleSheet`, не через реальный `QApplication()`.

---

## Task 1: `theme.py` — палитра, WCAG-контраст, QSS

**Files:**
- Create: `src/ven4control/theme.py`
- Test: `tests/test_theme.py`

**Interfaces:**
- Produces: `THEME_WEB = "web"`, `THEME_TEAL = "teal"`, `THEME_DARK = "dark"`, `THEME_LIGHT = "light"`, `THEMES: tuple[str, ...]`, `DEFAULT_THEME: str`, `THEME_LABELS: dict[str, str]`, `Color = tuple[int, int, int]`, `relative_luminance(color: Color) -> float`, `contrast(a: float, b: float) -> float`, `readable_on(background: Color) -> Color`, `build_palette(theme: str) -> dict[str, str]`, `stylesheet_for(theme: str) -> str`, `apply_theme(app, theme: str) -> None` (принимает любой объект с методом `setStyleSheet(str)`, не обязательно реальный `QApplication` — так тестируется без Qt-окна).

- [ ] **Step 1: Написать `theme.py`**

```python
"""Единый источник цветов интерфейса: 4 темы, как в Ven4Tools.

Палитра и формулы производных цветов перенесены из Ven4Tools
(Services/ThemeService.cs) построчно — те же 13 базовых цветов на тему,
тот же расчёт читаемого текста по WCAG-контрасту. В Qt нет WPF-овского
DynamicResource, поэтому применение другое: apply_theme собирает QSS-строку
и ставит её через setStyleSheet — виджеты, которым нужен цвет темы,
помечаются объектным именем/динамическим свойством (см. stylesheet_for),
а не читают палитру напрямую в коде.
"""
from __future__ import annotations

from dataclasses import dataclass

Color = tuple[int, int, int]

THEME_WEB = "web"
THEME_TEAL = "teal"
THEME_DARK = "dark"
THEME_LIGHT = "light"

THEMES: tuple[str, ...] = (THEME_WEB, THEME_TEAL, THEME_DARK, THEME_LIGHT)

# Реальный дефолт Ven4Tools (Models/UserProfile.cs) — не «web», как можно
# предположить по фирменному цвету сайта.
DEFAULT_THEME = THEME_TEAL

THEME_LABELS: dict[str, str] = {
    THEME_WEB: "Как на ven4tools.ru",
    THEME_TEAL: "Бирюзовая",
    THEME_DARK: "Тёмная",
    THEME_LIGHT: "Светлая",
}

# Тёмный текст поверх светлой заливки — тот же оттенок, что в Ven4Tools,
# чтобы кнопки со светлым акцентом выглядели одинаково в обоих приложениях.
_ON_LIGHT: Color = (0x06, 0x13, 0x0D)
_ON_DARK: Color = (0xFF, 0xFF, 0xFF)


@dataclass(frozen=True, slots=True)
class ThemePalette:
    """Базовые цвета одной темы. Остальное считается из них в build_palette."""

    window: Color
    sidebar: Color
    content: Color
    card: Color
    raised: Color
    text_primary: Color
    text_secondary: Color
    border: Color
    accent: Color
    success: Color
    warning: Color
    danger: Color
    info: Color


_PALETTES: dict[str, ThemePalette] = {
    THEME_WEB: ThemePalette(
        window=(0x0A, 0x16, 0x28), sidebar=(0x0D, 0x1F, 0x35),
        content=(0x08, 0x12, 0x20), card=(0x10, 0x1E, 0x34),
        raised=(0x16, 0x29, 0x4A), text_primary=(0xE8, 0xF0, 0xFE),
        text_secondary=(0x8A, 0x9B, 0xB5), border=(0x1E, 0x32, 0x50),
        accent=(0x4A, 0xDE, 0x80), success=(0x4A, 0xDE, 0x80),
        warning=(0xFB, 0xBF, 0x24), danger=(0xF8, 0x71, 0x71),
        info=(0x38, 0xBD, 0xF8),
    ),
    THEME_TEAL: ThemePalette(
        window=(0x0A, 0x0A, 0x14), sidebar=(0x0D, 0x10, 0x18),
        content=(0x0A, 0x0A, 0x14), card=(0x11, 0x16, 0x1F),
        raised=(0x15, 0x1C, 0x27), text_primary=(0xE2, 0xE8, 0xF0),
        text_secondary=(0xAA, 0xB8, 0xCE), border=(0x1E, 0x2A, 0x38),
        accent=(0x00, 0xBC, 0xD4), success=(0x4A, 0xDE, 0x80),
        warning=(0xFB, 0xBF, 0x24), danger=(0xF8, 0x71, 0x71),
        info=(0x38, 0xBD, 0xF8),
    ),
    THEME_LIGHT: ThemePalette(
        window=(0xF0, 0xF0, 0xF0), sidebar=(0xF8, 0xF8, 0xF8),
        content=(0xF5, 0xF5, 0xF5), card=(0xFF, 0xFF, 0xFF),
        raised=(0xE9, 0xE9, 0xE9), text_primary=(0x1E, 0x1E, 0x1E),
        text_secondary=(0x64, 0x64, 0x64), border=(0xDC, 0xDC, 0xDC),
        accent=(0x00, 0x78, 0xD4), success=(0x15, 0x7F, 0x35),
        warning=(0xA4, 0x5A, 0x00), danger=(0xC6, 0x28, 0x28),
        info=(0x03, 0x69, 0xA1),
    ),
    THEME_DARK: ThemePalette(
        window=(0x1E, 0x1E, 0x1E), sidebar=(0x2D, 0x2D, 0x2D),
        content=(0x25, 0x25, 0x26), card=(0x2D, 0x2D, 0x2D),
        raised=(0x3A, 0x3A, 0x3A), text_primary=(0xFF, 0xFF, 0xFF),
        text_secondary=(0xCC, 0xCC, 0xCC), border=(0x3D, 0x3D, 0x3D),
        accent=(0x4C, 0xC2, 0xFF), success=(0x4A, 0xDE, 0x80),
        warning=(0xFB, 0xBF, 0x24), danger=(0xF8, 0x71, 0x71),
        info=(0x38, 0xBD, 0xF8),
    ),
}


def _palette_for(theme: str) -> ThemePalette:
    """Палитра темы. Неизвестное имя — «Тёмная», как в Ven4Tools (default arm)."""
    return _PALETTES.get(theme, _PALETTES[THEME_DARK])


def _channel(value: int) -> float:
    v = value / 255.0
    return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4


def relative_luminance(color: Color) -> float:
    """Относительная яркость цвета по формуле WCAG 2.1."""
    r, g, b = color
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def contrast(first_luminance: float, second_luminance: float) -> float:
    """Контраст двух яркостей по формуле WCAG 2.1."""
    lighter = max(first_luminance, second_luminance)
    darker = min(first_luminance, second_luminance)
    return (lighter + 0.05) / (darker + 0.05)


def readable_on(background: Color) -> Color:
    """Белый или почти чёрный — тот, у которого выше контраст с фоном."""
    bg = relative_luminance(background)
    with_white = contrast(bg, relative_luminance(_ON_DARK))
    with_dark = contrast(bg, relative_luminance(_ON_LIGHT))
    return _ON_LIGHT if with_dark >= with_white else _ON_DARK


def _darken(color: Color) -> Color:
    """Цвет нажатой кнопки: тот же акцент, притемнённый на 18%."""
    r, g, b = color
    return (int(r * 0.82), int(g * 0.82), int(b * 0.82))


def _hex(color: Color) -> str:
    r, g, b = color
    return f"#{r:02x}{g:02x}{b:02x}"


def _rgba(color: Color, alpha: int) -> str:
    # Qt-стилшиты берут альфу как 0-255, не 0-1 (в отличие от веб-CSS).
    r, g, b = color
    return f"rgba({r}, {g}, {b}, {alpha})"


def build_palette(theme: str) -> dict[str, str]:
    """Полный набор цветов темы: базовые + производные, строками для QSS."""
    p = _palette_for(theme)
    return {
        "window_background": _hex(p.window),
        "sidebar_background": _hex(p.sidebar),
        "content_background": _hex(p.content),
        "card_background": _hex(p.card),
        "surface_raised": _hex(p.raised),
        "text_primary": _hex(p.text_primary),
        "text_secondary": _hex(p.text_secondary),
        "border_color": _hex(p.border),
        "overlay_background": _rgba(p.window, 230),
        "accent_color": _hex(p.accent),
        "accent_foreground": _hex(readable_on(p.accent)),
        "accent_pressed": _hex(_darken(p.accent)),
        "accent_hover_background": _rgba(p.accent, 20),
        "accent_soft_background": _rgba(p.accent, 36),
        "accent_soft_border": _rgba(p.accent, 64),
        "status_success": _hex(p.success),
        "status_warning": _hex(p.warning),
        "status_danger": _hex(p.danger),
        "status_info": _hex(p.info),
        "status_success_foreground": _hex(readable_on(p.success)),
        "status_warning_foreground": _hex(readable_on(p.warning)),
        "status_danger_foreground": _hex(readable_on(p.danger)),
        "status_info_foreground": _hex(readable_on(p.info)),
    }


def stylesheet_for(theme: str) -> str:
    """QSS для всего приложения, собранная из палитры темы.

    Селекторы `[nav="true"]`/`[navActive="true"]` предназначены для
    сайдбар-кнопок фаз 2/3 (главное окно, DeviceControlDialog) — заведены
    здесь, а не там, чтобы весь QSS собирался одной функцией, а не
    расползался по нескольким местам с риском разъехаться по цветам.
    """
    c = build_palette(theme)
    return f"""
QWidget {{
    background-color: {c['content_background']};
    color: {c['text_primary']};
    selection-background-color: {c['accent_soft_background']};
    selection-color: {c['text_primary']};
}}

QMainWindow, QDialog {{
    background-color: {c['window_background']};
}}

QLabel {{
    background: transparent;
}}

QLabel[secondary="true"] {{
    color: {c['text_secondary']};
}}

QLabel[eyebrow="true"] {{
    color: {c['text_secondary']};
    font-size: 9pt;
    font-weight: 600;
}}

QPushButton {{
    background-color: {c['surface_raised']};
    color: {c['text_primary']};
    border: 1px solid {c['border_color']};
    border-radius: 5px;
    padding: 6px 14px;
}}

QPushButton:hover {{
    border-color: {c['accent_color']};
}}

QPushButton:disabled {{
    color: {c['text_secondary']};
}}

QPushButton#accent {{
    background-color: {c['accent_color']};
    color: {c['accent_foreground']};
    border: none;
    font-weight: 600;
}}

QPushButton#accent:hover {{
    background-color: {c['accent_pressed']};
}}

QPushButton#accent:disabled {{
    background-color: {c['surface_raised']};
    color: {c['text_secondary']};
}}

QPushButton[nav="true"] {{
    background-color: transparent;
    color: {c['text_secondary']};
    border: none;
    border-radius: 5px;
    text-align: left;
    padding: 8px 12px;
    font-weight: 600;
}}

QPushButton[nav="true"]:hover {{
    background-color: {c['accent_hover_background']};
    color: {c['text_primary']};
}}

QPushButton[navActive="true"] {{
    background-color: {c['accent_soft_background']};
    color: {c['accent_color']};
}}

QLineEdit, QComboBox, QSpinBox, QTextEdit, QPlainTextEdit {{
    background-color: {c['card_background']};
    color: {c['text_primary']};
    border: 1px solid {c['border_color']};
    border-radius: 4px;
    padding: 4px 6px;
}}

QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{
    border-color: {c['accent_color']};
}}

QTableWidget {{
    background-color: {c['card_background']};
    gridline-color: {c['border_color']};
    border: 1px solid {c['border_color']};
}}

QTableWidget::item:selected {{
    background-color: {c['accent_soft_background']};
    color: {c['text_primary']};
}}

QHeaderView::section {{
    background-color: {c['sidebar_background']};
    color: {c['text_secondary']};
    padding: 4px;
    border: none;
    border-bottom: 1px solid {c['border_color']};
}}

QTabWidget::pane {{
    border: 1px solid {c['border_color']};
}}

QTabBar::tab {{
    background-color: {c['surface_raised']};
    color: {c['text_secondary']};
    padding: 6px 12px;
}}

QTabBar::tab:selected {{
    background-color: {c['accent_soft_background']};
    color: {c['accent_color']};
}}

QScrollBar:vertical {{
    background: transparent;
    width: 10px;
}}

QScrollBar::handle:vertical {{
    background: {c['surface_raised']};
    border-radius: 4px;
    min-height: 24px;
}}

QScrollBar::handle:vertical:hover {{
    background: {c['accent_color']};
}}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}
""".strip()


def apply_theme(app, theme: str) -> None:
    """Применяет тему ко всему приложению.

    Принимает любой объект с методом setStyleSheet(str), не обязательно
    настоящий QApplication — так функция тестируется без реального Qt-окна,
    как и весь остальной набор тестов этого проекта.
    """
    app.setStyleSheet(stylesheet_for(theme))
```

- [ ] **Step 2: Написать `tests/test_theme.py`**

```python
import unittest

from ven4control.theme import (
    DEFAULT_THEME,
    THEME_DARK,
    THEME_LIGHT,
    THEME_TEAL,
    THEME_WEB,
    THEMES,
    apply_theme,
    build_palette,
    readable_on,
    stylesheet_for,
)


class DefaultsTests(unittest.TestCase):
    def test_default_theme_is_teal(self) -> None:
        self.assertEqual("teal", DEFAULT_THEME)

    def test_four_themes_defined_in_ven4tools_order(self) -> None:
        self.assertEqual(("web", "teal", "dark", "light"), THEMES)


class PaletteKeysTests(unittest.TestCase):
    def test_all_themes_produce_the_same_key_set(self) -> None:
        """Как ThemePaletteTests в Ven4Tools: набор ключей не может разойтись."""
        key_sets = [set(build_palette(theme).keys()) for theme in THEMES]
        for keys in key_sets[1:]:
            self.assertEqual(key_sets[0], keys)

    def test_unknown_theme_falls_back_to_dark(self) -> None:
        self.assertEqual(build_palette("garbage"), build_palette(THEME_DARK))


class PaletteValuesTests(unittest.TestCase):
    def test_web_accent_matches_ven4tools_brand_color(self) -> None:
        self.assertEqual("#4ade80", build_palette(THEME_WEB)["accent_color"])

    def test_teal_accent_is_cyan(self) -> None:
        self.assertEqual("#00bcd4", build_palette(THEME_TEAL)["accent_color"])

    def test_light_theme_has_light_window_background(self) -> None:
        self.assertEqual("#f0f0f0", build_palette(THEME_LIGHT)["window_background"])

    def test_alpha_colors_use_0_255_range(self) -> None:
        # Qt QSS, не веб-CSS: alpha 0-255, не 0-1.
        hover = build_palette(THEME_TEAL)["accent_hover_background"]
        self.assertIn(", 20)", hover)


class ReadableOnTests(unittest.TestCase):
    def test_dark_background_gets_white_text(self) -> None:
        self.assertEqual((0xFF, 0xFF, 0xFF), readable_on((0x0A, 0x0A, 0x14)))

    def test_light_background_gets_dark_text(self) -> None:
        self.assertEqual((0x06, 0x13, 0x0D), readable_on((0xFF, 0xFF, 0xFF)))

    def test_web_accent_green_gets_dark_text(self) -> None:
        # Тот же случай, что в комментарии ThemeService.cs: светлый зелёный
        # акцент должен получать тёмную, не белую, надпись.
        self.assertEqual((0x06, 0x13, 0x0D), readable_on((0x4A, 0xDE, 0x80)))


class StylesheetTests(unittest.TestCase):
    def test_stylesheet_contains_accent_color_for_each_theme(self) -> None:
        for theme in THEMES:
            css = stylesheet_for(theme)
            accent = build_palette(theme)["accent_color"]
            self.assertIn(accent, css)

    def test_stylesheet_is_substantial(self) -> None:
        self.assertGreater(len(stylesheet_for(DEFAULT_THEME)), 500)


class _FakeApp:
    def __init__(self) -> None:
        self.sheet: str | None = None

    def setStyleSheet(self, sheet: str) -> None:
        self.sheet = sheet


class ApplyThemeTests(unittest.TestCase):
    def test_apply_theme_sets_the_matching_stylesheet(self) -> None:
        app = _FakeApp()
        apply_theme(app, THEME_LIGHT)
        self.assertEqual(stylesheet_for(THEME_LIGHT), app.sheet)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Прогнать тесты**

Run: `.venv\Scripts\python.exe -m pytest tests/test_theme.py -v`
Expected: все тесты PASS (14 тестов).

- [ ] **Step 4: Прогнать весь набор**

Run: `.venv\Scripts\python.exe -m pytest -q`
Expected: `344 passed` (было 330 + 14 новых).

- [ ] **Step 5: Commit**

```bash
git add src/ven4control/theme.py tests/test_theme.py
git commit -m "Добавлена система тем: палитра и QSS в стиле Ven4Tools"
```

---

## Task 2: `settings.py` — хранение выбранной темы

**Files:**
- Create: `src/ven4control/settings.py`
- Test: `tests/test_settings.py`

**Interfaces:**
- Consumes: `ven4control.paths.APP_DIR` (существующий), `ven4control.theme.DEFAULT_THEME` (из Task 1).
- Produces: `SETTINGS_PATH: Path`, `class AppSettings` (dataclass, поле `theme: str`), `load_settings(path: Path = SETTINGS_PATH) -> AppSettings`, `save_settings(settings: AppSettings, path: Path = SETTINGS_PATH) -> None`.

- [ ] **Step 1: Написать `settings.py`**

```python
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
```

- [ ] **Step 2: Написать `tests/test_settings.py`**

```python
import json
import tempfile
import unittest
from pathlib import Path

from ven4control.settings import AppSettings, load_settings, save_settings
from ven4control.theme import DEFAULT_THEME


class SettingsRoundTripTests(unittest.TestCase):
    def test_saved_theme_is_read_back(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            save_settings(AppSettings(theme="light"), path)
            self.assertEqual("light", load_settings(path).theme)

    def test_missing_file_returns_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "does-not-exist.json"
            self.assertEqual(DEFAULT_THEME, load_settings(path).theme)

    def test_corrupt_file_returns_defaults_not_an_exception(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text("{not valid json", encoding="utf-8")
            self.assertEqual(DEFAULT_THEME, load_settings(path).theme)

    def test_file_with_wrong_shape_returns_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(json.dumps(["not", "a", "dict"]), encoding="utf-8")
            self.assertEqual(DEFAULT_THEME, load_settings(path).theme)

    def test_save_creates_parent_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "settings.json"
            save_settings(AppSettings(theme="dark"), path)
            self.assertTrue(path.exists())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Прогнать тесты**

Run: `.venv\Scripts\python.exe -m pytest tests/test_settings.py -v`
Expected: все тесты PASS (5 тестов).

- [ ] **Step 4: Прогнать весь набор**

Run: `.venv\Scripts\python.exe -m pytest -q`
Expected: `349 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/ven4control/settings.py tests/test_settings.py
git commit -m "Добавлено хранение выбранной темы"
```

---

## Task 3: `SettingsDialog` — выбор темы в UI

**Files:**
- Modify: `src/ven4control/dialogs.py`
- Test: `tests/test_dialogs.py` (создать, если не существует — проверить `ls tests/test_dialogs.py` перед началом; если существует, добавить новый класс тестов в конец файла)

**Interfaces:**
- Consumes: `ven4control.theme.{THEMES, THEME_LABELS, build_palette, apply_theme}` (Task 1), `ven4control.settings.{AppSettings, save_settings}` (Task 2).
- Produces: `class SettingsDialog(QDialog)` с полем `self.selected_theme: str`, конструктор `SettingsDialog(current_theme: str, parent=None)`.

- [ ] **Step 0: Проверить текущее содержимое `dialogs.py`**

Run: `.venv\Scripts\python.exe -c "import ven4control.dialogs as d; print([n for n in dir(d) if not n.startswith('_')])"`
Ожидается увидеть `AddDeviceDialog`, `InstructionsDialog` — новый класс добавляется в конец файла, существующие два не трогаются.

- [ ] **Step 1: Добавить импорты в начало `dialogs.py`**

В начало файла (после существующих импортов `PySide6.QtWidgets`) добавить:

```python
from ven4control.settings import AppSettings, save_settings
from ven4control.theme import THEME_LABELS, THEMES, apply_theme, build_palette
```

(Импорт `Device` из `.models` уже есть — не дублировать.)

- [ ] **Step 2: Добавить `SettingsDialog` в конец `dialogs.py`**

```python
class SettingsDialog(QDialog):
    """Выбор темы интерфейса. Выбор применяется и сохраняется сразу же."""

    def __init__(self, current_theme: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Настройки")
        self.resize(360, 320)
        self.selected_theme = current_theme

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Тема интерфейса"))

        self._buttons: dict[str, QPushButton] = {}
        for theme in THEMES:
            accent = build_palette(theme)["accent_color"]
            button = QPushButton(THEME_LABELS[theme])
            button.setCheckable(True)
            button.setChecked(theme == current_theme)
            # Полоса акцентного цвета слева — тот же приём, что у логотипа
            # в Ven4Tools: сама кнопка показывает, какой это акцент, а не
            # только название темы текстом.
            button.setStyleSheet(
                f"QPushButton {{ border-left: 4px solid {accent}; "
                "text-align: left; padding: 10px; }"
            )
            button.clicked.connect(lambda _checked, t=theme: self._select(t))
            self._buttons[theme] = button
            layout.addWidget(button)

        layout.addStretch()
        buttons_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons_box.rejected.connect(self.reject)
        layout.addWidget(buttons_box)

    def _select(self, theme: str) -> None:
        self.selected_theme = theme
        for name, button in self._buttons.items():
            button.setChecked(name == theme)
        app = QApplication.instance()
        if app is not None:
            apply_theme(app, theme)
        save_settings(AppSettings(theme=theme))
```

Убедиться, что `QApplication` импортирован в `dialogs.py` (если нет — добавить в существующий блок импортов `from PySide6.QtWidgets import (...)`).

- [ ] **Step 3: Прогнать полный набор (нет отдельного юнит-теста на сам диалог — виджеты в этом проекте не тестируются созданием реального QApplication, см. Global Constraints; корректность проверяется живым запуском в Task 4)**

Run: `.venv\Scripts\python.exe -m pytest -q`
Expected: `349 passed` (без изменений — новых тестов в этом шаге нет, только новый импортируемый класс).

- [ ] **Step 4: Commit**

```bash
git add src/ven4control/dialogs.py
git commit -m "Добавлен диалог «Настройки» с выбором темы"
```

---

## Task 4: Применение темы при старте и в терминале

**Files:**
- Modify: `src/ven4control/app.py`
- Modify: `src/ven4control/ansi_screen.py`
- Modify: `src/ven4control/terminal_dialog.py`
- Test: `tests/test_ansi_screen.py` (добавить тесты в конец существующего файла)

**Interfaces:**
- Consumes: `ven4control.theme.apply_theme` (Task 1), `ven4control.settings.load_settings` (Task 2), `ven4control.dialogs.SettingsDialog` (Task 3).
- Produces: `ven4control.ansi_screen.set_default_colors(background: str, foreground: str) -> None` — новая функция, переопределяет цвета, на которые опирается инверсия SGR (`style_to_css`) и которые читает `terminal_dialog.py` для фона `QTextEdit`.

- [ ] **Step 1: Сделать цвета терминала переопределяемыми — `ansi_screen.py`**

Сейчас в файле:
```python
DEFAULT_FOREGROUND = "#d8d8d8"
DEFAULT_BACKGROUND = "#101014"
```

Заменить на:

```python
DEFAULT_FOREGROUND = "#d8d8d8"
DEFAULT_BACKGROUND = "#101014"


def set_default_colors(background: str, foreground: str) -> None:
    """Переопределяет цвета терминала по умолчанию — вызывается при открытии
    нового терминала с цветами активной темы. Уже отрисованная история
    существующих терминалов не перекрашивается задним числом: это цвета
    ПО УМОЛЧАНИЮ (инверсия SGR без явного цвета, фон нового QTextEdit),
    а не биндинг на тему.
    """
    global DEFAULT_BACKGROUND, DEFAULT_FOREGROUND
    DEFAULT_BACKGROUND = background
    DEFAULT_FOREGROUND = foreground
```

`style_to_css` и `DEFAULT_STYLE` в этом же файле уже читают `DEFAULT_BACKGROUND`/`DEFAULT_FOREGROUND` по имени модуля на момент вызова (не через захваченное на импорте значение) — проверить, что `style_to_css` обращается к ним как `DEFAULT_BACKGROUND`/`DEFAULT_FOREGROUND` внутри функции, а не через параметр по умолчанию вида `def style_to_css(style, bg=DEFAULT_BACKGROUND)` (последнее захватило бы значение один раз при определении функции и не увидело бы `set_default_colors`). Если сигнатура именно такая — переписать на чтение внутри тела функции.

- [ ] **Step 2: Написать тест на переопределение цветов**

**Та же ловушка, что в Step 4 ниже**: если проверять новое значение через
`DEFAULT_BACKGROUND`, импортированный в тестовый файл через
`from ven4control.ansi_screen import DEFAULT_BACKGROUND` (текущий стиль
файла, см. `tests/test_ansi_screen.py:3-18` — там уже есть подобный
from-импорт нескольких констант), тест проверял бы собственный
снимок-на-момент-импорта тестового модуля, а не то, что реально видит
остальной код после `set_default_colors()`. Проверять нужно через атрибут
модуля `ansi_screen.DEFAULT_BACKGROUND` — тогда чтение живое.

Добавить в начало `tests/test_ansi_screen.py` (рядом с существующим блоком
`from ven4control.ansi_screen import (...)`, не заменяя его):
```python
from ven4control import ansi_screen
from ven4control.ansi_screen import set_default_colors
```

Добавить в конец `tests/test_ansi_screen.py`:

```python
class DefaultColorOverrideTests(unittest.TestCase):
    def tearDown(self) -> None:
        # Возвращаем модуль в исходное состояние для остальных тестов файла.
        set_default_colors("#101014", "#d8d8d8")

    def test_set_default_colors_changes_the_module_values(self) -> None:
        set_default_colors("#123456", "#abcdef")
        # Через атрибут модуля, не через from-импорт — см. пояснение выше.
        self.assertEqual("#123456", ansi_screen.DEFAULT_BACKGROUND)
        self.assertEqual("#abcdef", ansi_screen.DEFAULT_FOREGROUND)

    def test_inverse_sgr_uses_the_overridden_colors(self) -> None:
        set_default_colors("#123456", "#abcdef")
        style = apply_sgr(DEFAULT_STYLE, [7])  # SGR 7 — инверсия
        css = style_to_css(style)
        # Инверсия без явного цвета берёт DEFAULT_BACKGROUND/FOREGROUND —
        # именно то место, которое должно увидеть переопределение.
        self.assertIn("color:#123456", css.replace(" ", ""))
        self.assertIn("background-color:#abcdef", css.replace(" ", ""))
```

- [ ] **Step 3: Прогнать новые тесты**

Run: `.venv\Scripts\python.exe -m pytest tests/test_ansi_screen.py -v`
Expected: все тесты PASS, включая 2 новых.

- [ ] **Step 4: Подключить цвета темы в `terminal_dialog.py`**

**Внимание, реальная ловушка**: сейчас файл делает
`from ven4control.ansi_screen import DEFAULT_BACKGROUND, DEFAULT_FOREGROUND`
(строки 21-26) — это копирует ЗНАЧЕНИЯ в момент импорта модуля в
собственное пространство имён `terminal_dialog`. `set_default_colors()`
в Task 4 Step 1 делает `global` только внутри `ansi_screen` — имя
`ansi_screen.DEFAULT_BACKGROUND` после вызова обновится, а уже
скопированные `terminal_dialog.DEFAULT_BACKGROUND`/`DEFAULT_FOREGROUND`
останутся старыми навсегда (Python `from x import y` не привязка на
модуль, а разовое копирование значения). Проверено на текущем коде —
`terminal_dialog.py:21-26`. Нужна замена на импорт модуля целиком и чтение
через него в момент использования (это уже живое чтение, не снимок).

Заменить (строки 21-26):
```python
from ven4control.ansi_screen import (
    DEFAULT_BACKGROUND,
    DEFAULT_FOREGROUND,
    TerminalScreen,
    terminal_size,
)
```
на:
```python
from ven4control import ansi_screen
from ven4control.ansi_screen import TerminalScreen, terminal_size
```

И в `TerminalView.__init__`, там где сейчас (см. текущий код, `f"background-color: {DEFAULT_BACKGROUND};"` / `f"color: {DEFAULT_FOREGROUND};"`), заменить на:
```python
            f"background-color: {ansi_screen.DEFAULT_BACKGROUND};"
            f"color: {ansi_screen.DEFAULT_FOREGROUND};"
```
(обращение через `ansi_screen.<имя>` — это чтение атрибута модуля в момент
вызова `__init__`, а не значение, скопированное при импорте, поэтому видит
результат `set_default_colors()`, вызванного в `app.py::main()` раньше.)

- [ ] **Step 5: Применить тему при старте — `app.py`**

В начало файла, в блок импортов `from ven4control...`, добавить:

```python
from ven4control.ansi_screen import set_default_colors as set_terminal_colors
from ven4control.settings import load_settings
from ven4control.theme import apply_theme, build_palette
```

В `main()` (см. текущее содержимое, строки ~1391-1413), сразу после
`app.setApplicationName("Ven4Control")`:

```python
    settings = load_settings()
    apply_theme(app, settings.theme)
    palette = build_palette(settings.theme)
    set_terminal_colors(palette["content_background"], palette["text_primary"])
```

- [ ] **Step 6: Живая проверка**

Run: `.venv\Scripts\python.exe -m ven4control.app`
Ожидается: окно открывается перекрашенным в тему «Бирюзовая» (тёмный
сине-чёрный фон, голубой акцент кнопок с `objectName="accent"` — таких
пока ни у одной кнопки не выставлено объектным именем, так что видимого
акцента на кнопках ещё не будет, это ожидаемо для этой фазы: фаза 2
расставит `objectName`/`nav`-свойства по конкретным кнопкам). Открыть
встроенный терминал к любому устройству — фон/текст терминала должны быть
тёмными (не то, что было раньше — те же `#101014`/`#d8d8d8`, если тема
`teal`, но подтверждает, что путь работает: попробовать переключить на
Task 3 диалог настроек, выбрать «Светлая», открыть НОВЫЙ терминал — его
фон должен стать светлым). Закрыть приложение (`Alt+F4`/закрыть окно).

- [ ] **Step 7: Прогнать весь набор**

Run: `.venv\Scripts\python.exe -m pytest -q`
Expected: `351 passed` (349 + 2 новых из Step 2).

- [ ] **Step 8: Commit**

```bash
git add src/ven4control/app.py src/ven4control/ansi_screen.py tests/test_ansi_screen.py
git commit -m "Применена тема при старте приложения и в новых терминалах"
```

---

## Self-Review (выполнено при написании плана)

1. **Покрытие спеки**: раздел «Архитектура: тема» → Task 1; «Архитектура: хранение выбора» → Task 2; «Диалог «Настройки»» → Task 3; интеграция при старте + терминал → Task 4. Разделы «Главное окно», «DeviceControlDialog», «Иконки» спеки — сознательно вне этого плана, это отдельные фазы 2/3 (план пишется заново после мержа этой фазы, когда решится реальное имя QSS-свойств для nav-кнопок на практике).
2. **Плейсхолдеров нет** — весь код в шагах законченный, ни одного TODO/TBD.
3. **Согласованность типов**: `apply_theme(app, theme)` — сигнатура одна и та же в Task 1 (определение) и Task 4 (вызов). `build_palette(theme) -> dict[str, str]` — ключи, которые Task 4 читает (`content_background`, `text_primary`), присутствуют в словаре, собираемом в Task 1. `AppSettings.theme`/`load_settings`/`save_settings` — сигнатуры одни и те же в Task 2 (определение) и Task 3/4 (использование).
4. **Живая проверка** включена явно (Task 4, Step 6) — не только юниты, ровно то, чему учит сегодняшний опыт с dropbear/SFTP: мок не ловит то, что ловит реальный запуск.
5. **Найдена и исправлена реальная ловушка при написании плана** (не гипотетическая): `terminal_dialog.py` и черновик собственного теста этого плана импортировали бы `DEFAULT_BACKGROUND`/`DEFAULT_FOREGROUND` через `from ... import`, что копирует значение на момент импорта модуля — `set_default_colors()` такую копию не обновляет (Python-семантика `from x import y`, не привязка на модуль). Проверено чтением реального `terminal_dialog.py:21-26` перед тем, как писать план, а не предположено. Task 4 Step 2 и Step 4 переписаны на чтение через атрибут модуля (`ansi_screen.DEFAULT_BACKGROUND`), это единственный способ увидеть переопределённое значение.
