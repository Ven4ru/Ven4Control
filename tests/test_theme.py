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

    def test_stylesheet_styles_the_sidebar_container(self) -> None:
        css = stylesheet_for(DEFAULT_THEME)
        self.assertIn("#sidebar", css)
        self.assertIn("#contentHeader", css)
        self.assertIn("#brandStrip", css)

    def test_stylesheet_styles_the_section_toggle(self) -> None:
        css = stylesheet_for(DEFAULT_THEME)
        self.assertIn("#sectionToggle", css)
        self.assertNotIn("eyebrow", css)


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
