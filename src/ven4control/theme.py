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

QPushButton#sectionToggle {{
    background-color: transparent;
    border: none;
    color: {c['text_secondary']};
    font-size: 9pt;
    font-weight: 600;
    text-align: left;
    padding: 4px 0;
}}

QPushButton#sectionToggle:hover {{
    color: {c['text_primary']};
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

QWidget#sidebar {{
    background-color: {c['sidebar_background']};
    border-right: 1px solid {c['border_color']};
}}

QWidget#contentHeader {{
    background-color: {c['sidebar_background']};
    border-bottom: 1px solid {c['border_color']};
}}

QFrame#brandStrip {{
    background-color: {c['accent_color']};
    border-radius: 1px;
}}
""".strip()


def apply_theme(app, theme: str) -> None:
    """Применяет тему ко всему приложению.

    Принимает любой объект с методом setStyleSheet(str), не обязательно
    настоящий QApplication — так функция тестируется без реального Qt-окна,
    как и весь остальной набор тестов этого проекта.
    """
    app.setStyleSheet(stylesheet_for(theme))
