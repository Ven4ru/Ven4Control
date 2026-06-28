import json

import keyring


SERVICE_NAME = "Ven4Control"


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
            return {"password": "", "passphrase": ""}
        try:
            data = json.loads(value)
            return {
                "password": str(data.get("password", "")),
                "passphrase": str(data.get("passphrase", "")),
            }
        except (TypeError, ValueError):
            return {"password": "", "passphrase": ""}

    def delete(self, device_id: int) -> None:
        try:
            keyring.delete_password(SERVICE_NAME, self._account(device_id))
        except keyring.errors.PasswordDeleteError:
            pass
