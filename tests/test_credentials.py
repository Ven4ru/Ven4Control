import unittest
from unittest import mock

from keyring.errors import NoKeyringError, PasswordDeleteError

from ven4control import credentials
from ven4control.credentials import SERVICE_NAME, CredentialStore


class CredentialStoreTests(unittest.TestCase):
    def test_saved_pair_is_returned_unchanged(self) -> None:
        store = CredentialStore()
        storage: dict[tuple[str, str], str] = {}

        def set_password(service: str, account: str, value: str) -> None:
            storage[(service, account)] = value

        with mock.patch.object(credentials.keyring, "set_password", set_password):
            store.save(7, password="па$$ ворд", passphrase="фраза")

        self.assertEqual([(SERVICE_NAME, "device:7")], list(storage))
        with mock.patch.object(
            credentials.keyring, "get_password", lambda s, a: storage[(s, a)]
        ):
            loaded = store.load(7)
        self.assertEqual({"password": "па$$ ворд", "passphrase": "фраза"}, loaded)

    def test_missing_record_returns_empty_pair(self) -> None:
        with mock.patch.object(credentials.keyring, "get_password", lambda s, a: None):
            self.assertEqual(
                {"password": "", "passphrase": ""}, CredentialStore().load(1)
            )

    def test_empty_result_is_not_shared_between_calls(self) -> None:
        store = CredentialStore()
        with mock.patch.object(credentials.keyring, "get_password", lambda s, a: None):
            first = store.load(1)
            first["password"] = "испорчено"
            second = store.load(1)
        self.assertEqual("", second["password"])

    def test_plain_text_record_is_treated_as_password(self) -> None:
        with mock.patch.object(
            credentials.keyring, "get_password", lambda s, a: "простой-пароль"
        ):
            self.assertEqual(
                {"password": "простой-пароль", "passphrase": ""},
                CredentialStore().load(3),
            )

    def test_numeric_record_does_not_crash(self) -> None:
        with mock.patch.object(credentials.keyring, "get_password", lambda s, a: "1234"):
            self.assertEqual(
                {"password": "1234", "passphrase": ""}, CredentialStore().load(4)
            )

    def test_null_fields_become_empty_strings(self) -> None:
        payload = '{"password": null, "passphrase": null}'
        with mock.patch.object(credentials.keyring, "get_password", lambda s, a: payload):
            self.assertEqual(
                {"password": "", "passphrase": ""}, CredentialStore().load(5)
            )

    def test_delete_survives_missing_record_and_missing_backend(self) -> None:
        for error in (PasswordDeleteError("нет записи"), NoKeyringError("нет хранилища")):
            with self.subTest(error=type(error).__name__):
                with mock.patch.object(
                    credentials.keyring,
                    "delete_password",
                    mock.Mock(side_effect=error),
                ):
                    CredentialStore().delete(9)


if __name__ == "__main__":
    unittest.main()
