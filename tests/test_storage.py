import sqlite3
import tempfile
import unittest
from contextlib import closing
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

            self.assertTrue(storage.delete(device.id))
            self.assertEqual([], storage.list_devices())

    def test_all_fields_survive_save_and_reload(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "devices.db"
            storage = DeviceStorage(path)
            saved = storage.save(
                Device(
                    None, "Домашний ПК", "198.51.100.10", 2222, "user",
                    auth_type="key",
                    key_path=r"C:\Users\User\AppData\Local\Ven4Control\ssh\id_ed25519",
                    save_credentials=True,
                    fingerprint="SHA256:abcdef0123456789",
                    log_background=True,
                    rdp_port=3390,
                    rdp_checked=True,
                    rdp_available=True,
                )
            )

            reopened = DeviceStorage(path)
            loaded = reopened.list_devices()[0]
            self.assertEqual(saved.id, loaded.id)
            self.assertEqual("Домашний ПК", loaded.name)
            self.assertEqual("198.51.100.10", loaded.host)
            self.assertEqual(2222, loaded.port)
            self.assertEqual("user", loaded.username)
            self.assertEqual("key", loaded.auth_type)
            self.assertEqual(
                r"C:\Users\User\AppData\Local\Ven4Control\ssh\id_ed25519",
                loaded.key_path,
            )
            self.assertTrue(loaded.save_credentials)
            self.assertEqual("SHA256:abcdef0123456789", loaded.fingerprint)
            self.assertTrue(loaded.log_background)
            self.assertEqual(3390, loaded.rdp_port)
            self.assertTrue(loaded.rdp_checked)
            self.assertTrue(loaded.rdp_available)

    def test_existing_database_is_opened_without_changes(self):
        """Повторное открытие не должно менять схему рабочей базы."""
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "devices.db"
            storage = DeviceStorage(path)
            storage.save(
                Device(
                    None, "Роутер", "192.168.99.1", 22, "root",
                    auth_type="key", key_path="C:/keys/id_ed25519",
                    fingerprint="SHA256:router",
                )
            )
            with closing(sqlite3.connect(path)) as db:
                before = db.execute(
                    "SELECT sql FROM sqlite_master WHERE name='devices'"
                ).fetchone()[0]

            DeviceStorage(path)

            with closing(sqlite3.connect(path)) as db:
                after = db.execute(
                    "SELECT sql FROM sqlite_master WHERE name='devices'"
                ).fetchone()[0]
            self.assertEqual(before, after)

    def test_legacy_database_without_fingerprint_is_migrated(self):
        """База прошлой версии открывается, старые поля сохраняются."""
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "devices.db"
            with closing(sqlite3.connect(path)) as db:
                db.execute(
                    """
                    CREATE TABLE devices (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT NOT NULL,
                        host TEXT NOT NULL,
                        port INTEGER NOT NULL DEFAULT 22,
                        username TEXT NOT NULL,
                        auth_type TEXT NOT NULL DEFAULT 'password',
                        key_path TEXT NOT NULL DEFAULT '',
                        save_credentials INTEGER NOT NULL DEFAULT 0,
                        UNIQUE(host, port, username)
                    )
                    """
                )
                db.execute(
                    "INSERT INTO devices (name, host, port, username, auth_type,"
                    " key_path, save_credentials) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    ("Старый", "100.64.0.2", 2200, "root", "key", "C:/keys/id", 1),
                )
                db.commit()

            devices = DeviceStorage(path).list_devices()
            self.assertEqual(1, len(devices))
            device = devices[0]
            self.assertEqual("Старый", device.name)
            self.assertEqual("100.64.0.2", device.host)
            self.assertEqual(2200, device.port)
            self.assertEqual("key", device.auth_type)
            self.assertEqual("C:/keys/id", device.key_path)
            self.assertTrue(device.save_credentials)
            self.assertEqual("", device.fingerprint)
            self.assertFalse(device.log_background)
            self.assertEqual(3389, device.rdp_port)
            self.assertFalse(device.rdp_checked)
            self.assertFalse(device.rdp_available)

    def test_database_without_rdp_columns_keeps_saved_devices(self):
        """База версии 0.1.0-beta открывается новым кодом без потери устройств.

        Проверяется полный набор колонок предыдущего релиза: рабочая база
        пользователя выглядит именно так, и после добавления колонок RDP
        все сохранённые подключения должны читаться и сохраняться дальше.
        """
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "devices.db"
            with closing(sqlite3.connect(path)) as db:
                db.execute(
                    """
                    CREATE TABLE devices (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT NOT NULL,
                        host TEXT NOT NULL,
                        port INTEGER NOT NULL DEFAULT 22,
                        username TEXT NOT NULL,
                        auth_type TEXT NOT NULL DEFAULT 'password',
                        key_path TEXT NOT NULL DEFAULT '',
                        save_credentials INTEGER NOT NULL DEFAULT 0,
                        fingerprint TEXT NOT NULL DEFAULT '',
                        log_background INTEGER NOT NULL DEFAULT 0,
                        UNIQUE(host, port, username)
                    )
                    """
                )
                db.executemany(
                    "INSERT INTO devices (name, host, port, username, auth_type,"
                    " key_path, save_credentials, fingerprint, log_background)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        (
                            "Роутер", "192.168.99.1", 22, "root", "key",
                            "C:/keys/id_ed25519", 1, "SHA256:router", 1,
                        ),
                        (
                            "Домашний ПК", "100.64.0.7", 2222, "user", "password",
                            "", 0, "SHA256:pc", 0,
                        ),
                    ],
                )
                db.commit()

            storage = DeviceStorage(path)
            devices = {device.name: device for device in storage.list_devices()}
            self.assertEqual({"Домашний ПК", "Роутер"}, set(devices))

            router = devices["Роутер"]
            self.assertEqual("192.168.99.1", router.host)
            self.assertEqual(22, router.port)
            self.assertEqual("key", router.auth_type)
            self.assertEqual("C:/keys/id_ed25519", router.key_path)
            self.assertTrue(router.save_credentials)
            self.assertEqual("SHA256:router", router.fingerprint)
            self.assertTrue(router.log_background)

            computer = devices["Домашний ПК"]
            self.assertEqual("100.64.0.7", computer.host)
            self.assertEqual(2222, computer.port)
            self.assertEqual("user", computer.username)
            self.assertEqual("SHA256:pc", computer.fingerprint)

            # Новые поля получают безопасные значения: RDP ещё не проверялся.
            for device in devices.values():
                self.assertEqual(3389, device.rdp_port)
                self.assertFalse(device.rdp_checked)
                self.assertFalse(device.rdp_available)

            # Старая запись остаётся обновляемой после добавления колонок.
            computer.rdp_checked = True
            computer.rdp_available = True
            storage.save(computer)
            reopened = {
                device.name: device for device in DeviceStorage(path).list_devices()
            }
            self.assertTrue(reopened["Домашний ПК"].rdp_available)
            self.assertEqual("SHA256:pc", reopened["Домашний ПК"].fingerprint)
            self.assertFalse(reopened["Роутер"].rdp_checked)
            self.assertTrue(reopened["Роутер"].log_background)

    def test_rdp_result_survives_reopen(self):
        """Результат проверки RDP не должен запрашиваться заново при запуске."""
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "devices.db"
            storage = DeviceStorage(path)
            device = storage.save(Device(None, "ПК", "198.51.100.10", 22, "user"))
            self.assertFalse(device.rdp_checked)

            device.rdp_checked = True
            device.rdp_available = False
            storage.save(device)

            loaded = DeviceStorage(path).list_devices()[0]
            self.assertTrue(loaded.rdp_checked)
            self.assertFalse(loaded.rdp_available)

    def test_null_values_do_not_break_key_authentication(self):
        """Пустое имя из чужой базы не должно ломать чтение данных входа."""
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "devices.db"
            with closing(sqlite3.connect(path)) as db:
                db.execute(
                    "CREATE TABLE devices (id INTEGER PRIMARY KEY AUTOINCREMENT,"
                    " name TEXT, host TEXT, port INTEGER, username TEXT,"
                    " auth_type TEXT, key_path TEXT, save_credentials INTEGER,"
                    " fingerprint TEXT)"
                )
                db.execute(
                    "INSERT INTO devices (id, name, host, port, username, auth_type,"
                    " key_path, save_credentials, fingerprint)"
                    " VALUES (1, NULL, 'h', 22, 'root', 'key', 'C:/k', 1, 'SHA256:x')"
                )
                db.commit()

            device = DeviceStorage(path).list_devices()[0]
            self.assertEqual("", device.name)
            self.assertEqual("key", device.auth_type)
            self.assertEqual("C:/k", device.key_path)
            self.assertEqual("SHA256:x", device.fingerprint)

    def test_background_logging_flag_survives_reopen(self):
        """Отметка фонового логирования должна переживать перезапуск."""
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "devices.db"
            storage = DeviceStorage(path)
            device = storage.save(Device(None, "Роутер", "192.168.99.1", 22, "root"))
            self.assertFalse(device.log_background)

            device.log_background = True
            storage.save(device)

            self.assertTrue(DeviceStorage(path).list_devices()[0].log_background)

    def test_duplicate_device_reports_readable_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            storage = DeviceStorage(Path(temporary) / "devices.db")
            storage.save(Device(None, "Роутер", "192.168.99.1", 22, "root"))
            with self.assertRaises(ValueError) as context:
                storage.save(Device(None, "Копия", "192.168.99.1", 22, "root"))
            self.assertIn("192.168.99.1", str(context.exception))

    def test_update_of_missing_device_is_reported(self):
        with tempfile.TemporaryDirectory() as temporary:
            storage = DeviceStorage(Path(temporary) / "devices.db")
            device = storage.save(Device(None, "Роутер", "192.168.99.1", 22, "root"))
            storage.delete(device.id)
            device.fingerprint = "SHA256:new"
            with self.assertRaises(LookupError):
                storage.save(device)

    def test_delete_reports_missing_device(self):
        with tempfile.TemporaryDirectory() as temporary:
            storage = DeviceStorage(Path(temporary) / "devices.db")
            self.assertFalse(storage.delete(404))


if __name__ == "__main__":
    unittest.main()
