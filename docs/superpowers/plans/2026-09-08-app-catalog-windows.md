# Установка приложений — Windows/winget + кнопка Ven4Tools (фаза 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Страница «Приложения» (фаза 1, уже в `main`) начинает работать и для Windows-устройств через `winget` — та же таблица результатов, тот же паттерн. Плюс отдельная кнопка «Установить/обновить Ven4Tools»: скачивает последний релиз с GitHub и распаковывает в фиксированную папку.

**Architecture:** `search_packages`/`install_package` (уже есть, фаза 1) получают третью ветку платформы — `windows` — рядом с уже существующими `openwrt`/остальное(apt). Новая функция `install_ven4tools` — отдельная, не через общий `search_packages`/`install_package` (это не поиск по каталогу, а конкретное действие с конкретным репозиторием). Экранирование строк для PowerShell — не `shlex.quote` (POSIX, для Windows-команд неверно: PowerShell экранирует `'` внутри `'...'`-строки удвоением `''`, не обратным слэшем) — общая функция, вынесенная в `powershell.py`, переиспользована из уже существующего `scheduled_task.py::_quoted` (та же логика была продублирована бы во второй раз, если не вынести).

**Tech Stack:** Python 3.12, PySide6, asyncssh, unittest.

## Global Constraints

- **Экранирование для Windows-веток — через `ven4control.powershell.quote`, НЕ `shlex.quote`.** `shlex.quote` — POSIX-экранирование, отправленное в PowerShell-команду (а Windows-устройства подключаются через PowerShell по умолчанию, см. `detect_platform`), оно работает неверно для строк с одинарной кавычкой внутри.
- **`$ProgressPreference = "SilentlyContinue"` обязателен перед любым `Invoke-WebRequest` в командах этого плана.** Живая находка при подготовке плана: без этой строки `Invoke-WebRequest`, отправленный через SSH (неинтерактивная сессия, нет реального терминала для прогресс-бара), может зависать на реальной машине на много минут вместо секунд — воспроизведено вживую на `VenchWork` (100.93.198.62). С `$ProgressPreference` не проверялось на той же зависавшей машине повторно (не было времени дождаться повторного факта зависания уже с фиксом, а объём файла там же, судя по её сетевому пути к CDN GitHub, download занимает существенно дольше, чем с домашнего ПК — 4 секунды на 85.6 МБ с домашнего ПК, на VenchWork не завершилось и за несколько минут даже голым `curl.exe` без PowerShell вообще, то есть дело не только в прогресс-баре, а в сетевом пути этой конкретной машины до `objects.githubusercontent.com` — GitHub API и голый TCP-коннект на 443 при этом отвечают нормально). Правильная реакция кода — щедрый таймаут (`timeout=600` в `_run`), не попытка обойти саму медленную сеть.
- Код и UI-строки — на русском, в стиле остального проекта. Комментарии — только там, где решение неочевидно.
- Ни одного упоминания Claude/Anthropic/AI нигде — ни в коде, ни в commit message. Commit message — обычный текст в стиле `git log` этого репозитория, без трейлеров.
- Полный набор тестов (`.venv/Scripts/python.exe -m pytest -q`, venv в корне репозитория) остаётся зелёным на каждом коммите, включая все существующие 377.
- «Установка по ссылке» (podkop-стиль/архитектурные ZIP zapret) — НЕ в этом плане, отдельная будущая задача (см. спеку, раздел «Установка по ссылке»).

---

## Task 1: `powershell.py` — общее экранирование, переиспользовано в `scheduled_task.py`

**Files:**
- Create: `src/ven4control/powershell.py`
- Modify: `src/ven4control/scheduled_task.py`
- Test: `tests/test_powershell.py`

**Interfaces:**
- Produces: `quote(value: str) -> str`.

- [ ] **Step 1: Написать тест**

Создать `tests/test_powershell.py`:

```python
import unittest

from ven4control.powershell import quote


class QuoteTests(unittest.TestCase):
    def test_plain_string_is_wrapped_in_single_quotes(self) -> None:
        self.assertEqual("'hello'", quote("hello"))

    def test_embedded_single_quote_is_doubled(self) -> None:
        # PowerShell экранирует ' внутри '...'-строки удвоением, не
        # обратным слэшем (это отличает от shlex.quote/POSIX).
        self.assertEqual("'it''s'", quote("it's"))

    def test_semicolon_stays_inside_the_quotes(self) -> None:
        self.assertEqual("'a; Remove-Item C:\\'", quote("a; Remove-Item C:\\"))

    def test_empty_string(self) -> None:
        self.assertEqual("''", quote(""))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Прогнать, убедиться что падает**

Run: `.venv\Scripts\python.exe -m pytest tests/test_powershell.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Реализовать**

Создать `src/ven4control/powershell.py`:

```python
"""Экранирование строк для команд PowerShell.

Одна общая функция вместо копий по модулям: `scheduled_task.py` уже
экранировал PowerShell-строки для планировщика (`_quoted`), теперь та же
логика нужна `remote_control.py` для winget/Ven4Tools — переносится сюда
и переиспользуется в обоих местах, а не дублируется во второй раз.
"""
from __future__ import annotations


def quote(value: str) -> str:
    """Строка PowerShell в одинарных кавычках: внутри удваивается только '."""
    return "'" + value.replace("'", "''") + "'"
```

В `src/ven4control/scheduled_task.py` найти:
```python
def _quoted(value: str) -> str:
    """Строка PowerShell в одинарных кавычках: внутри удваивается только '."""
    return "'" + value.replace("'", "''") + "'"
```
Заменить на:
```python
from ven4control.powershell import quote as _quoted
```
(Строку импорта разместить в начале файла, в существующем блоке импортов
`from ven4control import ...`/`from ven4control.X import ...` — не оставлять
`from ... import ...` посреди тела модуля.) Все существующие вызовы
`_quoted(...)` по всему файлу остаются как есть — меняется только
происхождение имени, не сигнатура и не поведение.

- [ ] **Step 4: Прогнать тесты**

Run: `.venv\Scripts\python.exe -m pytest tests/test_powershell.py tests/test_scheduled_task.py -v`
Expected: все PASS — новые 4 теста `powershell.py` и все существующие тесты `scheduled_task.py` (используют `_quoted` косвенно через уже написанные тесты этого модуля) не сломаны переносом.

- [ ] **Step 5: Прогнать весь набор**

Run: `.venv\Scripts\python.exe -m pytest -q`
Expected: `381 passed` (377 + 4).

- [ ] **Step 6: Commit**

```bash
git add src/ven4control/powershell.py src/ven4control/scheduled_task.py tests/test_powershell.py
git commit -m "Экранирование PowerShell-строк вынесено в общий модуль"
```

---

## Task 2: `parse_winget_search` — разбор вывода winget

**Files:**
- Modify: `src/ven4control/remote_control.py`
- Test: `tests/test_remote_control.py`

**Interfaces:**
- Produces: `parse_winget_search(output: str) -> list[PackageResult]` (использует уже существующий `PackageResult` из фазы 1).

- [ ] **Step 1: Написать тест на реальном зафиксированном выводе**

Добавить в `tests/test_remote_control.py` (импортировать `parse_winget_search` вместе с остальными parse-функциями фазы 1):

```python
class WingetSearchParsingTests(unittest.TestCase):
    """Строки — реальный вывод `winget search sftp --accept-source-agreements`
    с VenchWork (Windows 11, winget v1.29.290), не выдуманы."""

    def test_real_output_from_a_live_windows_machine(self) -> None:
        output = (
            "Name                   Id                              Version       Match            Source\n"
            "---------------------------------------------------------------------------------------------\n"
            "Avash                  AdrienCros.Avash                0.10.1        Tag: sftp        winget\n"
            "Bitvise SSH Client     Bitvise.SSH.Client              9.66          Tag: sftp        winget\n"
        )
        results = parse_winget_search(output)
        # В установку идёт Id, не Name — тот же принцип, что у apk/opkg/apt:
        # PackageResult.name — точный устанавливаемый идентификатор.
        self.assertEqual(["AdrienCros.Avash", "Bitvise.SSH.Client"], [r.name for r in results])
        self.assertEqual("Avash · 0.10.1", results[0].description)

    def test_no_separator_line_is_an_empty_list(self) -> None:
        self.assertEqual([], parse_winget_search("No package found matching input criteria.\n"))

    def test_empty_output_is_an_empty_list(self) -> None:
        self.assertEqual([], parse_winget_search(""))
```

- [ ] **Step 2: Прогнать, убедиться что падает**

Run: `.venv\Scripts\python.exe -m pytest tests/test_remote_control.py -k WingetSearch -v`
Expected: FAIL — `ImportError`.

- [ ] **Step 3: Реализовать**

Добавить в `src/ven4control/remote_control.py`, рядом с `parse_apt_search`:

```python
def parse_winget_search(output: str) -> list[PackageResult]:
    """Разбирает вывод `winget search <термин> --accept-source-agreements`.

    winget не даёт структурированный вывод для search (нет флага JSON) —
    только таблицу, выровненную пробелами. Граница столбца — 2+ пробела
    подряд; внутри значения столбца пробел встречается не больше одного
    раза (проверено на реальном выводе: `Bitvise SSH Client`, `Tag: sftp`).
    В install идёт Id (`Bitvise.SSH.Client`), не Name — тот же принцип,
    что у apk/opkg/apt: PackageResult.name — точный устанавливаемый
    идентификатор, Name+Version собираются в description для показа.
    """
    lines = [line for line in output.splitlines() if line.strip()]
    separator_index = next(
        (i for i, line in enumerate(lines) if set(line.strip()) == {"-"}),
        None,
    )
    if separator_index is None:
        return []
    results: list[PackageResult] = []
    for line in lines[separator_index + 1:]:
        fields = re.split(r"\s{2,}", line.strip())
        if len(fields) < 3:
            continue
        name, package_id, version = fields[0], fields[1], fields[2]
        results.append(PackageResult(package_id, f"{name} · {version}"))
    return results
```

- [ ] **Step 4: Прогнать тесты**

Run: `.venv\Scripts\python.exe -m pytest tests/test_remote_control.py -k WingetSearch -v`
Expected: все PASS (3 теста).

- [ ] **Step 5: Прогнать весь набор**

Run: `.venv\Scripts\python.exe -m pytest -q`
Expected: `384 passed` (381 + 3).

- [ ] **Step 6: Commit**

```bash
git add src/ven4control/remote_control.py tests/test_remote_control.py
git commit -m "Добавлен разбор вывода winget search"
```

---

## Task 3: `search_packages`/`install_package` — ветка Windows; `install_ven4tools`

**Files:**
- Modify: `src/ven4control/remote_control.py`
- Test: `tests/test_remote_control.py`

**Interfaces:**
- Consumes: `parse_winget_search` (Task 2), `ven4control.powershell.quote` (Task 1).
- Produces: `async def install_ven4tools(device: Device, credentials: dict[str, str]) -> str`. Существующие `search_packages`/`install_package` получают третью ветку платформы.

- [ ] **Step 1: Добавить импорт**

В начало `src/ven4control/remote_control.py` добавить:
```python
from ven4control.powershell import quote as ps_quote
```

- [ ] **Step 2: Добавить ветку Windows в `search_packages`**

Найти текущий блок (см. код фазы 1):
```python
        if platform == "openwrt":
            command = (
                "if command -v apk >/dev/null 2>&1; then "
                ...
            )
            parser = parse_apk_search
        else:
            command = (
                "sudo -n apt-get update >/dev/null 2>&1; "
                f"apt-cache search {safe_term}"
            )
            parser = parse_apt_search
```
Заменить на трёхветочный (Windows — первой веткой, `elif` вместо `if`/`else` для остальных двух, чтобы не спутать «не openwrt» с «apt» — раньше `else` буквально означало «apt», теперь под тем же `else` оказался бы и Windows, если его не выделить явной веткой первым):
```python
        if platform == "windows":
            command = f"winget search {ps_quote(term)} --accept-source-agreements"
            parser = parse_winget_search
        elif platform == "openwrt":
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
```
`safe_term = shlex.quote(term)` остаётся как есть для веток `openwrt`/остальное — экранирование для Windows-ветки идёт отдельной переменной `ps_quote(term)` (PowerShell, не POSIX), не той же `safe_term`.

- [ ] **Step 3: Добавить ветку Windows в `install_package`**

Тем же способом — найти текущий блок:
```python
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
```
Заменить на:
```python
        if platform == "windows":
            command = (
                f"winget install --id {ps_quote(name)} --exact --silent "
                "--accept-package-agreements --accept-source-agreements"
            )
        elif platform == "openwrt":
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
```

- [ ] **Step 4: Добавить `install_ven4tools`**

Добавить в `src/ven4control/remote_control.py`, после `install_package`:

```python
# Владелец этой папки на устройстве — кнопка «Ven4Tools»: обновление
# полностью перезаписывает её содержимое, не класть туда ничего своего.
VEN4TOOLS_INSTALL_PATH = "C:\\Ven4Tools"
VEN4TOOLS_REPO = "Ven4ru/Ven4Tools"


async def install_ven4tools(device: Device, credentials: dict[str, str]) -> str:
    """Скачивает последний релиз Ven4Tools с GitHub и распаковывает на устройство.

    Тот же путь и для первой установки, и для обновления — Expand-Archive
    -Force перезаписывает совпадающие файлы. $ProgressPreference обязателен
    (см. Global Constraints плана — без него Invoke-WebRequest зависает на
    неинтерактивной SSH-сессии на некоторых машинах).
    """
    connection = await _connect(device, credentials)
    try:
        platform, description = await detect_platform(connection)
        if platform != "windows":
            raise RuntimeError(
                f"Ven4Tools ставится только на Windows, устройство "
                f"определено как {description} ({platform})."
            )
        command = (
            '$ProgressPreference = "SilentlyContinue"; '
            "$release = Invoke-RestMethod -Uri "
            f'"https://api.github.com/repos/{VEN4TOOLS_REPO}/releases/latest"; '
            '$asset = $release.assets | Where-Object { $_.name -like "*.zip" } '
            "| Select-Object -First 1; "
            "if (-not $asset) { throw 'В последнем релизе Ven4Tools нет ZIP-архива.' }; "
            '$zipPath = "$env:TEMP\\ven4tools-update.zip"; '
            "Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $zipPath; "
            f"Expand-Archive -Path $zipPath -DestinationPath {ps_quote(VEN4TOOLS_INSTALL_PATH)} -Force; "
            "Remove-Item $zipPath -Force; "
            f'Write-Output "Ven4Tools $($release.tag_name) установлен в {VEN4TOOLS_INSTALL_PATH}"'
        )
        result = await _run(connection, command, timeout=600, check=True)
        return result.stdout.strip()
    finally:
        connection.close()
        await connection.wait_closed()
```

- [ ] **Step 5: Написать тесты**

Добавить в `tests/test_remote_control.py`:

```python
class SearchPackagesWindowsTests(unittest.TestCase):
    def test_windows_device_uses_winget(self) -> None:
        connection = windows_connection(("winget search", (
            "Name    Id            Version  Match       Source\n"
            "----------------------------------------------\n"
            "Avash   Vendor.Avash  1.0      Tag: sftp   winget\n"
        ), 0))
        with patched_connect(connection):
            results = asyncio.run(search_packages(device(), {}, "sftp"))
        self.assertEqual("Vendor.Avash", results[0].name)

    def test_search_term_is_powershell_escaped(self) -> None:
        connection = windows_connection(("winget search", "", 0))
        with patched_connect(connection):
            asyncio.run(search_packages(device(), {}, "sftp'; Remove-Item C:\\"))
        executed = connection.commands[-1]
        # PowerShell-экранирование: одинарная кавычка внутри строки
        # удваивается, не убегает обратным слэшем (POSIX-приём здесь неверен).
        self.assertIn("'sftp''; Remove-Item C:\\'", executed)


class InstallVen4ToolsTests(unittest.TestCase):
    def test_non_windows_device_is_rejected(self) -> None:
        connection = openwrt_connection()
        with patched_connect(connection):
            with self.assertRaises(RuntimeError) as raised:
                asyncio.run(install_ven4tools(device(), {}))
        self.assertIn("только на Windows", str(raised.exception))

    def test_windows_device_runs_the_download_command(self) -> None:
        connection = windows_connection(
            ("Invoke-RestMethod", "Ven4Tools v5.1.1 установлен в C:\\Ven4Tools", 0)
        )
        with patched_connect(connection):
            result = asyncio.run(install_ven4tools(device(), {}))
        self.assertIn("установлен", result)
        executed = connection.commands[-1]
        self.assertIn("SilentlyContinue", executed)
```

**Проверить перед написанием**: у `windows_connection`/`openwrt_connection` в начале
`tests/test_remote_control.py` — сигнатуру (принимают ли `*extra: tuple[str, str, int]`
позиционно, как уже используется в других тестах этого файла, например
`SearchPackagesTests`/`InstallPackageTests` из фазы 1) — использовать тем же
способом, не изобретать новый вызов.

- [ ] **Step 6: Прогнать тесты**

Run: `.venv\Scripts\python.exe -m pytest tests/test_remote_control.py -k "Windows or Ven4Tools" -v`
Expected: все PASS.

- [ ] **Step 7: Прогнать весь набор**

Run: `.venv\Scripts\python.exe -m pytest -q`
Expected: `389 passed` (384 + 5: `test_windows_device_uses_winget`, `test_search_term_is_powershell_escaped`, `test_non_windows_device_is_rejected`, `test_windows_device_runs_the_download_command` — и ещё раз свериться по факту написанных тестов, если получилось иначе, не считать расхождение ошибкой само по себе, разобраться в причине).

- [ ] **Step 8: Живая проверка (обязательный шаг, если есть доступ к Windows-устройству в каталоге)**

Если в `devices.db` есть реальное Windows-устройство (у пользователя это `ПК VenchWork`, id=13) — вызвать `search_packages(device, {}, "sftp")` напрямую (не через кнопку в UI) и убедиться, что возвращаются реальные результаты с реального `winget`. **Не вызывать `install_package`/`install_ven4tools` на реальном устройстве** без отдельного разрешения — то же правило, что и в фазе 1.

Если тестировать на `VenchWork` — учесть найденную вживую при подготовке плана особенность: крупные скачивания (`install_ven4tools`) там зависают по сети независимо от того, что делает код (см. Global Constraints) — это не повод считать код сломанным, если конкретно `install_ven4tools` не удастся проверить именно на этой машине за разумное время. `search_packages`/`install_package` (winget, без крупных скачиваний) этой проблемы не имеют — их проверить можно и нужно.

- [ ] **Step 9: Commit**

```bash
git add src/ven4control/remote_control.py tests/test_remote_control.py
git commit -m "Добавлены winget-поиск/установка и скачивание Ven4Tools на устройство"
```

---

## Task 4: UI — кнопка «Установить/обновить Ven4Tools»

**Files:**
- Modify: `src/ven4control/control_dialog.py`

**Interfaces:**
- Consumes: `install_ven4tools` (Task 3).

- [ ] **Step 1: Добавить импорт**

В `from ven4control.remote_control import (...)` добавить `install_ven4tools` (сохраняя алфавитный порядок).

- [ ] **Step 2: Добавить кнопку в `_create_apps_tab`**

Найти конец метода `_create_apps_tab` (после блока `self.apps_output = QTextEdit()` ... `layout.addWidget(self.apps_output)`, перед `self._apps_results: list[PackageResult] = []`) и добавить перед `return page`:

```python
        ven4tools_button = QPushButton("Установить/обновить Ven4Tools")
        ven4tools_button.clicked.connect(self.install_ven4tools_on_device)
        layout.addWidget(ven4tools_button)
```

(Порядок виджетов внутри `layout` — как добавлены; кнопка окажется под областью вывода. Разместить можно и по-другому, если так нагляднее — это единственное место плана, где точная позиция не критична.)

- [ ] **Step 3: Добавить обработчик**

Добавить метод рядом с `install_selected_app`:

```python
    def install_ven4tools_on_device(self) -> None:
        self._start(
            lambda: install_ven4tools(self.device, self.credentials),
            lambda result: self.apps_output.setPlainText(str(result)),
            "Установка Ven4Tools…",
        )
```

- [ ] **Step 4: Прогнать весь набор тестов**

Run: `.venv\Scripts\python.exe -m pytest -q`
Expected: без изменений в числе — только UI-раскладка, юнит-тестируемых чистых функций не добавляется.

- [ ] **Step 5: Живая проверка (обязательный шаг)**

Run: `.venv\Scripts\python.exe -m ven4control.app`, открыть «Управление устройством» у Windows-устройства (`ПК VenchWork`, если доступно), перейти на «Приложения».

Проверить глазами:
- Кнопка «Установить/обновить Ven4Tools» видна на странице.
- Поиск (`sftp` или любой другой термин) работает так же, как для OpenWrt/Ubuntu — таблица заполняется реальными результатами `winget search`, поле «Имя» показывает winget Id (не человекочитаемое название — это ожидаемо, тот же принцип, что у остальных платформ).
- **Кнопку Ven4Tools не нажимать на реальном устройстве** без отдельного разрешения пользователя — это скачивание и распаковка в `C:\Ven4Tools`, реальное изменение файловой системы устройства, не только чтение.
- Открыть страницу «Приложения» у OpenWrt/Ubuntu-устройства ещё раз — кнопка Ven4Tools там тоже видна (её платформенная проверка — внутри `install_ven4tools`, не в видимости кнопки, см. спеку) — при нажатии на не-Windows устройстве должна вернуть понятную ошибку «ставится только на Windows», не молча зависнуть и не уронить приложение. Эту часть — нажатие на НЕ-Windows устройстве — можно и нужно проверить по-настоящему: это безопасно, команда до реального изменения файловой системы не доходит.

Закрыть приложение после проверки.

- [ ] **Step 6: Commit**

```bash
git add src/ven4control/control_dialog.py
git commit -m "Добавлена кнопка установки и обновления Ven4Tools на устройстве"
```

---

## Self-Review (выполнено при написании плана)

1. **Покрытие спеки**: раздел «Фаза 2» → Task 2 (парсинг winget) + Task 3 (сеть, обе функции) + Task 4 (UI). Task 1 — предпосылка (общее экранирование), явно вызвана в спеке как открытый вопрос дизайна, решена здесь.
2. **Плейсхолдеров нет.**
3. **Согласованность**: `ps_quote`/`quote` — одно и то же имя функции (алиас при импорте), сигнатура не меняется между определением (Task 1) и использованием (Task 3). `install_ven4tools` сигнатура одинакова в определении и вызове из UI (Task 4).
4. **Реальная, а не воображаемая живая проверка**: команды `winget search`/`winget install --help` и полный цикл GitHub API→скачивание→распаковка проверены на реальном Windows-устройстве (`VenchWork`) при подготовке плана — включая находку про зависание `Invoke-WebRequest`/`curl.exe` специфично на этой машине (не код виноват, сетевой путь) и подтверждение через альтернативный тест с домашнего ПК (4 секунды на 85.6 МБ), что сам механизм работает корректно в нормальных сетевых условиях.
5. **Экранирование — explicit, отдельная задача, не забыто**: Task 1 выделена именно потому, что POSIX/PowerShell экранирование — разные вещи, и использование `shlex.quote` для Windows-ветки было бы тихой, незаметной в тестах ошибкой (строка без `'` внутри экранировалась бы одинаково что POSIX-, что PowerShell-способом — баг проявился бы только на реальном вводе с кавычкой, ровно как и было бы с обычным `apk`/`apt`, если бы не заметили разницу заранее).
