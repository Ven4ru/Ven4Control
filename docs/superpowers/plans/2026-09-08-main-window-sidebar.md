# Сайдбар главного окна (фаза 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Заменить тулбар + панель действий главного окна на сайдбар в стиле Ven4Tools (логотип с акцентной планкой, сгруппированные кнопки под заголовками разделов, статус-пилюля, кнопка «Настройки») и контентную область с шапкой — без изменения логики выбора/массовых операций/таблицы.

**Architecture:** Виджеты и их сигналы/слоты не меняются — меняется только контейнер, в котором они живут (`QVBoxLayout` сайдбара вместо `action_panel`/`QToolBar`). Новые QSS-селекторы (`#sidebar`, `#contentHeader`, `#brandStrip`) добавляются в `theme.py::stylesheet_for` по той же схеме, что уже есть там (`#accent`, `[nav="true"]`), а не инлайновым `setStyleSheet` на конкретных виджетах — инлайн не переживает смену темы (см. `docs/superpowers/plans/2026-09-08-theme-system.md`, Task 4, про ту же ошибку с `DEFAULT_BACKGROUND`).

**Tech Stack:** Python 3.12, PySide6, unittest.

## Global Constraints

- **Инвариант `row == index` в `self.devices` не трогается.** `_row_of`, `selected_device`, `checked_device_ids`, `reload()` не меняются по существу — эти методы работают с `self.table`/`self.devices` напрямую, не с контейнером, в котором таблица отображается, поэтому перенос виджетов в новый контейнер их не касается.
- Виджеты, чьи ссылки читает `_update_selection`/`_update_bulk_actions`/`_set_status`/`_session_status_changed`/`_tunnel_status_changed`/`_tunnel_closed` (см. `src/ven4control/app.py:858-660,780-792,501-539`), остаются теми же объектами с теми же именами атрибутов (`self.terminal_button`, `self.console_button`, `self.control_button`, `self.logging_button`, `self.rdp_check_button`, `self.rdp_enable_button`, `self.rdp_button`, `self.group_button`, `self.forget_button`, `self.delete_button`, `self.selected_label`, `self.bulk_label`, `self.bulk_reboot_button`, `self.bulk_update_button`) — меняется только `.addWidget(...)` на новый layout, не создание объектов заново и не переименование.
- Тулбар (`QToolBar`, кнопки «Добавить»/«Обновить»/«Импорт Tailscale»/«Установка ключа») убирается целиком — эти действия переезжают в сайдбар как обычные кнопки. `QAction`/`QToolBar` после этого нигде в файле не используются — убрать оба импорта.
- Код и UI-строки — на русском, в стиле остального проекта. Комментарии — только там, где решение неочевидно.
- Ни одного упоминания Claude/Anthropic/AI нигде — ни в коде, ни в commit message. Commit message — обычный текст в стиле `git log` этого репозитория, без трейлеров.
- Полный набор тестов (`.venv/Scripts/python.exe -m pytest -q`, venv в корне репозитория) остаётся зелёным на каждом коммите, включая все существующие 356.
- В этом проекте тесты не создают настоящий `QApplication`/виджеты (см. Global Constraints плана фазы 1) — новая раскладка проверяется живым запуском (обязательный шаг, не опциональный), не юнитами. Юнитами покрывается только чистая логика, которую можно вынести из обработчиков (в этой фазе — `online_summary`).

---

## Task 1: `online_summary` — текст статус-пилюли

**Files:**
- Modify: `src/ven4control/app.py` (добавить функцию на уровне модуля, рядом с `bulk_report`/`_single_line` — теми же соседями, что и остальные чистые функции этого файла)
- Test: `tests/test_app.py`

**Interfaces:**
- Produces: `online_summary(online: int, total: int) -> str`.

- [ ] **Step 1: Написать тест**

Добавить в `tests/test_app.py` (рядом с существующим импортом `bulk_report` и т.п. — добавить `online_summary` в тот же импорт из `ven4control.app`):

```python
class OnlineSummaryTests(unittest.TestCase):
    def test_no_devices_at_all(self) -> None:
        self.assertEqual("Устройств нет", online_summary(0, 0))

    def test_all_online(self) -> None:
        self.assertEqual("Онлайн: 3 из 3", online_summary(3, 3))

    def test_some_offline(self) -> None:
        self.assertEqual("Онлайн: 1 из 3", online_summary(1, 3))

    def test_none_online_yet(self) -> None:
        # Проверки ещё не пришли (или все офлайн) — 0 из total, не «нет устройств».
        self.assertEqual("Онлайн: 0 из 3", online_summary(0, 3))
```

- [ ] **Step 2: Прогнать тест, убедиться что падает**

Run: `.venv\Scripts\python.exe -m pytest tests/test_app.py::OnlineSummaryTests -v`
Expected: FAIL — `ImportError: cannot import name 'online_summary'`.

- [ ] **Step 3: Реализовать**

Добавить в `src/ven4control/app.py` рядом с `bulk_report`/`_single_line` (после `_single_line`, перед `terminal_command` — там сейчас граница между «чистыми функциями отчётов» и остальным кодом файла):

```python
def online_summary(online: int, total: int) -> str:
    """Текст статус-пилюли сайдбара: сколько устройств сейчас в сети."""
    if not total:
        return "Устройств нет"
    return f"Онлайн: {online} из {total}"
```

- [ ] **Step 4: Прогнать тест**

Run: `.venv\Scripts\python.exe -m pytest tests/test_app.py::OnlineSummaryTests -v`
Expected: PASS (4 теста).

- [ ] **Step 5: Прогнать весь набор**

Run: `.venv\Scripts\python.exe -m pytest -q`
Expected: `360 passed` (356 + 4).

- [ ] **Step 6: Commit**

```bash
git add src/ven4control/app.py tests/test_app.py
git commit -m "Добавлена сборка текста статус-пилюли сайдбара"
```

---

## Task 2: QSS-селекторы сайдбара в `theme.py`

**Files:**
- Modify: `src/ven4control/theme.py`
- Modify: `tests/test_theme.py`

**Interfaces:**
- Consumes: ничего нового — расширяет уже существующую `stylesheet_for`.
- Produces: селекторы `QWidget#sidebar`, `QWidget#contentHeader`, `QFrame#brandStrip` в QSS, доступные Task 3 через `objectName`.

- [ ] **Step 1: Написать тест**

Добавить в конец `tests/test_theme.py` (класс `StylesheetTests` уже существует — добавить туда, не создавать новый класс):

```python
    def test_stylesheet_styles_the_sidebar_container(self) -> None:
        css = stylesheet_for(DEFAULT_THEME)
        self.assertIn("#sidebar", css)
        self.assertIn("#contentHeader", css)
        self.assertIn("#brandStrip", css)
```

- [ ] **Step 2: Прогнать, убедиться что падает**

Run: `.venv\Scripts\python.exe -m pytest tests/test_theme.py::StylesheetTests::test_stylesheet_styles_the_sidebar_container -v`
Expected: FAIL.

- [ ] **Step 3: Добавить селекторы в `stylesheet_for`**

В `src/ven4control/theme.py`, внутри f-строки `stylesheet_for`, добавить сразу после блока `QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}` (последний блок перед закрывающим `""".strip()`):

```python

QWidget#sidebar {{
    background-color: {c['sidebar_background']};
    border-right: 1px solid {c['border_color']};
}}

QWidget#contentHeader {{
    background-color: {c['sidebar_background']};
    border-bottom: 1px solid {c['border_color']};
}}

QFrame#brandStrip {{
    background-color: {c['accent_color']};
    border-radius: 1px;
}}
```

- [ ] **Step 4: Прогнать тест**

Run: `.venv\Scripts\python.exe -m pytest tests/test_theme.py -v`
Expected: все тесты PASS (15 — было 14 + 1 новый).

- [ ] **Step 5: Прогнать весь набор**

Run: `.venv\Scripts\python.exe -m pytest -q`
Expected: `361 passed` (360 + 1).

- [ ] **Step 6: Commit**

```bash
git add src/ven4control/theme.py tests/test_theme.py
git commit -m "Добавлены QSS-селекторы сайдбара и шапки контента"
```

---

## Task 3: Пересборка `MainWindow.__init__` — сайдбар и контентная область

**Files:**
- Modify: `src/ven4control/app.py`

**Interfaces:**
- Consumes: `online_summary` (Task 1), `#sidebar`/`#contentHeader`/`#brandStrip` (Task 2), `ven4control.dialogs.SettingsDialog` (уже существует, фаза 1), `ven4control.settings.load_settings` (уже импортирован в файле).
- Produces: `MainWindow.open_settings() -> None`, `MainWindow._section_label(text: str) -> QLabel`, `MainWindow._update_status_pill() -> None`. Все существующие имена атрибутов (`self.terminal_button` и т.д. — полный список в Global Constraints) сохраняются без изменений.

- [ ] **Step 1: Обновить импорты**

В `src/ven4control/app.py`, строка с `from PySide6.QtGui import QAction, QCloseEvent, QIcon` — убрать `QAction` (использовался только в убираемом тулбаре):
```python
from PySide6.QtGui import QCloseEvent, QIcon
```

Строка с `from PySide6.QtWidgets import (...)` — убрать `QToolBar` (тулбар убирается целиком), добавить `QFrame`:
```python
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QHeaderView, QInputDialog, QLabel,
    QMainWindow, QMenu, QMessageBox, QPushButton, QStyle, QSystemTrayIcon,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)
```

Строка `from ven4control.dialogs import AddDeviceDialog, InstructionsDialog` — добавить `SettingsDialog`:
```python
from ven4control.dialogs import AddDeviceDialog, InstructionsDialog, SettingsDialog
```

- [ ] **Step 2: Заменить блок от `action_panel = QWidget()` до `self.setCentralWidget(container)`**

Это строки `289`-`363` в текущем файле (проверить актуальные номера строк перед правкой — `grep -n "action_panel = QWidget()" src/ven4control/app.py` и `grep -n "self.setCentralWidget(container)" src/ven4control/app.py`, между ними — заменяемый диапазон целиком, включая блок тулбара и блок `container`). Заменить на:

```python
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(230)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(14, 14, 14, 10)
        sidebar_layout.setSpacing(6)

        brand_row = QHBoxLayout()
        brand_strip = QFrame()
        brand_strip.setObjectName("brandStrip")
        brand_strip.setFixedSize(3, 36)
        brand_row.addWidget(brand_strip)
        brand_title = QLabel("Ven4Control")
        brand_title.setStyleSheet("font-size: 14pt; font-weight: 700;")
        brand_row.addWidget(brand_title)
        brand_row.addStretch()
        sidebar_layout.addLayout(brand_row)
        sidebar_layout.addSpacing(10)

        sidebar_layout.addWidget(self._section_label("УСТРОЙСТВА"))
        add_button = QPushButton("Добавить")
        add_button.clicked.connect(self.add_device)
        refresh_button = QPushButton("Обновить")
        refresh_button.clicked.connect(self.refresh_statuses)
        tailscale_button = QPushButton("Импорт Tailscale")
        tailscale_button.clicked.connect(self.import_tailscale)
        sidebar_layout.addWidget(add_button)
        sidebar_layout.addWidget(refresh_button)
        sidebar_layout.addWidget(tailscale_button)

        sidebar_layout.addSpacing(10)
        sidebar_layout.addWidget(self._section_label("ВЫБРАННОЕ УСТРОЙСТВО"))
        self.selected_label = QLabel("Устройство не выбрано")
        self.selected_label.setWordWrap(True)
        sidebar_layout.addWidget(self.selected_label)
        self.terminal_button = QPushButton("Открыть терминал")
        self.terminal_button.clicked.connect(self.open_selected_terminal)
        self.console_button = QPushButton("Встроенный терминал")
        self.console_button.clicked.connect(self.open_selected_console)
        self.control_button = QPushButton("Управление устройством")
        self.control_button.clicked.connect(self.control_selected_device)
        self.logging_button = QPushButton("Логировать в фоне")
        self.logging_button.clicked.connect(self.toggle_selected_logging)
        self.rdp_check_button = QPushButton(rdp_check_label(None))
        self.rdp_check_button.clicked.connect(self.check_selected_rdp)
        self.rdp_enable_button = QPushButton("Включить RDP")
        self.rdp_enable_button.clicked.connect(self.enable_selected_rdp)
        self.rdp_button = QPushButton("🖥️ RDP")
        self.rdp_button.clicked.connect(self.open_selected_rdp)
        self.group_button = QPushButton("Изменить группу")
        self.group_button.clicked.connect(self.edit_selected_group)
        self.forget_button = QPushButton("Удалить сохранённые данные")
        self.forget_button.clicked.connect(self.forget_selected_credentials)
        self.delete_button = QPushButton("Удалить устройство")
        self.delete_button.clicked.connect(self.delete_selected_device)
        sidebar_layout.addWidget(self.control_button)
        sidebar_layout.addWidget(self.logging_button)
        sidebar_layout.addWidget(self.console_button)
        sidebar_layout.addWidget(self.terminal_button)
        sidebar_layout.addWidget(self.rdp_check_button)
        sidebar_layout.addWidget(self.rdp_enable_button)
        sidebar_layout.addWidget(self.rdp_button)
        sidebar_layout.addWidget(self.group_button)
        sidebar_layout.addWidget(self.forget_button)
        sidebar_layout.addWidget(self.delete_button)

        sidebar_layout.addSpacing(10)
        sidebar_layout.addWidget(self._section_label("МАССОВЫЕ ОПЕРАЦИИ"))
        self.bulk_label = QLabel("Ничего не отмечено")
        self.bulk_label.setWordWrap(True)
        sidebar_layout.addWidget(self.bulk_label)
        self.bulk_reboot_button = QPushButton("Перезагрузить выбранные")
        self.bulk_reboot_button.clicked.connect(self.reboot_checked_devices)
        self.bulk_update_button = QPushButton("Обновить пакеты на выбранных")
        self.bulk_update_button.clicked.connect(self.update_checked_devices)
        sidebar_layout.addWidget(self.bulk_reboot_button)
        sidebar_layout.addWidget(self.bulk_update_button)

        sidebar_layout.addStretch()
        self.status_pill = QLabel(online_summary(0, 0))
        self.status_pill.setProperty("secondary", True)
        self.status_pill.setWordWrap(True)
        sidebar_layout.addWidget(self.status_pill)
        instructions_button = QPushButton("Установка ключа")
        instructions_button.clicked.connect(self.show_instructions)
        settings_button = QPushButton("Настройки")
        settings_button.clicked.connect(self.open_settings)
        sidebar_layout.addWidget(instructions_button)
        sidebar_layout.addWidget(settings_button)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        header = QWidget()
        header.setObjectName("contentHeader")
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(18, 12, 18, 12)
        header_title = QLabel("Устройства")
        header_title.setStyleSheet("font-size: 13pt; font-weight: 600;")
        header_layout.addWidget(header_title)
        header_subtitle = QLabel("Управление вашими SSH-устройствами")
        header_subtitle.setProperty("secondary", True)
        header_layout.addWidget(header_subtitle)
        content_layout.addWidget(header)
        content_layout.addWidget(self.table, 1)

        root = QWidget()
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        root_layout.addWidget(sidebar)
        root_layout.addWidget(content, 1)
        self.setCentralWidget(root)
```

**Не переносить дословно** заголовок `QLabel("Ваши устройства")` и `QLabel("Действия")` из старого кода — их смысл теперь несёт шапка контентной области (`header_title`) и заголовок первого раздела сайдбара соответственно, дублировать не нужно.

- [ ] **Step 3: Добавить вспомогательные методы**

Добавить в класс `MainWindow` (рядом с `_create_tray`/`_update_tray` — там уже есть похожие маленькие приватные помощники):

```python
    def _section_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setProperty("eyebrow", True)
        return label

    def open_settings(self) -> None:
        SettingsDialog(load_settings(), self).exec()

    def _update_status_pill(self) -> None:
        online = sum(
            1
            for row in range(self.table.rowCount())
            if (item := self.table.item(row, COL_STATUS)) is not None
            and item.text() == "В сети"
        )
        self.status_pill.setText(online_summary(online, len(self.devices)))
```

- [ ] **Step 4: Вызвать `_update_status_pill` там, где меняется статус устройства**

В `_set_status` (см. текущий код, ~строка 780-792), в самом конце метода (после `self.table.setItem(row, COL_LATENCY, ...)`), добавить:
```python
        self._update_status_pill()
```

В `reload()` (см. текущий код, ~строка 563-600), после `self._update_bulk_actions()` и перед `self.refresh_statuses()`, добавить:
```python
        self._update_status_pill()
```
(На этот момент статусы ещё «Проверка…», не «В сети» — пилюля покажет «Онлайн: 0 из N» сразу после `reload()`, а `_set_status` будет обновлять её по мере ответов; это ожидаемо, не ошибка.)

- [ ] **Step 5: Прогнать весь набор тестов**

Run: `.venv\Scripts\python.exe -m pytest -q`
Expected: `361 passed`, без изменений в числе (правки в `app.py` в этом шаге не добавляют новых юнит-тестируемых чистых функций — раскладка проверяется живым запуском в Step 6).

- [ ] **Step 6: Живая проверка (обязательный шаг)**

Run: `.venv\Scripts\python.exe -m ven4control.app`

Проверить глазами:
- Слева — сайдбар с логотипом «Ven4Control» и акцентной цветной планкой рядом с ним (цвет — акцент активной темы), три раздела с заголовками КАПСОМ («УСТРОЙСТВА», «ВЫБРАННОЕ УСТРОЙСТВО», «МАССОВЫЕ ОПЕРАЦИИ»), внизу — статус-пилюля с текстом вида «Онлайн: N из M» и кнопки «Установка ключа»/«Настройки».
- Тулбара сверху больше нет.
- Справа — шапка «Устройства» / «Управление вашими SSH-устройствами», под ней таблица устройств — без изменений в столбцах/флажках/группах.
- Выбрать устройство в таблице — кнопки «ВЫБРАННОГО УСТРОЙСТВА» в сайдбаре становятся активными, `selected_label` показывает имя — то есть `_update_selection` по-прежнему работает с перенесёнными кнопками.
- Отметить флажок у одной-двух строк — «МАССОВЫЕ ОПЕРАЦИИ» активируются, `bulk_label` показывает счётчик.
- Нажать «Настройки» — открывается `SettingsDialog` (тот же диалог из фазы 1, теперь наконец подключённый к UI), выбрать другую тему — сайдбар и вся остальная раскраска перекрашиваются (проверяет, что `#sidebar`/`#contentHeader`/`#brandStrip` реально видят `apply_theme`, не только базовые виджеты).
- Подождать несколько секунд после запуска — статус-пилюля должна обновиться с «Онлайн: 0 из N» на реальное число, когда придут ответы `refresh_statuses`.

Закрыть приложение после проверки.

- [ ] **Step 7: Commit**

```bash
git add src/ven4control/app.py
git commit -m "Тулбар и панель действий заменены сайдбаром в стиле Ven4Tools"
```

---

## Self-Review (выполнено при написании плана)

1. **Покрытие спеки**: раздел «Главное окно» спеки → полностью Task 3 (сайдбар с тремя разделами, статус-пилюля, кнопка «Настройки», шапка контентной области). Task 1/2 — вспомогательные, обслуживают Task 3.
2. **Плейсхолдеров нет.**
3. **Согласованность**: полный список имён атрибутов, которые обязаны сохраниться, явно перечислен в Global Constraints и в Interfaces Task 3 — сверен построчно с реальным `_update_selection`/`_update_bulk_actions`/`_set_status`/`_session_status_changed`/`_tunnel_status_changed`/`_tunnel_closed` в текущем коде (не придуман по памяти).
4. **Реальная проверка импортов перед написанием плана**: `QAction` и `QToolBar` действительно нигде в файле не используются, кроме убираемого тулбара (проверено `grep`, не предположено) — их удаление из импортов безопасно.
5. **Живая проверка обязательна** (Task 3, Step 6) — не только чтобы увидеть раскладку, но чтобы поймать класс ошибок из фазы 1 (инлайн-стиль не переживает смену темы) на новых `#sidebar`/`#brandStrip`/`#contentHeader`, если бы они были стилизованы неверно.
