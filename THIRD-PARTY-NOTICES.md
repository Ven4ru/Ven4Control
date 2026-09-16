# Сторонние компоненты

Сам Ven4Control распространяется под [MIT](LICENSE). Готовый `Ven4Control.exe`
собирается PyInstaller в один файл, поэтому внутрь него попадают перечисленные
ниже библиотеки со своими лицензиями. Этот документ — обязательное уведомление
об их использовании; он входит и в репозиторий, и в саму сборку.

| Компонент | Версия | Лицензия | Исходный код |
|---|---|---|---|
| PySide6 (привязки Qt для Python) | 6.11.1 | LGPL-3.0-only ИЛИ GPL-2.0-only ИЛИ GPL-3.0-only | https://code.qt.io/cgit/pyside/pyside-setup.git/ |
| shiboken6 (рантайм привязок Qt) | 6.11.1 | LGPL-3.0-only ИЛИ GPL-2.0-only ИЛИ GPL-3.0-only | https://code.qt.io/cgit/pyside/pyside-setup.git/ |
| Qt (библиотеки, поставляемые с PySide6) | 6.11.x | LGPL-3.0 | https://code.qt.io/cgit/qt/qt5.git/ |
| AsyncSSH | 2.24.0 | EPL-2.0 ИЛИ GPL-2.0-or-later | https://github.com/ronf/asyncssh |
| cryptography (зависимость AsyncSSH) | 49.0.0 | Apache-2.0 ИЛИ BSD-3-Clause | https://github.com/pyca/cryptography |
| keyring | 25.7.0 | MIT | https://github.com/jaraco/keyring |
| python-docx | 1.2.0 | MIT | https://github.com/python-openxml/python-docx |
| openpyxl | 3.1.5 | MIT | https://foss.heptapod.net/openpyxl/openpyxl |

## Qt и PySide6 — LGPL-3.0

Qt и PySide6 используются **без изменений** и подключаются динамически: внутри
onefile-сборки они остаются отдельными библиотеками (`.pyd`/`.dll`), которые
PyInstaller распаковывает во временный каталог и загружает на старте.

LGPL-3.0 даёт вам право заменить эти библиотеки на свою сборку той же версии.
Практически это делается так: установите Python 3.12+, поставьте нужную вам
сборку PySide6 (`pip install PySide6==6.11.1` или собранную самостоятельно),
получите исходный код Ven4Control (`git clone https://github.com/Ven4ru/Ven4Control`)
и запустите `python -m ven4control.app` — приложение будет работать поверх вашей
версии Qt. Полный текст лицензии: https://www.gnu.org/licenses/lgpl-3.0.html

## AsyncSSH — EPL-2.0

AsyncSSH используется без изменений. Исходный код доступен по ссылке в таблице
выше. Полный текст лицензии: https://www.eclipse.org/legal/epl-2.0/

## Остальные компоненты

keyring, python-docx и openpyxl распространяются под MIT, cryptography — под
Apache-2.0 / BSD-3-Clause. Тексты лицензий доступны в репозиториях этих проектов
по ссылкам выше.
