# Установка приложений на устройство (фаза 1: OpenWrt/Ubuntu) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Новая страница «Приложения» в `DeviceControlDialog` — поиск пакетов в уже настроенных на устройстве репозиториях (`apk`/`opkg`/`apt`) и установка выбранного одной кнопкой.

**Architecture:** Парсинг вывода команд поиска — чистые функции в `remote_control.py` (тестируются без сети, на реальных зафиксированных строках вывода). Сетевая часть (`search_packages`/`install_package`) — тот же паттерн, что у `update_packages`: ветвление по платформе из `detect_platform`, один SSH-вызов на операцию. UI-страница — седьмой пункт сайдбар-навигации `DeviceControlDialog`, тот же `_start`/`AsyncTask` паттерн, что у страницы «Обслуживание».

**Tech Stack:** Python 3.12, PySide6, asyncssh, unittest.

## Global Constraints

- **Поисковый термин — пользовательский ввод, идёт в shell-команду по SSH: обязательно `shlex.quote()`.** Без экранирования это command injection на устройство пользователя — строка вида `x; rm -rf /` в поле поиска иначе выполнилась бы буквально. Имя пакета для установки (пришло из разбора вывода устройства, не от пользователя напрямую) экранируется тем же способом — дешёвая защита, тот же принцип, что и для термина.
- Команды поиска/установки — ровно те, что проверены живьём в спеке (`docs/superpowers/specs/2026-09-08-app-catalog-design.md`) и в этом плане: `apk search -v -d`/`apk add`, `opkg list | grep`/`opkg install` (эта ветка НЕ проверена на реальном устройстве — ни одно устройство пользователя сейчас не на opkg, все на apk), `apt-cache search`/`apt-get install`. Не менять флаги/синтаксис без новой живой проверки.
- Код и UI-строки — на русском, в стиле остального проекта. Комментарии — только там, где решение неочевидно.
- Ни одного упоминания Claude/Anthropic/AI нигде — ни в коде, ни в commit message. Commit message — обычный текст в стиле `git log` этого репозитория, без трейлеров.
- Полный набор тестов (`.venv/Scripts/python.exe -m pytest -q`, venv в корне репозитория) остаётся зелёным на каждом коммите, включая все существующие 365.
- Windows (`winget`) и кнопка «Ven4Tools» — явно вне рамок этого плана (см. спеку, раздел «Явно вне рамок»). Не добавлять.

---

## Task 1: Разбор вывода команд поиска — чистые функции

**Files:**
- Modify: `src/ven4control/remote_control.py`
- Test: `tests/test_remote_control.py`

**Interfaces:**
- Produces: `@dataclass PackageResult(name: str, description: str)`, `parse_apk_search(output: str) -> list[PackageResult]`, `parse_opkg_search(output: str) -> list[PackageResult]`, `parse_apt_search(output: str) -> list[PackageResult]`.

- [ ] **Step 1: Написать тесты на реальных зафиксированных строках вывода**

В начале `tests/test_remote_control.py` есть многострочный
`from ven4control.remote_control import (...)` — добавить в него (сохраняя
алфавитный порядок остальных имён) `PackageResult`, `parse_apk_search`,
`parse_apt_search`, `parse_opkg_search`. `search_packages`/`install_package`
понадобятся отдельно в Task 2 — их в этот же импорт можно добавить сразу,
чтобы не редактировать этот блок дважды.

Добавить в `tests/test_remote_control.py`:

```python
class ApkSearchParsingTests(unittest.TestCase):
    """Строки — реальный вывод `apk search -v -d sftp` с домашнего роутера
    (192.168.1.1, OpenWrt 25.12.0, apk-tools 3.0.2), не выдуманы."""

    def test_real_output_from_a_live_router(self) -> None:
        output = (
            "announce-1.0.1-r1 - Announce services on the network with "
            "Zeroconf/Bonjour.\n"
            "erlang-ssh-28.0.3-r1 - Erlang/OTP implementation of the Secure "
            "Shell protocol, with SSH & SFTP support.\n"
            "openssh-sftp-avahi-service-10.3_p1-r1 - This package contains "
            "the service definition for announcing SFTP support via "
            "mDNS/DNS-SD.\n"
            "openssh-sftp-client-10.3_p1-r1 - OpenSSH SFTP client.\n"
        )
        results = parse_apk_search(output)
        self.assertEqual(
            ["announce", "erlang-ssh", "openssh-sftp-avahi-service", "openssh-sftp-client"],
            [item.name for item in results],
        )
        self.assertEqual("OpenSSH SFTP client.", results[3].description)

    def test_version_suffix_is_stripped_from_the_name(self) -> None:
        # Живая находка: apk add с версией в имени (как в выводе search без -q)
        # падает с «no such package» — install должен получать чистое имя.
        results = parse_apk_search("vsftpd-3.0.5-r6 - FTP server.\n")
        self.assertEqual("vsftpd", results[0].name)

    def test_multi_word_version_suffix(self) -> None:
        # openssh-sftp-server-10.3_p1-r1: версия «10.3_p1» с подчёркиванием —
        # не просто «X.Y.Z», эвристика должна справляться и с этим.
        results = parse_apk_search(
            "openssh-sftp-server-10.3_p1-r1 - OpenSSH SFTP server.\n"
        )
        self.assertEqual("openssh-sftp-server", results[0].name)

    def test_blank_lines_are_skipped(self) -> None:
        self.assertEqual([], parse_apk_search("\n\n"))

    def test_no_matches_is_an_empty_list(self) -> None:
        self.assertEqual([], parse_apk_search(""))


class OpkgSearchParsingTests(unittest.TestCase):
    def test_name_version_description_format(self) -> None:
        # Формат `opkg list`: "имя - версия - описание" — не проверено на
        # реальном устройстве (нет доступного opkg-роутера), задокументировано
        # как предположение по формату, а не подтверждённый факт.
        output = "openssh-sftp-server - 9.6-r1 - OpenSSH SFTP server\n"
        results = parse_opkg_search(output)
        self.assertEqual([("openssh-sftp-server", "OpenSSH SFTP server")],
                          [(r.name, r.description) for r in results])

    def test_no_matches_is_an_empty_list(self) -> None:
        self.assertEqual([], parse_opkg_search(""))


class AptSearchParsingTests(unittest.TestCase):
    """Строки — реальный вывод `apt-cache search sftp` с VPS
    (138.16.152.133, Ubuntu 24.04), не выдуманы."""

    def test_real_output_from_a_live_server(self) -> None:
        output = (
            "curl - command line tool for transferring data with URL syntax\n"
            "gvfs-backends - userspace virtual filesystem - backends\n"
            "lftp - Sophisticated command-line FTP/HTTP/BitTorrent client "
            "programs\n"
        )
        results = parse_apt_search(output)
        self.assertEqual(["curl", "gvfs-backends", "lftp"], [r.name for r in results])
        # Живая находка: описание САМО содержит " - " ("virtual filesystem -
        # backends") — разбор обязан резать по ПЕРВОМУ разделителю, не по
        # первому вхождению паттерна где попало.
        self.assertEqual("userspace virtual filesystem - backends", results[1].description)

    def test_no_matches_is_an_empty_list(self) -> None:
        self.assertEqual([], parse_apt_search(""))
```

- [ ] **Step 2: Прогнать, убедиться что падает**

Run: `.venv\Scripts\python.exe -m pytest tests/test_remote_control.py -k "ApkSearch or OpkgSearch or AptSearch" -v`
Expected: FAIL — `ImportError` (функций ещё нет).

- [ ] **Step 3: Реализовать**

Добавить в начало `src/ven4control/remote_control.py`, в блок импортов: `import shlex` (если ещё не импортирован — проверить существующие импорты файла первой строкой правки).

Добавить в `src/ven4control/remote_control.py` (рядом с другими dataclass — например, после `ServiceInfo`):

```python
@dataclass(slots=True)
class PackageResult:
    """Один результат поиска пакета: имя, готовое для установки, и описание."""

    name: str
    description: str = ""


def _apk_bare_name(versioned: str) -> str:
    """Имя пакета apk без версии.

    `apk search` без `-q` печатает `имя-версия` одной строкой (`apk search
    -q` даёт чистое имя, но тогда пропадает возможность получить описание
    в том же вызове — поэтому парсим версию сами). Правило: с конца
    отрезаются сегменты через дефис, которые начинаются с цифры — версия
    и релиз (`10.3_p1`, `r1`) всегда начинаются с цифры, а имя пакета в
    apk — никогда (проверено на реальных примерах: `vsftpd-3.0.5-r6`,
    `openssh-sftp-server-10.3_p1-r1`, `erlang-ssh-28.0.3-r1`).
    """
    parts = versioned.split("-")
    while len(parts) > 1 and parts[-1][:1].isdigit():
        parts.pop()
    return "-".join(parts)


def parse_apk_search(output: str) -> list[PackageResult]:
    """Разбирает вывод `apk search -v -d <термин>`."""
    results: list[PackageResult] = []
    for line in output.splitlines():
        line = line.strip()
        if not line or " - " not in line:
            continue
        versioned, description = line.split(" - ", 1)
        results.append(PackageResult(_apk_bare_name(versioned.strip()), description.strip()))
    return results


def parse_opkg_search(output: str) -> list[PackageResult]:
    """Разбирает вывод `opkg list | grep <термин>` — формат `имя - версия - описание`."""
    results: list[PackageResult] = []
    for line in output.splitlines():
        line = line.strip()
        parts = line.split(" - ", 2)
        if len(parts) < 2 or not parts[0].strip():
            continue
        name = parts[0].strip()
        description = parts[2].strip() if len(parts) > 2 else ""
        results.append(PackageResult(name, description))
    return results


def parse_apt_search(output: str) -> list[PackageResult]:
    """Разбирает вывод `apt-cache search <термин>` — формат `имя - описание`.

    Разрез строго по ПЕРВОМУ ` - `: описание само может содержать этот же
    разделитель (реальный пример: `gvfs-backends - userspace virtual
    filesystem - backends`), maxsplit=1 обязателен.
    """
    results: list[PackageResult] = []
    for line in output.splitlines():
        line = line.strip()
        if " - " not in line:
            continue
        name, description = line.split(" - ", 1)
        name = name.strip()
        if name:
            results.append(PackageResult(name, description.strip()))
    return results
```

- [ ] **Step 4: Прогнать тесты**

Run: `.venv\Scripts\python.exe -m pytest tests/test_remote_control.py -k "ApkSearch or OpkgSearch or AptSearch" -v`
Expected: все PASS (9 тестов).

- [ ] **Step 5: Прогнать весь набор**

Run: `.venv\Scripts\python.exe -m pytest -q`
Expected: `374 passed` (365 + 9).

- [ ] **Step 6: Commit**

```bash
git add src/ven4control/remote_control.py tests/test_remote_control.py
git commit -m "Добавлен разбор вывода поиска пакетов apk/opkg/apt"
```

---

## Task 2: `search_packages`/`install_package` — сетевая часть

**Files:**
- Modify: `src/ven4control/remote_control.py`
- Test: `tests/test_remote_control.py`

**Interfaces:**
- Consumes: `PackageResult`, `parse_apk_search`, `parse_opkg_search`, `parse_apt_search` (Task 1); `_connect`, `_run`, `detect_platform` (уже существуют в файле).
- Produces: `async def search_packages(device: Device, credentials: dict[str, str], term: str) -> list[PackageResult]`, `async def install_package(device: Device, credentials: dict[str, str], name: str) -> str`.

- [ ] **Step 1: Реализовать (тесты — в Step 2, через мок `FakeConnection` из уже существующего файла тестов)**

Добавить в `src/ven4control/remote_control.py`, после `update_packages`:

```python
async def search_packages(
    device: Device,
    credentials: dict[str, str],
    term: str,
) -> list[PackageResult]:
    """Ищет пакет в уже настроенных на устройстве репозиториях."""
    connection = await _connect(device, credentials)
    try:
        platform, _ = await detect_platform(connection)
        safe_term = shlex.quote(term)
        if platform == "openwrt":
            command = (
                "if command -v apk >/dev/null 2>&1; then "
                "apk update >/dev/null 2>&1; "
                f"apk search -v -d {safe_term} 2>/dev/null; "
                "elif command -v opkg >/dev/null 2>&1; then "
                "opkg update >/dev/null 2>&1; "
                f"opkg list 2>/dev/null | grep -i {safe_term}; "
                "else echo 'Менеджер пакетов не найден' >&2; exit 127; fi"
            )
            parser = parse_apk_search
        else:
            command = (
                "sudo -n apt-get update >/dev/null 2>&1; "
                f"apt-cache search {safe_term}"
            )
            parser = parse_apt_search
        result = await _run(connection, command, timeout=30, check=False)
        if result.exit_status not in (0, None) and not result.stdout.strip():
            detail = (result.stderr or "").strip()
            raise RuntimeError(detail or "Поиск не выполнен.")
        return parser(result.stdout)
    finally:
        connection.close()
        await connection.wait_closed()


async def install_package(
    device: Device,
    credentials: dict[str, str],
    name: str,
) -> str:
    """Устанавливает пакет по имени, полученному из результатов поиска."""
    connection = await _connect(device, credentials)
    try:
        platform, _ = await detect_platform(connection)
        safe_name = shlex.quote(name)
        if platform == "openwrt":
            command = (
                "if command -v apk >/dev/null 2>&1; then "
                f"apk add {safe_name}; "
                "elif command -v opkg >/dev/null 2>&1; then "
                f"opkg install {safe_name}; "
                "else echo 'Менеджер пакетов не найден' >&2; exit 127; fi"
            )
        else:
            command = (
                "sudo -n env DEBIAN_FRONTEND=noninteractive "
                f"apt-get install -y {safe_name}"
            )
        result = await _run(connection, command, timeout=300, check=True)
        return result.stdout.strip() or f"Пакет «{name}» установлен."
    finally:
        connection.close()
        await connection.wait_closed()
```

Важно про `search_packages`: команда для opkg-ветки использует `grep -i` — при отсутствии совпадений `grep` возвращает ненулевой код (это НЕ ошибка, а «ничего не найдено»); для apk/apt отсутствие результатов — код 0 с пустым выводом. Проверка `result.exit_status not in (0, None) and not result.stdout.strip()` отличает «реальный сбой команды» (ненулевой код И пустой вывод) от «просто нет совпадений» (пустой вывод при коде 0) и от «есть результаты, но grep вернул 1 по своей семантике» (непустой вывод — не ошибка, что бы ни было в exit_status).

- [ ] **Step 2: Написать тесты через существующий `FakeConnection`**

Добавить в `tests/test_remote_control.py` (используя уже существующие в файле `device()`, `patched_connect`, `openwrt_connection`/аналогичные хелперы — свериться с их точными именами и сигнатурами в начале файла перед использованием, не изобретать новые):

```python
class SearchPackagesTests(unittest.TestCase):
    def test_apk_device_returns_parsed_results(self) -> None:
        # Маркер "apk search" — подстрока реальной команды, которую строит
        # search_packages для платформы openwrt (см. Step 1). FakeConnection
        # матчит ПЕРВУЮ подходящую запись по подстроке в команде — маркер
        # обязан реально встречаться в отправленной строке, иначе ответ
        # не подставится и вернётся дефолтный "команда не найдена".
        connection = openwrt_connection(
            ("apk search", "openssh-sftp-server-10.3_p1-r1 - OpenSSH SFTP server.\n", 0)
        )
        with patched_connect(connection):
            results = asyncio.run(search_packages(device(), {}, "sftp"))
        self.assertEqual("openssh-sftp-server", results[0].name)

    def test_search_term_is_shell_escaped(self) -> None:
        """Живая защита от command injection: символы шелла не должны уйти как есть."""
        connection = openwrt_connection(("apk search", "", 0))
        with patched_connect(connection):
            asyncio.run(search_packages(device(), {}, "sftp; rm -rf /"))
        executed = connection.commands[-1]
        self.assertNotIn("; rm -rf /", executed)
        self.assertIn("sftp; rm -rf /", executed)  # содержится, но экранировано кавычками


class InstallPackageTests(unittest.TestCase):
    def test_package_name_is_shell_escaped(self) -> None:
        # install_package использует _run(..., check=True) — ответ ОБЯЗАН
        # быть с exit_status=0, иначе _run сам поднимет RuntimeError раньше,
        # чем тест дойдёт до проверки экранирования.
        connection = openwrt_connection(("apk add", "OK", 0))
        with patched_connect(connection):
            asyncio.run(install_package(device(), {}, "pkg`whoami`"))
        executed = connection.commands[-1]
        self.assertNotIn("`whoami`", executed.replace("'`whoami`'", ""))
```

**Перед написанием этого шага — обязательно прочитать существующий класс
`FakeConnection`/`openwrt_connection` целиком в начале `tests/test_remote_control.py`.**
`FakeConnection` уже даёт `self.commands: list[str]` (список всех отправленных
по SSH команд) — ничего добавлять не нужно, он уже есть. `FakeConnection.run`
матчит ответ по ПЕРВОЙ подстроке-маркеру, которая встречается в реальной
отправленной команде (см. код класса) — маркеры в тестах выше подобраны так,
чтобы реально входить в команды, которые строит `search_packages`/
`install_package` в Task 2 Step 1 (`"apk search"`/`"apk add"` — часть веток
именно для платформы `openwrt`, которую и возвращает `openwrt_connection`
для пробы платформы).

- [ ] **Step 3: Прогнать тесты**

Run: `.venv\Scripts\python.exe -m pytest tests/test_remote_control.py -k "SearchPackages or InstallPackage" -v`
Expected: все PASS.

- [ ] **Step 4: Прогнать весь набор**

Run: `.venv\Scripts\python.exe -m pytest -q`
Expected: `377 passed` (374 + 3: `test_apk_device_returns_parsed_results`, `test_search_term_is_shell_escaped`, `test_package_name_is_shell_escaped`).

- [ ] **Step 5: Живая проверка (обязательный шаг — команда реальная, но её парсинг и экранирование стоит увидеть на настоящем устройстве)**

Написать одноразовый скрипт (не коммитить, удалить после), который вызывает
`search_packages(device_с_реальным_fingerprint, {}, "sftp")` против домашнего
роутера (`192.168.1.1`, id=1 в `devices.db`) и печатает результаты — сверить,
что среди них есть `openssh-sftp-server` с осмысленным описанием (сверка с
тем, что показала прямая SSH-проверка при написании плана — 10 результатов,
включая `openssh-sftp-server`, `sshfs`, `vsftpd`). НЕ вызывать
`install_package` на реальном устройстве без явного отдельного разрешения —
это уже меняет систему, а не только читает.

- [ ] **Step 6: Commit**

```bash
git add src/ven4control/remote_control.py tests/test_remote_control.py
git commit -m "Добавлены поиск и установка пакета на устройстве"
```

---

## Task 3: Страница «Приложения» в `DeviceControlDialog`

**Files:**
- Modify: `src/ven4control/control_dialog.py`

**Interfaces:**
- Consumes: `search_packages`, `install_package`, `PackageResult` (Task 2/1).
- Produces: `DeviceControlDialog._create_apps_tab() -> QWidget`, седьмой пункт навигации «Приложения».

- [ ] **Step 1: Добавить импорт**

В блок `from ven4control.remote_control import (...)` добавить `install_package`, `search_packages` (сохранить алфавитный порядок остальных имён).

- [ ] **Step 2: Добавить фабрику страницы**

Добавить в `src/ven4control/control_dialog.py` метод (рядом с `_create_maintenance_tab`):

```python
    def _create_apps_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        search_row = QHBoxLayout()
        self.apps_search_field = QLineEdit()
        self.apps_search_field.setPlaceholderText(
            "Название или часть описания — например, sftp"
        )
        self.apps_search_field.returnPressed.connect(self.search_apps)
        search_button = QPushButton("Найти")
        search_button.clicked.connect(self.search_apps)
        search_row.addWidget(self.apps_search_field, 1)
        search_row.addWidget(search_button)
        layout.addLayout(search_row)

        self.apps_table = QTableWidget(0, 2)
        self.apps_table.setHorizontalHeaderLabels(["Имя", "Описание"])
        self.apps_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.apps_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.apps_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.apps_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        layout.addWidget(self.apps_table, 1)

        self.apps_install_button = QPushButton("Установить выбранный")
        self.apps_install_button.setEnabled(False)
        self.apps_install_button.clicked.connect(self.install_selected_app)
        self.apps_table.itemSelectionChanged.connect(self._update_apps_install_button)
        layout.addWidget(self.apps_install_button)

        self.apps_output = QTextEdit()
        self.apps_output.setReadOnly(True)
        self.apps_output.setMaximumHeight(120)
        layout.addWidget(self.apps_output)

        self._apps_results: list[PackageResult] = []
        return page

    def search_apps(self) -> None:
        term = self.apps_search_field.text().strip()
        if not term:
            return
        self.apps_install_button.setEnabled(False)
        self._start(
            lambda: search_packages(self.device, self.credentials, term),
            self._apps_search_done,
            f"Поиск «{term}»…",
        )

    def _apps_search_done(self, result: object) -> None:
        results = result if isinstance(result, list) else []
        self._apps_results = results
        self.apps_table.setRowCount(len(results))
        for row, item in enumerate(results):
            self.apps_table.setItem(row, 0, QTableWidgetItem(item.name))
            self.apps_table.setItem(row, 1, QTableWidgetItem(item.description))
        self.apps_output.setPlainText(
            f"Найдено: {len(results)}" if results else "Ничего не найдено."
        )

    def _update_apps_install_button(self) -> None:
        self.apps_install_button.setEnabled(
            bool(self.apps_table.selectionModel().selectedRows())
        )

    def install_selected_app(self) -> None:
        rows = self.apps_table.selectionModel().selectedRows()
        if not rows:
            return
        package = self._apps_results[rows[0].row()]
        self._start(
            lambda: install_package(self.device, self.credentials, package.name),
            lambda result: self.apps_output.setPlainText(str(result)),
            f"Установка «{package.name}»…",
        )
```

`PackageResult` нужно импортировать в `control_dialog.py` тоже (добавить в
тот же импорт из `ven4control.remote_control`, что и `search_packages`/
`install_package`).

`QLineEdit` — проверено: в текущем блоке импортов `PySide6.QtWidgets` этого
файла его нет (`QAbstractItemView`/`QHeaderView` уже есть, `QLineEdit` —
нет) — добавить в тот же блок, сохраняя алфавитный порядок остальных имён.

- [ ] **Step 3: Подключить страницу в навигацию**

Найти в `__init__` список `page_titles` (заведён в фазе 3, `docs/superpowers/plans/2026-09-08-device-dialog-sidebar.md`) — добавить новую запись **после** `"Обслуживание"` (последний пункт списка):

```python
            ("Приложения", self._create_apps_tab()),
```

(Порядок в списке — порядок кнопок сверху вниз в навигации; «Приложения» — новый последний пункт.)

- [ ] **Step 4: Прогнать весь набор тестов**

Run: `.venv\Scripts\python.exe -m pytest -q`
Expected: без изменений в числе относительно конца Task 2 — эта задача не добавляет юнит-тестируемых чистых функций, только UI-раскладку.

- [ ] **Step 5: Живая проверка (обязательный шаг)**

Run: `.venv\Scripts\python.exe -m ven4control.app`, открыть «Управление устройством» у домашнего роутера, перейти на «Приложения».

Проверить глазами:
- Строка поиска + кнопка «Найти» + пустая таблица «Имя»/«Описание» при открытии.
- Ввести `sftp`, нажать Enter (не только кликом по «Найти» — проверить `returnPressed`) — таблица заполняется реальными результатами с устройства (должен быть виден `openssh-sftp-server` среди них).
- Выбрать строку — кнопка «Установить выбранный» становится активной; без выбора — неактивна.
- **Установку на реальном устройстве в этой проверке не делать** (оставить кнопку неактивной / не нажимать) — устанавливать пакет на боевой роутер без явной причины не нужно; факта, что кнопка доходит до готового к отправке состояния, достаточно для проверки раскладки. Если хочется убедиться, что установка реально работает — спросить пользователя, на каком устройстве можно поставить безобидный пакет для проверки, не решать самостоятельно.
- Смена темы через главное окно — страница «Приложения» и её таблица перекрашиваются вместе с остальным (уже общий QSS, ничего нового стилизовать не нужно — проверить, что не сломалось).

Закрыть приложение после проверки.

- [ ] **Step 6: Commit**

```bash
git add src/ven4control/control_dialog.py
git commit -m "Добавлена страница «Приложения»: поиск и установка пакетов"
```

---

## Self-Review (выполнено при написании плана)

1. **Покрытие спеки**: разделы «Команды», «UI» → Task 1 (парсинг) + Task 2 (сеть) + Task 3 (страница). «Явно вне рамок» (Windows/Ven4Tools) — не тронуто нигде в плане, проверено отсутствием этих слов в коде задач.
2. **Плейсхолдеров нет.**
3. **Согласованность**: `PackageResult(name, description)` — одни и те же поля в определении (Task 1) и использовании (Task 2 парсеры, Task 3 UI). `search_packages`/`install_package` сигнатуры одинаковы в определении и в вызовах из `control_dialog.py`.
4. **Реальные данные, не выдуманные**: все примеры в тестах Task 1 и комментарии о живом поведении — из фактических SSH-сессий на `192.168.1.1` и `138.16.152.133`, зафиксированных при подготовке плана, включая нетривиальный случай (`gvfs-backends` с `" - "` внутри описания).
5. **Безопасность — explicit, не забыто постфактум**: `shlex.quote()` на обоих местах интерполяции пользовательских/устройство-полученных строк в shell-команду, отдельные тесты именно на экранирование (`test_search_term_is_shell_escaped`, `test_package_name_is_shell_escaped`), не только на happy path парсинга.
6. **Живые проверки обязательны в Task 2 и Task 3** — план явно запрещает реальную установку без отдельного разрешения пользователя (это не разрушительное действие в смысле подтверждения UI, но и не то, что стоит делать самовольно на боевом устройстве ради проверки раскладки).
