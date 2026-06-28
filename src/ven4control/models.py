from dataclasses import dataclass


@dataclass(slots=True)
class Device:
    id: int | None
    name: str
    host: str
    port: int
    username: str
    auth_type: str = "password"
    key_path: str = ""
    save_credentials: bool = False
    fingerprint: str = ""
