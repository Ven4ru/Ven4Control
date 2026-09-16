import unittest

from ven4control.updates import UpdateInfo, is_newer, parse_release_tag, select_update


def release(tag: str = "v0.6.0-beta", assets: list[dict] | None = None) -> dict:
    if assets is None:
        assets = [
            {
                "name": "Ven4Control.Setup-0.6.0.exe",
                "browser_download_url": "https://example.invalid/Setup.exe",
                "digest": "sha256:" + "a" * 64,
            }
        ]
    return {
        "tag_name": tag,
        "html_url": "https://example.invalid/releases/tag/" + tag,
        "assets": assets,
    }


class ParseReleaseTagTests(unittest.TestCase):
    def test_full_tag_with_suffix(self) -> None:
        self.assertEqual((0, 5, 0), parse_release_tag("v0.5.0-beta"))

    def test_short_tag_is_padded(self) -> None:
        """В истории релизов есть и v0.4-beta, и v0.4.1-beta."""
        self.assertEqual((0, 4, 0), parse_release_tag("v0.4-beta"))

    def test_tag_without_prefix_and_suffix(self) -> None:
        self.assertEqual((1, 2, 3), parse_release_tag("1.2.3"))

    def test_unparseable_tag_is_none(self) -> None:
        for tag in ("", "latest", "release-осень", "v.1.2"):
            with self.subTest(tag=tag):
                self.assertIsNone(parse_release_tag(tag))


class IsNewerTests(unittest.TestCase):
    def test_patch_is_newer_than_short_tag(self) -> None:
        self.assertTrue(is_newer("0.4", "v0.4.1-beta"))

    def test_ten_is_newer_than_nine(self) -> None:
        """Сравнение обязано быть числовым, а не строковым."""
        self.assertTrue(is_newer("0.9.0", "v0.10.0-beta"))

    def test_same_version_is_not_newer(self) -> None:
        self.assertFalse(is_newer("0.5.0", "v0.5.0-beta"))

    def test_older_release_is_not_newer(self) -> None:
        self.assertFalse(is_newer("0.5.0", "v0.4.1-beta"))

    def test_unparseable_never_counts_as_newer(self) -> None:
        """Не разобрали — не предлагаем обновление."""
        self.assertFalse(is_newer("0.5.0", "невнятный-тег"))
        self.assertFalse(is_newer("невнятная", "v9.9.9"))


class SelectUpdateTests(unittest.TestCase):
    def test_picks_the_installer_asset(self) -> None:
        found = select_update(release(), "0.5.0")
        self.assertIsInstance(found, UpdateInfo)
        self.assertEqual("0.6.0", found.version)
        self.assertEqual("Ven4Control.Setup-0.6.0.exe", found.asset_name)
        self.assertEqual("a" * 64, found.sha256)

    def test_ignores_release_that_is_not_newer(self) -> None:
        self.assertIsNone(select_update(release("v0.5.0-beta"), "0.5.0"))

    def test_portable_exe_is_not_treated_as_an_installer(self) -> None:
        """Рядом с установщиком в релизе лежит портативный EXE."""
        assets = [
            {
                "name": "Ven4Control.exe",
                "browser_download_url": "https://example.invalid/portable.exe",
                "digest": "sha256:" + "b" * 64,
            }
        ]
        self.assertIsNone(select_update(release(assets=assets), "0.5.0"))

    def test_ambiguous_assets_are_refused(self) -> None:
        """Два подходящих ассета — берём не «первый попавшийся», а отказываем."""
        assets = [
            {
                "name": "Ven4Control.Setup-0.6.0.exe",
                "browser_download_url": "https://example.invalid/a.exe",
                "digest": "sha256:" + "a" * 64,
            },
            {
                "name": "Ven4Control.Setup-0.6.1.exe",
                "browser_download_url": "https://example.invalid/b.exe",
                "digest": "sha256:" + "c" * 64,
            },
        ]
        self.assertIsNone(select_update(release(assets=assets), "0.5.0"))

    def test_asset_without_digest_is_refused(self) -> None:
        """Без контрольной суммы обновление не предлагается — проверять нечем."""
        assets = [
            {
                "name": "Ven4Control.Setup-0.6.0.exe",
                "browser_download_url": "https://example.invalid/a.exe",
            }
        ]
        self.assertIsNone(select_update(release(assets=assets), "0.5.0"))

    def test_digest_in_unexpected_format_is_refused(self) -> None:
        assets = [
            {
                "name": "Ven4Control.Setup-0.6.0.exe",
                "browser_download_url": "https://example.invalid/a.exe",
                "digest": "md5:" + "a" * 32,
            }
        ]
        self.assertIsNone(select_update(release(assets=assets), "0.5.0"))

    def test_empty_release_is_refused(self) -> None:
        self.assertIsNone(select_update({}, "0.5.0"))


if __name__ == "__main__":
    unittest.main()
