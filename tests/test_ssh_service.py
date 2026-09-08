import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import asyncssh

from ven4control.ssh_service import ensure_app_key, secure_private_key_permissions
from ven4control.windows_identity import current_user_principal


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


@unittest.skipUnless(os.name == "nt", "Проверка ACL предназначена для Windows")
class PrivateKeyPermissionsTests(unittest.TestCase):
    def test_legacy_read_only_acl_is_migrated_idempotently(self) -> None:
        principal = current_user_principal()

        with tempfile.TemporaryDirectory() as directory:
            private_key = Path(directory) / "id_ed25519"
            expected = b"private-key-test-data"
            private_key.write_bytes(expected)
            subprocess.run(
                [
                    "icacls",
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

    def test_workgroup_is_never_used_as_the_area(self) -> None:
        """Живая проверка: рабочая группа не сопоставляется с учётной записью.

        На машине вне домена Windows кладёт в USERDOMAIN имя рабочей группы,
        и `icacls WORKGROUP\\user:(F)` отвечает отказом — нет сопоставления
        имени с SID. Та же причина, что уже чинили в `scheduled_task`.
        """
        captured: dict[str, list[str]] = {}

        def fake_run(args, **kwargs):
            captured["args"] = args
            return subprocess.CompletedProcess(args, 0, "", "")

        with mock.patch.dict(
            "os.environ",
            {
                "USERDOMAIN": "WORKGROUP",
                "COMPUTERNAME": "DESKTOP-P1097DP",
                "USERNAME": "venchwork",
            },
            clear=True,
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
        self.assertNotIn("WORKGROUP", principal_argument)
        self.assertEqual("DESKTOP-P1097DP\\venchwork:(F)", principal_argument)


if __name__ == "__main__":
    unittest.main()
