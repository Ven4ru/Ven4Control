import unittest

from ven4control.updates import is_newer, parse_release_tag


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


if __name__ == "__main__":
    unittest.main()
