import hashlib
import pathlib
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest import mock

from ven4control.updates import (
    UpdateInfo,
    is_installed_copy,
    is_newer,
    parse_release_tag,
    select_update,
    should_check,
)


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


class InstallerNamingTests(unittest.TestCase):
    """Имя ассета релиза обязано совпадать с тем, что ищет приложение.

    Теги релизов идут с суффиксом (`v0.5.0-beta`), и собрать установщик как
    `Ven4Control.Setup-0.5.0-beta.exe` — естественная ошибка. Такое имя шаблон
    не примет, и самообновление молча не сработает никогда: пользователь будет
    видеть «Обновлений не найдено» без единого намёка на причину.
    """

    def test_name_built_from_the_project_version_matches(self) -> None:
        from ven4control.updates import INSTALLER_PATTERN
        from ven4control._version import __version__

        self.assertRegex(__version__, r"^\d+\.\d+\.\d+$")
        self.assertIsNotNone(
            INSTALLER_PATTERN.match(f"Ven4Control.Setup-{__version__}.exe")
        )

    def test_name_with_a_release_suffix_is_rejected(self) -> None:
        from ven4control.updates import INSTALLER_PATTERN

        self.assertIsNone(
            INSTALLER_PATTERN.match("Ven4Control.Setup-0.6.0-beta.exe")
        )

    def test_generated_installer_version_matches_the_constant(self) -> None:
        """installer/version.nsh генерируется из той же константы.

        Расхождение означало бы установщик, который ставит код одной версии
        под именем другой, — и приложение предлагало бы то же обновление
        бесконечно.
        """
        import pathlib
        import re as regex

        from ven4control._version import __version__

        nsh = (
            pathlib.Path(__file__).resolve().parent.parent
            / "installer"
            / "version.nsh"
        )
        match = regex.search(r'!define VERSION "([^"]+)"', nsh.read_text(encoding="utf-8"))
        self.assertIsNotNone(match, "version.nsh не сгенерирован")
        self.assertEqual(__version__, match.group(1))


class InstallationKindTests(unittest.TestCase):
    def test_exe_inside_registered_directory_is_installed(self) -> None:
        self.assertTrue(
            is_installed_copy(
                executable=r"C:\Users\Ann\AppData\Local\Ven4Control\App\Ven4Control.exe",
                install_dir=r"C:\Users\Ann\AppData\Local\Ven4Control\App",
            )
        )

    def test_case_and_separator_differences_do_not_matter(self) -> None:
        """Установщик пишет App, пользователь мог создать app — Windows не различает."""
        self.assertTrue(
            is_installed_copy(
                executable=r"C:\Users\Ann\AppData\Local\Ven4Control\app\Ven4Control.exe",
                install_dir="C:/Users/Ann/AppData/Local/Ven4Control/App/",
            )
        )

    def test_exe_elsewhere_is_portable(self) -> None:
        self.assertFalse(
            is_installed_copy(
                executable=r"D:\Downloads\Ven4Control.exe",
                install_dir=r"C:\Users\Ann\AppData\Local\Ven4Control\App",
            )
        )

    def test_without_registry_entry_it_is_portable(self) -> None:
        """Записи в реестре нет — значит копию не устанавливали.

        Путь из реестра подменяется явно: иначе тест читал бы реестр машины,
        на которой запущен, и его результат зависел бы от того, установлен ли
        там Ven4Control.
        """
        from ven4control import updates

        with mock.patch.object(updates, "installed_dir", return_value=None):
            self.assertFalse(
                updates.is_installed_copy(
                    executable=(
                        r"C:\Users\Ann\AppData\Local\Ven4Control\App\Ven4Control.exe"
                    )
                )
            )


class ShouldCheckTests(unittest.TestCase):
    def _settings(self, consent: str, last: str = ""):
        from ven4control.settings import AppSettings

        return AppSettings(update_check=consent, update_last_check=last)

    def test_no_check_until_the_question_is_answered(self) -> None:
        from ven4control.settings import UPDATE_CHECK_UNKNOWN

        self.assertFalse(
            should_check(self._settings(UPDATE_CHECK_UNKNOWN), datetime(2026, 9, 16))
        )

    def test_no_check_when_refused(self) -> None:
        from ven4control.settings import UPDATE_CHECK_DISABLED

        self.assertFalse(
            should_check(self._settings(UPDATE_CHECK_DISABLED), datetime(2026, 9, 16))
        )

    def test_first_check_after_consent(self) -> None:
        from ven4control.settings import UPDATE_CHECK_ENABLED

        self.assertTrue(
            should_check(self._settings(UPDATE_CHECK_ENABLED), datetime(2026, 9, 16))
        )

    def test_not_more_often_than_once_a_day(self) -> None:
        from ven4control.settings import UPDATE_CHECK_ENABLED

        now = datetime(2026, 9, 16, 12, 0, 0)
        recent = (now - timedelta(hours=3)).isoformat()
        self.assertFalse(
            should_check(self._settings(UPDATE_CHECK_ENABLED, recent), now)
        )

    def test_checks_again_after_a_day(self) -> None:
        from ven4control.settings import UPDATE_CHECK_ENABLED

        now = datetime(2026, 9, 16, 12, 0, 0)
        old = (now - timedelta(hours=25)).isoformat()
        self.assertTrue(should_check(self._settings(UPDATE_CHECK_ENABLED, old), now))

    def test_timestamp_from_the_future_does_not_block_checking(self) -> None:
        """Часы могли уйти вперёд или их перевели назад.

        Без этого разница получалась отрицательной, и проверка блокировалась,
        пока реальное время не догонит записанное, — это могут быть месяцы.
        """
        from ven4control.settings import UPDATE_CHECK_ENABLED

        now = datetime(2026, 9, 16, 12, 0, 0)
        future = (now + timedelta(days=40)).isoformat()
        self.assertTrue(should_check(self._settings(UPDATE_CHECK_ENABLED, future), now))

    def test_corrupt_timestamp_does_not_block_checking(self) -> None:
        from ven4control.settings import UPDATE_CHECK_ENABLED

        self.assertTrue(
            should_check(
                self._settings(UPDATE_CHECK_ENABLED, "позавчера"), datetime(2026, 9, 16)
            )
        )


class VerifyHashTests(unittest.TestCase):
    def _file(self, directory: str, payload: bytes = b"content") -> pathlib.Path:
        target = pathlib.Path(directory) / "file.bin"
        target.write_bytes(payload)
        return target

    def test_matching_hash_passes(self) -> None:
        from ven4control.updates import verify_sha256

        with tempfile.TemporaryDirectory() as directory:
            target = self._file(directory)
            self.assertTrue(
                verify_sha256(target, hashlib.sha256(b"content").hexdigest())
            )

    def test_different_hash_fails(self) -> None:
        from ven4control.updates import verify_sha256

        with tempfile.TemporaryDirectory() as directory:
            self.assertFalse(verify_sha256(self._file(directory), "0" * 64))

    def test_comparison_ignores_case(self) -> None:
        from ven4control.updates import verify_sha256

        with tempfile.TemporaryDirectory() as directory:
            target = self._file(directory)
            self.assertTrue(
                verify_sha256(target, hashlib.sha256(b"content").hexdigest().upper())
            )


class CheckForUpdateTests(unittest.TestCase):
    def test_returns_update_when_release_is_newer(self) -> None:
        from ven4control import updates

        with mock.patch.object(updates, "fetch_latest_release", return_value=release()):
            found = updates.check_for_update("0.5.0")
        self.assertIsNotNone(found)
        self.assertEqual("0.6.0", found.version)

    def test_returns_none_when_up_to_date(self) -> None:
        from ven4control import updates

        payload = release("v0.5.0-beta")
        with mock.patch.object(updates, "fetch_latest_release", return_value=payload):
            self.assertIsNone(updates.check_for_update("0.5.0"))

    def test_rate_limit_is_reported_separately(self) -> None:
        """Иначе человек решит, что у него сломался интернет."""
        from ven4control import updates

        with mock.patch.object(
            updates, "fetch_latest_release", side_effect=updates.RateLimitedError()
        ):
            with self.assertRaises(updates.RateLimitedError):
                updates.check_for_update("0.5.0")


if __name__ == "__main__":
    unittest.main()
