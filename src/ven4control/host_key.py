"""Сверка ключа сервера до отправки пароля и ключа.

Раньше соединение открывалось с паролем, а fingerprint сравнивался уже с
готовым соединением: пароль пользователя успевал уйти любому, кто ответил на
TCP-соединение. Здесь ключ сервера проверяется во время обмена ключами —
asyncssh обрывает соединение до фазы аутентификации, и учётные данные никуда
не отправляются.

`known_hosts` намеренно не `None`: при `None` asyncssh считает host key
непроверяемым и `validate_host_public_key` не вызывает вовсе. Пустые списки
доверенных ключей оставляют единственным источником доверия наш коллбек — и
при этом не подмешивают `~/.ssh/known_hosts`, где доверенным может оказаться
совсем не тот ключ, который сохранён у устройства.
"""
from __future__ import annotations

import asyncssh


EMPTY_KNOWN_HOSTS: tuple[list[str], list[str], list[str]] = ([], [], [])

# Первый проход к незнакомому устройству: ни пароля, ни ключей, ни агента —
# только транспорт. Нужен единственный ответ сервера: его host key.
NO_CREDENTIALS: dict[str, object] = {
    "password": None,
    "client_keys": None,
    "agent_path": None,
    "password_auth": False,
    "public_key_auth": False,
    "kbdint_auth": False,
    "host_based_auth": False,
    "gss_auth": False,
    "gss_kex": False,
}


class HostKeyPin(asyncssh.SSHClient):
    """Сверяет ключ сервера во время обмена ключами.

    Без ожидаемого отпечатка ключ принимается любой, а сам отпечаток
    запоминается: это первичное знакомство с устройством (TOFU), где
    подтверждает отпечаток пользователь, а не приложение.
    """

    def __init__(self, expected_fingerprint: str | None = None) -> None:
        self.expected_fingerprint = expected_fingerprint
        self.fingerprint: str | None = None

    def validate_host_public_key(
        self,
        host: str,
        addr: str,
        port: int,
        key: asyncssh.SSHKey,
    ) -> bool:
        self.fingerprint = key.get_fingerprint("sha256")
        if self.expected_fingerprint is None:
            return True
        return self.fingerprint == self.expected_fingerprint


def pinned_options(pin: HostKeyPin) -> dict[str, object]:
    """Параметры `asyncssh.connect`, включающие проверку ключа сервера."""
    return {"known_hosts": EMPTY_KNOWN_HOSTS, "client_factory": lambda: pin}
