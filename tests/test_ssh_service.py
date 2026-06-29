import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from ven4control.ssh_service import secure_private_key_permissions


@unittest.skipUnless(os.name == "nt", "Проверка ACL предназначена для Windows")
class PrivateKeyPermissionsTests(unittest.TestCase):
    def test_legacy_read_only_acl_is_migrated_idempotently(self) -> None:
        username = os.environ["USERNAME"]
        domain = os.environ.get("USERDOMAIN")
        principal = f"{domain}\\{username}" if domain else username

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


if __name__ == "__main__":
    unittest.main()
