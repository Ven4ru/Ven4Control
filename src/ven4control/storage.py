import sqlite3
from contextlib import closing
from pathlib import Path

from .models import Device


# Столбцы таблицы устройств и их определения для добавления в старые базы.
# Порядок совпадает с исходной схемой: он используется только при
# восстановлении отсутствующих столбцов, существующие столбцы не изменяются.
DEVICE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("name", "TEXT NOT NULL DEFAULT ''"),
    ("host", "TEXT NOT NULL DEFAULT ''"),
    ("port", "INTEGER NOT NULL DEFAULT 22"),
    ("username", "TEXT NOT NULL DEFAULT ''"),
    ("auth_type", "TEXT NOT NULL DEFAULT 'password'"),
    ("key_path", "TEXT NOT NULL DEFAULT ''"),
    ("save_credentials", "INTEGER NOT NULL DEFAULT 0"),
    ("fingerprint", "TEXT NOT NULL DEFAULT ''"),
    ("log_background", "INTEGER NOT NULL DEFAULT 0"),
    ("rdp_port", "INTEGER NOT NULL DEFAULT 3389"),
    ("rdp_checked", "INTEGER NOT NULL DEFAULT 0"),
    ("rdp_available", "INTEGER NOT NULL DEFAULT 0"),
)


class DeviceStorage:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
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
                    log_background INTEGER NOT NULL DEFAULT 0,
                    rdp_port INTEGER NOT NULL DEFAULT 3389,
                    rdp_checked INTEGER NOT NULL DEFAULT 0,
                    rdp_available INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(host, port, username)
                )
                """
            )
            self._restore_missing_columns(db)
            db.commit()

    @staticmethod
    def _restore_missing_columns(db: sqlite3.Connection) -> None:
        """Добавляет столбцы, которых нет в базе, созданной прошлой версией.

        Существующие столбцы и записи не изменяются: для актуальной схемы
        метод ничего не делает.
        """
        existing = {row["name"] for row in db.execute("PRAGMA table_info(devices)")}
        for column, definition in DEVICE_COLUMNS:
            if column not in existing:
                db.execute(f"ALTER TABLE devices ADD COLUMN {column} {definition}")

    def list_devices(self) -> list[Device]:
        with closing(self._connect()) as db:
            rows = db.execute("SELECT * FROM devices ORDER BY name COLLATE NOCASE").fetchall()
        return [self._from_row(row) for row in rows]

    def save(self, device: Device) -> Device:
        with closing(self._connect()) as db:
            try:
                if device.id is None:
                    cursor = db.execute(
                        """
                        INSERT INTO devices
                        (name, host, port, username, auth_type, key_path, save_credentials,
                         fingerprint, log_background, rdp_port, rdp_checked, rdp_available)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            device.name, device.host, device.port, device.username,
                            device.auth_type, device.key_path, int(device.save_credentials),
                            device.fingerprint, int(device.log_background),
                            device.rdp_port, int(device.rdp_checked),
                            int(device.rdp_available),
                        ),
                    )
                    device.id = int(cursor.lastrowid)
                else:
                    cursor = db.execute(
                        """
                        UPDATE devices SET name=?, host=?, port=?, username=?, auth_type=?,
                        key_path=?, save_credentials=?, fingerprint=?, log_background=?,
                        rdp_port=?, rdp_checked=?, rdp_available=?
                        WHERE id=?
                        """,
                        (
                            device.name, device.host, device.port, device.username,
                            device.auth_type, device.key_path, int(device.save_credentials),
                            device.fingerprint, int(device.log_background),
                            device.rdp_port, int(device.rdp_checked),
                            int(device.rdp_available), device.id,
                        ),
                    )
                    if cursor.rowcount == 0:
                        raise LookupError(
                            f"Устройство «{device.name}» отсутствует в базе, "
                            "изменения не сохранены."
                        )
            except sqlite3.IntegrityError as error:
                raise ValueError(
                    f"Устройство {device.username}@{device.host}:{device.port} "
                    "уже есть в списке."
                ) from error
            db.commit()
        return device

    def delete(self, device_id: int) -> bool:
        with closing(self._connect()) as db:
            cursor = db.execute("DELETE FROM devices WHERE id=?", (device_id,))
            db.commit()
        return cursor.rowcount > 0

    @staticmethod
    def _from_row(row: sqlite3.Row) -> Device:
        # Значения читаются как есть; подстановка выполняется только для NULL,
        # который может встретиться в базе, созданной другой версией.
        def value(column: str, fallback):
            stored = row[column]
            return fallback if stored is None else stored

        return Device(
            id=row["id"], name=value("name", ""), host=value("host", ""),
            port=int(value("port", 22)),
            username=value("username", ""), auth_type=value("auth_type", "password"),
            key_path=value("key_path", ""),
            save_credentials=bool(value("save_credentials", 0)),
            fingerprint=value("fingerprint", ""),
            log_background=bool(value("log_background", 0)),
            rdp_port=int(value("rdp_port", 3389)),
            rdp_checked=bool(value("rdp_checked", 0)),
            rdp_available=bool(value("rdp_available", 0)),
        )
