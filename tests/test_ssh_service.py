import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import asyncssh

from ven4control.ssh_service import ensure_app_key, secure_private_key_permissions
from ven4control.windows_identity import current_user_sid, system32_path


class ApplicationKeyTests(unittest.TestCase):
    def test_existing_private_key_is_never_replaced(self) -> None:
        """Ключ уже прописан в authorized_keys устройств — его нельзя менять."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "id_ed25519"
            private, public = ensure_app_key(path)
            original_private = private.read_bytes()
            original_public = public.read_bytes()

            for _ in range(3):
                ensure_app_key(path)

            self.assertEqual(original_private, private.read_bytes())
            self.assertEqual(original_public, public.read_bytes())

    def test_lost_public_key_is_restored_from_private_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "id_ed25519"
            private, public = ensure_app_key(path)
            original_private = private.read_bytes()
            expected_public = public.read_bytes()
            public.unlink()

            restored_private, restored_public = ensure_app_key(path)

            self.assertEqual(original_private, restored_private.read_bytes())
            self.assertTrue(restored_public.exists())
            self.assertEqual(
                asyncssh.import_public_key(expected_public).get_fingerprint("sha256"),
                asyncssh.import_public_key(
                    restored_public.read_bytes()
                ).get_fingerprint("sha256"),
            )

    def test_broken_private_key_does_not_stop_application(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "id_ed25519"
            damaged = b"not-a-private-key"
            path.write_bytes(damaged)

            private, public = ensure_app_key(path)

            self.assertEqual(damaged, private.read_bytes())
            self.assertFalse(public.exists())

    def test_new_key_does_not_stay_unprotected(self) -> None:
        """Ключ пишется на диск до ACL — при отказе его нельзя оставлять."""
        with mock.patch(
            "ven4control.ssh_service.secure_private_key_permissions",
            side_effect=RuntimeError("Не удалось защитить SSH-ключ"),
        ):
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "id_ed25519"
                with self.assertRaises(RuntimeError):
                    ensure_app_key(path)
                self.assertFalse(path.exists())
                self.assertFalse(path.with_suffix(".pub").exists())

    def test_existing_key_survives_a_failed_acl(self) -> None:
        """Существующий ключ уже прописан в authorized_keys — не удалять."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "id_ed25519"
            private, public = ensure_app_key(path)
            expected = private.read_bytes()

            with mock.patch(
                "ven4control.ssh_service.secure_private_key_permissions",
                side_effect=RuntimeError("Не удалось защитить SSH-ключ"),
            ), self.assertRaises(RuntimeError):
                ensure_app_key(path)

            self.assertEqual(expected, private.read_bytes())
            self.assertTrue(public.exists())


@unittest.skipUnless(os.name == "nt", "Проверка ACL предназначена для Windows")
class PrivateKeyPermissionsTests(unittest.TestCase):
    def test_legacy_read_only_acl_is_migrated_idempotently(self) -> None:
        principal = f"*{current_user_sid()}"

        with tempfile.TemporaryDirectory() as directory:
            private_key = Path(directory) / "id_ed25519"
            expected = b"private-key-test-data"
            private_key.write_bytes(expected)
            subprocess.run(
                [
                    system32_path("icacls.exe"),
                    str(private_key),
                    "/inheritance:r",
                    "/grant:r",
                    f"{principal}:(R)",
                ],
                capture_output=True,
                check=True,
            )

            secure_private_key_permissions(private_key)
            secure_private_key_permissions(private_key)

            self.assertEqual(private_key.read_bytes(), expected)

    def test_acl_is_granted_to_the_sid(self) -> None:
        """Составное имя не резолвится в домене, SID — всегда.

        Живая проверка: `icacls` принимает SID только в форме `*S-1-...`, на
        голый SID отвечает «нет сопоставления имени с SID» — той же ошибкой,
        что и на `КОМПЬЮТЕР\\пользователь` доменной учётки.
        """
        captured: dict[str, list[str]] = {}

        def fake_run(args, **kwargs):
            captured["args"] = args
            return subprocess.CompletedProcess(args, 0, "", "")

        sid = "S-1-5-21-2052111302-1275210071-1801674531-1105"
        with mock.patch.dict(
            "os.environ",
            {"COMPUTERNAME": "DESKTOP-P1097DP", "USERNAME": "venchwork"},
            clear=True,
        ), mock.patch(
            "ven4control.ssh_service.current_user_sid", return_value=sid
        ), mock.patch(
            "ven4control.ssh_service.subprocess.run", side_effect=fake_run
        ):
            with tempfile.TemporaryDirectory() as directory:
                private_key = Path(directory) / "id_ed25519"
                private_key.write_bytes(b"private-key-test-data")
                secure_private_key_permissions(private_key)

        principal_argument = next(
            argument for argument in captured["args"] if argument.endswith(":(F)")
        )
        self.assertEqual(f"*{sid}:(F)", principal_argument)
        self.assertNotIn("DESKTOP-P1097DP", principal_argument)

    def test_icacls_is_taken_from_system32(self) -> None:
        """Имя без пути Windows ищет и в рабочем каталоге процесса."""
        captured: dict[str, list[str]] = {}

        def fake_run(args, **kwargs):
            captured["args"] = args
            return subprocess.CompletedProcess(args, 0, "", "")

        with mock.patch(
            "ven4control.ssh_service.current_user_sid", return_value="S-1-5-21-1-2-3"
        ), mock.patch(
            "ven4control.ssh_service.subprocess.run", side_effect=fake_run
        ):
            with tempfile.TemporaryDirectory() as directory:
                private_key = Path(directory) / "id_ed25519"
                private_key.write_bytes(b"private-key-test-data")
                secure_private_key_permissions(private_key)

        executable = captured["args"][0]
        self.assertTrue(os.path.isabs(executable), executable)
        self.assertTrue(
            executable.lower().endswith("system32\\icacls.exe"), executable
        )


if __name__ == "__main__":
    unittest.main()
