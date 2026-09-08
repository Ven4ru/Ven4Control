import json
import tempfile
import unittest
from pathlib import Path

from ven4control.settings import (
    TERMINAL_THEME_SYNC,
    AppSettings,
    load_settings,
    save_settings,
    terminal_palette,
)
from ven4control.theme import DEFAULT_THEME, THEME_LIGHT, build_palette


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

    def test_terminal_theme_defaults_to_sync(self) -> None:
        self.assertEqual(TERMINAL_THEME_SYNC, AppSettings().terminal_theme)

    def test_terminal_theme_round_trips(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            save_settings(AppSettings(theme="dark", terminal_theme="web"), path)
            self.assertEqual("web", load_settings(path).terminal_theme)

    def test_file_from_before_terminal_theme_existed_defaults_to_sync(self) -> None:
        """Файл фазы 1 хранит только theme — старые настройки не должны падать."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(json.dumps({"theme": "light"}), encoding="utf-8")
            settings = load_settings(path)
            self.assertEqual("light", settings.theme)
            self.assertEqual(TERMINAL_THEME_SYNC, settings.terminal_theme)


class TerminalPaletteTests(unittest.TestCase):
    def test_synced_terminal_follows_app_theme(self) -> None:
        settings = AppSettings(theme=THEME_LIGHT, terminal_theme=TERMINAL_THEME_SYNC)
        self.assertEqual(build_palette(THEME_LIGHT), terminal_palette(settings))

    def test_overridden_terminal_ignores_app_theme(self) -> None:
        settings = AppSettings(theme=THEME_LIGHT, terminal_theme="dark")
        self.assertEqual(build_palette("dark"), terminal_palette(settings))


if __name__ == "__main__":
    unittest.main()
