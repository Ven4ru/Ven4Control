"""Проверка ключа сервера до отправки учётных данных.

Тесты поднимают настоящий SSH-сервер на 127.0.0.1 и записывают все попытки
аутентификации. Только так видно главное свойство: при чужом ключе сервера
пароль и приватный ключ до сервера не доходят вовсе, а не «соединение
закрылось после входа».
"""
from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

import asyncssh

from ven4control.models import Device
from ven4control.remote_control import FingerprintError, _connect
from ven4control.ssh_service import install_public_key, probe_device


WRONG_FINGERPRINT = "SHA256:" + "A" * 43


class RecordingServer(asyncssh.SSHServer):
    """Сервер, который запоминает каждую попытку аутентификации."""

    def __init__(
        self,
        attempts: list[tuple[str, str]],
        accept: bool,
        public_key_auth: bool,
    ) -> None:
        self._attempts = attempts
        self._accept = accept
        self._public_key_auth = public_key_auth

    def begin_auth(self, username: str) -> bool:
        # True — «пустая» аутентификация не принимается, клиент обязан
        # предъявить пароль или ключ.
        return True

    def password_auth_supported(self) -> bool:
        return True

    def validate_password(self, username: str, password: str) -> bool:
        self._attempts.append(("password", password))
        return self._accept

    def public_key_auth_supported(self) -> bool:
        return self._public_key_auth

    def validate_public_key(self, username: str, key: asyncssh.SSHKey) -> bool:
        self._attempts.append(("publickey", key.get_fingerprint("sha256")))
        return self._accept


class LoopbackServer:
    """Асинхронный контекст: сервер на свободном порту 127.0.0.1."""

    def __init__(self, accept: bool = False, public_key_auth: bool = True) -> None:
        self.accept = accept
        self.public_key_auth = public_key_auth
        self.attempts: list[tuple[str, str]] = []
        self.commands: list[str] = []
        self.fingerprint = ""
        self.port = 0
        self._server: asyncssh.SSHAcceptor | None = None

    async def _handle(self, process: asyncssh.SSHServerProcess) -> None:
        command = str(process.command or "")
        self.commands.append(command)
        if "openwrt_release" in command:
            process.stdout.write("openwrt\n")
        process.exit(0)

    async def __aenter__(self) -> "LoopbackServer":
        host_key = asyncssh.generate_private_key("ssh-ed25519")
        self.fingerprint = host_key.get_fingerprint("sha256")
        self._server = await asyncssh.create_server(
            lambda: RecordingServer(
                self.attempts, self.accept, self.public_key_auth
            ),
            "127.0.0.1",
            0,
            server_host_keys=[host_key],
            process_factory=self._handle,
        )
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        assert self._server is not None
        self._server.close()
        await self._server.wait_closed()

    def device(self, fingerprint: str = "", **extra: object) -> Device:
        return Device(
            id=1,
            name="устройство",
            host="127.0.0.1",
            port=self.port,
            username="user",
            fingerprint=fingerprint,
            **extra,
        )


class ConnectPinningTests(unittest.TestCase):
    def test_password_never_leaves_when_fingerprint_differs(self) -> None:
        async def scenario() -> list[tuple[str, str]]:
            async with LoopbackServer() as server:
                device = server.device(fingerprint=WRONG_FINGERPRINT)
                with self.assertRaises(FingerprintError) as raised:
                    await _connect(device, {"password": "секрет"})
                self.assertIn("fingerprint устройства изменился", str(raised.exception))
                return server.attempts

        self.assertEqual([], asyncio.run(scenario()))

    def test_private_key_is_never_offered_when_fingerprint_differs(self) -> None:
        async def scenario() -> list[tuple[str, str]]:
            with tempfile.TemporaryDirectory() as directory:
                key_path = Path(directory) / "id_ed25519"
                key = asyncssh.generate_private_key("ssh-ed25519")
                key_path.write_bytes(key.export_private_key("openssh"))
                async with LoopbackServer() as server:
                    device = server.device(
                        fingerprint=WRONG_FINGERPRINT,
                        auth_type="key",
                        key_path=str(key_path),
                    )
                    with self.assertRaises(FingerprintError):
                        await _connect(device, {})
                    return server.attempts

        self.assertEqual([], asyncio.run(scenario()))

    def test_saved_fingerprint_still_lets_the_password_through(self) -> None:
        async def scenario() -> list[tuple[str, str]]:
            # Сервер не предлагает вход по ключу: иначе asyncssh успел бы
            # войти ключом разработчика из ~/.ssh, и проверка зависела бы от
            # содержимого домашнего каталога.
            async with LoopbackServer(accept=True, public_key_auth=False) as server:
                device = server.device(fingerprint=server.fingerprint)
                connection = await _connect(device, {"password": "секрет"})
                connection.close()
                await connection.wait_closed()
                return server.attempts

        self.assertEqual([("password", "секрет")], asyncio.run(scenario()))

    def test_device_without_fingerprint_is_not_connected(self) -> None:
        async def scenario() -> list[tuple[str, str]]:
            async with LoopbackServer(accept=True) as server:
                device = server.device()
                with self.assertRaises(FingerprintError):
                    await _connect(device, {"password": "секрет"})
                return server.attempts

        self.assertEqual([], asyncio.run(scenario()))


class ProbeDeviceTests(unittest.TestCase):
    def test_fingerprint_is_read_without_any_credentials(self) -> None:
        async def scenario() -> tuple[str, str, list[tuple[str, str]]]:
            async with LoopbackServer(accept=True) as server:
                fingerprint = await probe_device(server.device())
                return fingerprint, server.fingerprint, server.attempts

        fingerprint, expected, attempts = asyncio.run(scenario())
        self.assertEqual(expected, fingerprint)
        self.assertEqual([], attempts)


class InstallPublicKeyTests(unittest.TestCase):
    def test_password_never_leaves_when_fingerprint_differs(self) -> None:
        async def scenario() -> list[tuple[str, str]]:
            with tempfile.TemporaryDirectory() as directory:
                public_path = Path(directory) / "id_ed25519.pub"
                key = asyncssh.generate_private_key("ssh-ed25519")
                public_path.write_bytes(key.export_public_key("openssh"))
                async with LoopbackServer(accept=True) as server:
                    with self.assertRaises(RuntimeError):
                        await install_public_key(
                            server.device(),
                            "секрет",
                            public_path,
                            WRONG_FINGERPRINT,
                        )
                    return server.attempts

        self.assertEqual([], asyncio.run(scenario()))

    def test_confirmed_fingerprint_lets_the_installation_through(self) -> None:
        async def scenario() -> tuple[str, list[str]]:
            with tempfile.TemporaryDirectory() as directory:
                public_path = Path(directory) / "id_ed25519.pub"
                key = asyncssh.generate_private_key("ssh-ed25519")
                public_path.write_bytes(key.export_public_key("openssh"))
                async with LoopbackServer(
                    accept=True, public_key_auth=False
                ) as server:
                    system = await install_public_key(
                        server.device(),
                        "секрет",
                        public_path,
                        server.fingerprint,
                    )
                    return system, server.commands

        system, commands = asyncio.run(scenario())
        self.assertEqual("openwrt", system)
        self.assertTrue(
            any("/etc/dropbear/authorized_keys" in command for command in commands),
            commands,
        )


if __name__ == "__main__":
    unittest.main()
