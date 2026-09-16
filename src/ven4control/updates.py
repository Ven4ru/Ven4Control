"""Проверка и применение обновлений Ven4Control.

Разбор ответа GitHub отделён от самого запроса: всё, что принимает решения,
работает с обычными словарями и строками и тестируется без сети.

Обновляется только копия, установленная через NSIS. Портативный onefile-EXE
ничего не прописывает в систему, поэтому за него решения не принимаем — ему
показывается уведомление и ссылка на страницу релиза.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

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
