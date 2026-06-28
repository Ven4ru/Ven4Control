import tempfile
import unittest
from pathlib import Path

from ven4control.models import Device
from ven4control.ssh_service import ensure_app_key
from ven4control.storage import DeviceStorage


class StorageTests(unittest.TestCase):
    def test_device_lifecycle_and_key_generation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            storage = DeviceStorage(root / "devices.db")
            device = storage.save(Device(None, "Роутер", "100.64.0.1", 22, "root"))

            loaded = storage.list_devices()
            self.assertEqual(1, len(loaded))
            self.assertEqual("Роутер", loaded[0].name)

            private, public = ensure_app_key(root / "id_ed25519")
            self.assertTrue(private.exists())
            self.assertTrue(public.exists())

            storage.delete(device.id)
            self.assertEqual([], storage.list_devices())


if __name__ == "__main__":
    unittest.main()
