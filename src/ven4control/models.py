from dataclasses import dataclass


@dataclass(slots=True)
class Device:
    id: int | None
    name: str
    host: str
    port: int
    username: str
    # Свободная метка вида «Дом» или «Работа»: только для группировки взглядом
    # в списке. Поле названо не group, потому что group — ключевое слово SQL.
    group_name: str = ""
    auth_type: str = "password"
    key_path: str = ""
    save_credentials: bool = False
    fingerprint: str = ""
    # Запускать фоновое логирование при старте приложения.
    log_background: bool = False
    # Порт RDP устройства (стандартный порт Windows).
    rdp_port: int = 3389
    # Признаки хранятся раздельно, чтобы отличать «ещё не проверяли»
    # от «проверили, RDP не отвечает»: результат проверки запоминается
    # и не запрашивается заново при каждом запуске.
    rdp_checked: bool = False
    rdp_available: bool = False
