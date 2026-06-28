import sqlite3
from contextlib import closing
from pathlib import Path

from .models import Device


class DeviceStorage:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with closing(self._connect()) as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS devices (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    host TEXT NOT NULL,
                    port INTEGER NOT NULL DEFAULT 22,
                    username TEXT NOT NULL,
                    auth_type TEXT NOT NULL DEFAULT 'password',
                    key_path TEXT NOT NULL DEFAULT '',
                    save_credentials INTEGER NOT NULL DEFAULT 0,
                    fingerprint TEXT NOT NULL DEFAULT '',
                    UNIQUE(host, port, username)
                )
                """
            )
            db.commit()

    def list_devices(self) -> list[Device]:
        with closing(self._connect()) as db:
            rows = db.execute("SELECT * FROM devices ORDER BY name COLLATE NOCASE").fetchall()
        return [self._from_row(row) for row in rows]

    def save(self, device: Device) -> Device:
        with closing(self._connect()) as db:
            if device.id is None:
                cursor = db.execute(
                    """
                    INSERT INTO devices
                    (name, host, port, username, auth_type, key_path, save_credentials, fingerprint)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        device.name, device.host, device.port, device.username,
                        device.auth_type, device.key_path, int(device.save_credentials),
                        device.fingerprint,
                    ),
                )
                device.id = int(cursor.lastrowid)
            else:
                db.execute(
                    """
                    UPDATE devices SET name=?, host=?, port=?, username=?, auth_type=?,
                    key_path=?, save_credentials=?, fingerprint=? WHERE id=?
                    """,
                    (
                        device.name, device.host, device.port, device.username,
                        device.auth_type, device.key_path, int(device.save_credentials),
                        device.fingerprint, device.id,
                    ),
                )
            db.commit()
        return device

    def delete(self, device_id: int) -> None:
        with closing(self._connect()) as db:
            db.execute("DELETE FROM devices WHERE id=?", (device_id,))
            db.commit()

    @staticmethod
    def _from_row(row: sqlite3.Row) -> Device:
        return Device(
            id=row["id"], name=row["name"], host=row["host"], port=row["port"],
            username=row["username"], auth_type=row["auth_type"],
            key_path=row["key_path"], save_credentials=bool(row["save_credentials"]),
            fingerprint=row["fingerprint"],
        )
