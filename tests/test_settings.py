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


class UpdateConsentTests(unittest.TestCase):
    def test_new_installation_has_not_been_asked_yet(self) -> None:
        from ven4control.settings import UPDATE_CHECK_UNKNOWN

        self.assertEqual(UPDATE_CHECK_UNKNOWN, AppSettings().update_check)

    def test_settings_saved_before_this_feature_are_read_as_not_asked(self) -> None:
        """У тех, кто обновляется с прежних версий, ключа в файле нет."""
        from ven4control.settings import UPDATE_CHECK_UNKNOWN

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text('{"theme": "dark"}', encoding="utf-8")
            loaded = load_settings(path)
            self.assertEqual(UPDATE_CHECK_UNKNOWN, loaded.update_check)
            self.assertEqual("", loaded.update_last_check)

    def test_consent_and_timestamp_survive_a_round_trip(self) -> None:
        from ven4control.settings import UPDATE_CHECK_ENABLED

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            save_settings(
                AppSettings(
                    update_check=UPDATE_CHECK_ENABLED,
                    update_last_check="2026-09-16T10:00:00",
                ),
                path,
            )
            loaded = load_settings(path)
            self.assertEqual(UPDATE_CHECK_ENABLED, loaded.update_check)
            self.assertEqual("2026-09-16T10:00:00", loaded.update_last_check)

    def test_unknown_value_in_file_falls_back_to_not_asked(self) -> None:
        from ven4control.settings import UPDATE_CHECK_UNKNOWN

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text('{"update_check": "может быть"}', encoding="utf-8")
            self.assertEqual(UPDATE_CHECK_UNKNOWN, load_settings(path).update_check)


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
