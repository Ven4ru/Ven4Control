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
