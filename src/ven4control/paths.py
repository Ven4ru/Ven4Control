"""Пути приложения в профиле пользователя.

Все данные Ven4Control лежат в одном дереве `%LOCALAPPDATA%\\Ven4Control`:
база устройств, ключ приложения, резервные копии и журналы фоновых сессий.
"""
import os
from pathlib import Path


APP_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Ven4Control"
DB_PATH = APP_DIR / "devices.db"
APP_KEY_PATH = APP_DIR / "ssh" / "id_ed25519"
BACKUP_DIR = APP_DIR / "backups"
LOG_DIR = APP_DIR / "logs"
