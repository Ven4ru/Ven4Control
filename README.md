# Ven4Control

Закрытый локальный менеджер компьютеров и OpenWrt-роутеров.

## Возможности MVP

- список устройств и проверка доступности SSH;
- добавление по адресу, логину и паролю либо SSH-ключу;
- сохранение пароля и passphrase в Windows Credential Manager;
- удаление сохранённых данных отдельно от устройства;
- генерация собственного SSH-ключа приложения;
- установка публичного ключа на Linux/OpenWrt через одноразовый пароль;
- готовые инструкции для Linux, OpenWrt и Windows OpenSSH;
- открытие устройства в Windows Terminal.

## Запуск

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
ven4control
```

Данные устройств сохраняются в `%LOCALAPPDATA%\Ven4Control\devices.db`. Секреты хранятся отдельно в Windows Credential Manager.
