"""Проверка и применение обновлений Ven4Control.

Разбор ответа GitHub отделён от самого запроса: всё, что принимает решения,
работает с обычными словарями и строками и тестируется без сети.

Обновляется только копия, установленная через NSIS. Портативный onefile-EXE
ничего не прописывает в систему, поэтому за него решения не принимаем — ему
показывается уведомление и ссылка на страницу релиза.
"""
from __future__ import annotations

import re

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
