import json

import keyring
from keyring.errors import KeyringError


SERVICE_NAME = "Ven4Control"

EMPTY_CREDENTIALS = {"password": "", "passphrase": ""}


def _text(value: object) -> str:
    return "" if value is None else str(value)


class CredentialStore:
    @staticmethod
    def _account(device_id: int) -> str:
        return f"device:{device_id}"

    def save(self, device_id: int, password: str = "", passphrase: str = "") -> None:
        payload = json.dumps(
            {"password": password, "passphrase": passphrase},
            ensure_ascii=False,
        )
        keyring.set_password(SERVICE_NAME, self._account(device_id), payload)

    def load(self, device_id: int) -> dict[str, str]:
        value = keyring.get_password(SERVICE_NAME, self._account(device_id))
        if not value:
            return dict(EMPTY_CREDENTIALS)
        try:
            data = json.loads(value)
        except (TypeError, ValueError):
            data = None
        if not isinstance(data, dict):
            # Запись не в формате приложения: считаем её сохранённым паролем,
            # чтобы не потерять доступ к устройству.
            return {"password": value, "passphrase": ""}
        return {
            "password": _text(data.get("password")),
            "passphrase": _text(data.get("passphrase")),
        }

    def delete(self, device_id: int) -> None:
        try:
            keyring.delete_password(SERVICE_NAME, self._account(device_id))
        except KeyringError:
            # Записи нет или хранилище недоступно: удалять нечего.
            pass
