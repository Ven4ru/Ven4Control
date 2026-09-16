"""Проверка и применение обновлений Ven4Control.

Разбор ответа GitHub отделён от самого запроса: всё, что принимает решения,
работает с обычными словарями и строками и тестируется без сети.

Обновляется только копия, установленная через NSIS. Портативный onefile-EXE
ничего не прописывает в систему, поэтому за него решения не принимаем — ему
показывается уведомление и ссылка на страницу релиза.
"""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta

from ven4control.settings import UPDATE_CHECK_ENABLED, AppSettings

# Теги релизов исторически разной формы: v0.4-beta, v0.4.1-beta, v0.5.0-beta.
# Недостающие части считаем нулями, иначе 0.4 и 0.4.0 сравнивались бы как
# разные версии.
TAG_PATTERN = re.compile(r"^v?(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:-.+)?$")


def parse_release_tag(tag: str) -> tuple[int, int, int] | None:
    """Разбирает тег релиза в кортеж чисел. Неразбираемый тег — None."""
    match = TAG_PATTERN.match(tag.strip())
    if not match:
        return None
    major, minor, patch = match.groups()
    return int(major), int(minor or 0), int(patch or 0)


def is_newer(current: str, candidate: str) -> bool:
    """Строго ли candidate новее current.

    Любая неразобранная сторона означает «не новее»: предлагать обновление на
    основании тега, который мы не поняли, нельзя.
    """
    left = parse_release_tag(current)
    right = parse_release_tag(candidate)
    if left is None or right is None:
        return False
    return right > left


# Имя ассета опознаётся по явному шаблону, а не «первый подходящий»: в релизе
# рядом лежит портативный Ven4Control.exe, а когда-нибудь появятся и другие
# файлы. Выбор наугад — ровно тот дефект, который уже находили в установке
# Ven4Tools на устройство.
INSTALLER_PATTERN = re.compile(r"^Ven4Control\.Setup-\d+\.\d+\.\d+\.exe$")

SHA256_PREFIX = "sha256:"
SHA256_LENGTH = 64


@dataclass(frozen=True, slots=True)
class UpdateInfo:
    version: str
    tag: str
    asset_name: str
    download_url: str
    sha256: str
    page_url: str


def select_update(release: dict, current_version: str) -> UpdateInfo | None:
    """Выбирает обновление из ответа GitHub или отказывает.

    Отказ (None) во всех сомнительных случаях: версия не новее, подходящего
    ассета нет, их больше одного, нет контрольной суммы или она в незнакомом
    формате. Ставить то, что нечем проверить, нельзя.
    """
    tag = release.get("tag_name")
    if not isinstance(tag, str) or not is_newer(current_version, tag):
        return None

    assets = release.get("assets")
    if not isinstance(assets, list):
        return None
    matching = [
        asset
        for asset in assets
        if isinstance(asset, dict)
        and isinstance(asset.get("name"), str)
        and INSTALLER_PATTERN.match(asset["name"])
    ]
    if len(matching) != 1:
        return None

    asset = matching[0]
    digest = asset.get("digest")
    if not isinstance(digest, str) or not digest.startswith(SHA256_PREFIX):
        return None
    sha256 = digest[len(SHA256_PREFIX):]
    if len(sha256) != SHA256_LENGTH:
        return None

    url = asset.get("browser_download_url")
    if not isinstance(url, str) or not url.startswith("https://"):
        return None

    parsed = parse_release_tag(tag)
    assert parsed is not None  # is_newer выше уже разобрал тег
    page_url = release.get("html_url")
    return UpdateInfo(
        version=".".join(str(part) for part in parsed),
        tag=tag,
        asset_name=asset["name"],
        download_url=url,
        sha256=sha256,
        page_url=page_url if isinstance(page_url, str) else "",
    )


REGISTRY_KEY = r"Software\Ven4Control"
REGISTRY_VALUE = "InstallDir"


def installed_dir() -> str | None:
    """Каталог установки из реестра или None, если приложение не устанавливали.

    Значение пишет установщик NSIS. Его отсутствие означает портативную копию.
    """
    try:
        import winreg
    except ImportError:  # не Windows — портативный запуск из исходников
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REGISTRY_KEY) as key:
            value, _ = winreg.QueryValueEx(key, REGISTRY_VALUE)
    except OSError:
        return None
    return value if isinstance(value, str) and value else None


def _same_dir(left: str, right: str) -> bool:
    try:
        return os.path.normcase(os.path.abspath(left)) == os.path.normcase(
            os.path.abspath(right)
        )
    except OSError:
        return False


def is_installed_copy(
    executable: str | None = None,
    install_dir: str | None = None,
) -> bool:
    """Запущена ли установленная копия, а не портативная.

    Сравниваются каталог запущенного файла и каталог из реестра, с
    нормализацией: Windows не различает регистр, а разделители и хвостовой
    слэш могут отличаться (установщик пишет App, пользователь мог создать app).
    """
    exe = executable if executable is not None else sys.executable
    target = install_dir if install_dir is not None else installed_dir()
    if not target:
        return False
    return _same_dir(os.path.dirname(exe), target)


CHECK_INTERVAL_HOURS = 24


def should_check(settings: AppSettings, now: datetime) -> bool:
    """Пора ли автоматически проверять обновления.

    Отметка времени двигается только при успешном ответе (см. вызывающий код),
    поэтому машина, простоявшая сутки без сети, попробует снова при следующем
    запуске, а не сочтёт проверку состоявшейся. Испорченная отметка тоже не
    должна блокировать проверку навсегда.
    """
    if settings.update_check != UPDATE_CHECK_ENABLED:
        return False
    if not settings.update_last_check:
        return True
    try:
        last = datetime.fromisoformat(settings.update_last_check)
    except ValueError:
        return True
    return now - last >= timedelta(hours=CHECK_INTERVAL_HOURS)
