# Раскрывающиеся разделы сайдбара Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Сайдбар главного окна (фаза 2) перегружен — раздел «ВЫБРАННОЕ УСТРОЙСТВО» держит 8-10 кнопок подряд. Сделать три раздела сайдбара раскрывающимися по клику на заголовок; «МАССОВЫЕ ОПЕРАЦИИ» свёрнут по умолчанию (обычно пуст) и автоматически разворачивается, когда отмечено хоть одно устройство — остальные два раздела («УСТРОЙСТВА», «ВЫБРАННОЕ УСТРОЙСТВО») раскрыты по умолчанию, это основные действия, прятать их по умолчанию нельзя.

**Architecture:** Новый переиспользуемый виджет `CollapsibleSection` (заголовок-переключатель + тело со списком кнопок), заменяет плоские `QLabel`-заголовки разделов из фазы 2. Существующие кнопки/сигналы не меняются — только контейнер, в который они добавляются (`section.add_widget(...)` вместо `sidebar_layout.addWidget(...)` напрямую), тот же принцип осторожности, что в фазе 2.

**Tech Stack:** Python 3.12, PySide6, unittest.

## Global Constraints

- Решение принято пользователем заранее («авансом согласовано») по итогам обсуждения — не переспрашивать варианты компоновки, план ниже финальный.
- Инвариант `row == index` в `self.devices` и вся логика выбора/массовых операций (`_row_of`, `selected_device`, `checked_device_ids`, `_update_selection`, `_update_bulk_actions`, `reload()`) не меняются по существу.
- Имена атрибутов существующих кнопок не меняются: `self.terminal_button`, `self.console_button`, `self.control_button`, `self.logging_button`, `self.rdp_check_button`, `self.rdp_enable_button`, `self.rdp_button`, `self.group_button`, `self.forget_button`, `self.delete_button`, `self.selected_label`, `self.bulk_label`, `self.bulk_reboot_button`, `self.bulk_update_button`.
- `_section_label`/свойство `eyebrow` (введены в фазе 2) убираются целиком — их заменяет `CollapsibleSection`, использовавший их код был только в трёх местах, которые эта задача переписывает (проверено `grep`, не предположено).
- Код и UI-строки — на русском, в стиле остального проекта. Комментарии — только там, где решение неочевидно.
- Ни одного упоминания Claude/Anthropic/AI нигде — ни в коде, ни в commit message. Commit message — обычный текст в стиле `git log` этого репозитория, без трейлеров.
- Полный набор тестов (`.venv/Scripts/python.exe -m pytest -q`, venv в корне репозитория) остаётся зелёным на каждом коммите, включая все существующие 361.
- Раскладка проверяется живым запуском (обязательный шаг) — в этом проекте тесты не создают настоящий `QApplication`/виджеты.

---

## Task 1: `section_header_text` — текст заголовка-переключателя

**Files:**
- Modify: `src/ven4control/app.py` (добавить функцию рядом с `online_summary`)
- Test: `tests/test_app.py`

**Interfaces:**
- Produces: `section_header_text(title: str, expanded: bool) -> str`.

- [ ] **Step 1: Написать тест**

Добавить в `tests/test_app.py` (импортировать `section_header_text` вместе с `online_summary` из `ven4control.app`):

```python
class SectionHeaderTextTests(unittest.TestCase):
    def test_expanded_shows_down_arrow(self) -> None:
        self.assertEqual("▾ УСТРОЙСТВА", section_header_text("УСТРОЙСТВА", True))

    def test_collapsed_shows_right_arrow(self) -> None:
        self.assertEqual("▸ УСТРОЙСТВА", section_header_text("УСТРОЙСТВА", False))
```

- [ ] **Step 2: Прогнать, убедиться что падает**

Run: `.venv\Scripts\python.exe -m pytest tests/test_app.py::SectionHeaderTextTests -v`
Expected: FAIL — `ImportError: cannot import name 'section_header_text'`.

- [ ] **Step 3: Реализовать**

Добавить в `src/ven4control/app.py` сразу после `online_summary`:

```python
def section_header_text(title: str, expanded: bool) -> str:
    """Текст заголовка-переключателя раздела сайдбара: стрелка + название."""
    arrow = "▾" if expanded else "▸"
    return f"{arrow} {title}"
```

- [ ] **Step 4: Прогнать тест**

Run: `.venv\Scripts\python.exe -m pytest tests/test_app.py::SectionHeaderTextTests -v`
Expected: PASS (2 теста).

- [ ] **Step 5: Прогнать весь набор**

Run: `.venv\Scripts\python.exe -m pytest -q`
Expected: `363 passed` (361 + 2).

- [ ] **Step 6: Commit**

```bash
git add src/ven4control/app.py tests/test_app.py
git commit -m "Добавлена сборка текста заголовка-переключателя раздела сайдбара"
```

---

## Task 2: QSS-селектор заголовка-переключателя в `theme.py`

**Files:**
- Modify: `src/ven4control/theme.py`
- Modify: `tests/test_theme.py`

**Interfaces:**
- Produces: селектор `QPushButton#sectionToggle` в QSS, заменяющий убираемый `QLabel[eyebrow="true"]`.

- [ ] **Step 1: Написать тест**

Добавить в `tests/test_theme.py`, в класс `StylesheetTests`:

```python
    def test_stylesheet_styles_the_section_toggle(self) -> None:
        css = stylesheet_for(DEFAULT_THEME)
        self.assertIn("#sectionToggle", css)
        self.assertNotIn("eyebrow", css)
```

(Вторая проверка — что старый неиспользуемый селектор реально убран, а не просто заброшен рядом с новым.)

- [ ] **Step 2: Прогнать, убедиться что падает**

Run: `.venv\Scripts\python.exe -m pytest tests/test_theme.py::StylesheetTests::test_stylesheet_styles_the_section_toggle -v`
Expected: FAIL (`#sectionToggle` ещё нет).

- [ ] **Step 3: Заменить блок `QLabel[eyebrow="true"]`**

В `src/ven4control/theme.py` найти блок:
```python
QLabel[eyebrow="true"] {{
    color: {c['text_secondary']};
    font-size: 9pt;
    font-weight: 600;
}}
```
Заменить на:
```python
QPushButton#sectionToggle {{
    background-color: transparent;
    border: none;
    color: {c['text_secondary']};
    font-size: 9pt;
    font-weight: 600;
    text-align: left;
    padding: 4px 0;
}}

QPushButton#sectionToggle:hover {{
    color: {c['text_primary']};
}}
```

- [ ] **Step 4: Прогнать тесты**

Run: `.venv\Scripts\python.exe -m pytest tests/test_theme.py -v`
Expected: все PASS (16 — было 15 + 1 новый).

- [ ] **Step 5: Прогнать весь набор**

Run: `.venv\Scripts\python.exe -m pytest -q`
Expected: `364 passed`.

- [ ] **Step 6: Commit**

```bash
git add src/ven4control/theme.py tests/test_theme.py
git commit -m "Заголовок раздела сайдбара стилизован как переключатель"
```

---

## Task 3: `CollapsibleSection` и перестройка сайдбара

**Files:**
- Modify: `src/ven4control/app.py`

**Interfaces:**
- Consumes: `section_header_text` (Task 1), `#sectionToggle` (Task 2).
- Produces: `class CollapsibleSection(QWidget)` с методами `add_widget(widget: QWidget) -> None` и `set_expanded(expanded: bool) -> None`; `MainWindow._bulk_section: CollapsibleSection` (новый атрибут — на него ссылается `_update_bulk_actions`).

- [ ] **Step 1: Добавить класс `CollapsibleSection`**

Добавить в `src/ven4control/app.py` перед классом `MainWindow` (там же, где сейчас определены `Worker`/`WorkerSignals` — рядом с остальными вспомогательными классами модуля):

```python
class CollapsibleSection(QWidget):
    """Раздел сайдбара, который можно свернуть кликом по заголовку.

    Заголовок — QPushButton, не QLabel: клику нужен сигнал, у QLabel его
    нет. Стиль (без рамки/фона, как ярлык) — в theme.py, #sectionToggle.
    """

    def __init__(self, title: str, expanded: bool = True, parent=None):
        super().__init__(parent)
        self._title = title
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self._toggle = QPushButton(section_header_text(title, expanded))
        self._toggle.setObjectName("sectionToggle")
        self._toggle.setCheckable(True)
        self._toggle.setChecked(expanded)
        self._toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self._toggle.clicked.connect(self._on_toggled)
        layout.addWidget(self._toggle)

        self.body = QWidget()
        self._body_layout = QVBoxLayout(self.body)
        self._body_layout.setContentsMargins(0, 0, 0, 0)
        self._body_layout.setSpacing(6)
        self.body.setVisible(expanded)
        layout.addWidget(self.body)

    def add_widget(self, widget: QWidget) -> None:
        self._body_layout.addWidget(widget)

    def set_expanded(self, expanded: bool) -> None:
        self._toggle.setChecked(expanded)
        self._on_toggled(expanded)

    def _on_toggled(self, expanded: bool) -> None:
        self.body.setVisible(expanded)
        self._toggle.setText(section_header_text(self._title, expanded))
```

- [ ] **Step 2: Заменить блок сборки трёх разделов**

Найти в `MainWindow.__init__` (свериться через `grep -n 'sidebar_layout.addWidget(self._section_label' src/ven4control/app.py` — покажет 3 строки, начало заменяемого диапазона; конец — строка `sidebar_layout.addWidget(self.bulk_update_button)`, последняя перед `sidebar_layout.addStretch()`). Весь диапазон от первого `sidebar_layout.addWidget(self._section_label("УСТРОЙСТВА"))` до последнего `sidebar_layout.addWidget(self.bulk_update_button)` заменить на:

```python
        devices_section = CollapsibleSection("УСТРОЙСТВА", expanded=True)
        add_button = QPushButton("Добавить")
        add_button.clicked.connect(self.add_device)
        refresh_button = QPushButton("Обновить")
        refresh_button.clicked.connect(self.refresh_statuses)
        tailscale_button = QPushButton("Импорт Tailscale")
        tailscale_button.clicked.connect(self.import_tailscale)
        devices_section.add_widget(add_button)
        devices_section.add_widget(refresh_button)
        devices_section.add_widget(tailscale_button)
        sidebar_layout.addWidget(devices_section)

        sidebar_layout.addSpacing(10)
        selected_section = CollapsibleSection("ВЫБРАННОЕ УСТРОЙСТВО", expanded=True)
        self.selected_label = QLabel("Устройство не выбрано")
        self.selected_label.setWordWrap(True)
        selected_section.add_widget(self.selected_label)
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
        selected_section.add_widget(self.control_button)
        selected_section.add_widget(self.logging_button)
        selected_section.add_widget(self.console_button)
        selected_section.add_widget(self.terminal_button)
        selected_section.add_widget(self.rdp_check_button)
        selected_section.add_widget(self.rdp_enable_button)
        selected_section.add_widget(self.rdp_button)
        selected_section.add_widget(self.group_button)
        selected_section.add_widget(self.forget_button)
        selected_section.add_widget(self.delete_button)
        sidebar_layout.addWidget(selected_section)

        sidebar_layout.addSpacing(10)
        self._bulk_section = CollapsibleSection("МАССОВЫЕ ОПЕРАЦИИ", expanded=False)
        self.bulk_label = QLabel("Ничего не отмечено")
        self.bulk_label.setWordWrap(True)
        self._bulk_section.add_widget(self.bulk_label)
        self.bulk_reboot_button = QPushButton("Перезагрузить выбранные")
        self.bulk_reboot_button.clicked.connect(self.reboot_checked_devices)
        self.bulk_update_button = QPushButton("Обновить пакеты на выбранных")
        self.bulk_update_button.clicked.connect(self.update_checked_devices)
        self._bulk_section.add_widget(self.bulk_reboot_button)
        self._bulk_section.add_widget(self.bulk_update_button)
        sidebar_layout.addWidget(self._bulk_section)
```

Строки `sidebar_layout.addSpacing(10)`, которые в текущем коде стоят непосредственно ПЕРЕД каждым `self._section_label(...)` (кроме первого — перед «УСТРОЙСТВА» уже есть `sidebar_layout.addSpacing(10)` от блока логотипа, его не трогать), в новом блоке выше уже расставлены на тех же местах — отдельно ничего добавлять не нужно, просто не продублировать.

- [ ] **Step 3: Убрать `_section_label`**

Удалить метод `_section_label` целиком (в текущем коде — сразу после `self.restore_background_sessions()` в конце `__init__`, перед `open_settings`):
```python
    def _section_label(self, text: str) -> QLabel:
        """Заголовок раздела сайдбара: цвет и размер задаёт QSS темы."""
        label = QLabel(text)
        label.setProperty("eyebrow", True)
        return label
```

- [ ] **Step 4: Автораскрытие «МАССОВЫЕ ОПЕРАЦИИ» при отметке устройства**

В `_update_bulk_actions` (см. текущий код — метод считает `count` и обновляет `bulk_label`/кнопки) добавить в конец:
```python
        if count:
            self._bulk_section.set_expanded(True)
```
Полный метод после правки:
```python
    def _update_bulk_actions(self) -> None:
        count = len(self.checked_device_ids)
        self.bulk_label.setText(
            f"Отмечено устройств: {count}" if count else "Ничего не отмечено"
        )
        # Пока идёт массовая операция, вторая только смешала бы отчёты.
        enabled = bool(count) and not self._bulk_expected
        self.bulk_reboot_button.setEnabled(enabled)
        self.bulk_update_button.setEnabled(enabled)
        if count:
            self._bulk_section.set_expanded(True)
```

Раздел не сворачивается автоматически при снятии последней отметки — пользователь мог осознанно открыть его, чтобы посмотреть, а не только чтобы отметить; закрыть сам, если не нужен.

- [ ] **Step 5: Прогнать весь набор тестов**

Run: `.venv\Scripts\python.exe -m pytest -q`
Expected: `364 passed` (без изменений в числе — Task 3 не добавляет новых юнит-тестируемых чистых функций).

- [ ] **Step 6: Живая проверка (обязательный шаг)**

Run: `.venv\Scripts\python.exe -m ven4control.app`

Проверить глазами:
- Сайдбар: разделы «▾ УСТРОЙСТВА» и «▾ ВЫБРАННОЕ УСТРОЙСТВО» раскрыты по умолчанию (кнопки видны), «▸ МАССОВЫЕ ОПЕРАЦИИ» свёрнут (кнопок не видно, только заголовок).
- Клик по «▸ МАССОВЫЕ ОПЕРАЦИИ» — раскрывается (стрелка меняется на ▾, появляются `bulk_label`+2 кнопки), повторный клик — сворачивается обратно.
- Клик по «▾ ВЫБРАННОЕ УСТРОЙСТВО» — сворачивается, все кнопки этого раздела (терминал, управление, RDP и т.д.) прячутся, сайдбар визуально короче.
- Выбрать устройство в таблице, затем отметить флажок у любой строки — раздел «МАССОВЫЕ ОПЕРАЦИИ» автоматически разворачивается сам (без клика по заголовку) — это и есть автораскрытие из Step 4.
- Смена темы через «Настройки» — заголовки разделов (`#sectionToggle`) перекрашиваются вместе со всем остальным (проверяет, что QSS-селектор реально применяется, а не остался от старого `eyebrow`).
- Открыть `DeviceControlDialog` («Управление устройством») — не должен был измениться (эта задача его не трогает), просто визуальная сверка, что ничего не сломалось рядом.

Закрыть приложение после проверки.

- [ ] **Step 7: Commit**

```bash
git add src/ven4control/app.py
git commit -m "Разделы сайдбара сделаны раскрывающимися, массовые операции свёрнуты по умолчанию"
```

---

## Self-Review (выполнено при написании плана)

1. **Покрытие**: пользовательский запрос («раскрывающиеся категории кнопок», сайдбар перегружен) → Task 3 целиком; заголовок-переключатель и его текст — Task 1/2, обслуживают Task 3.
2. **Плейсхолдеров нет.**
3. **Согласованность**: `CollapsibleSection.add_widget`/`set_expanded` — одни и те же сигнатуры в определении (Task 3, Step 1) и во всех местах использования (Step 2, Step 4). `section_header_text(title, expanded)` — сигнатура одна и та же в Task 1 (определение) и Task 3 (использование внутри `CollapsibleSection`).
4. **Реальная проверка перед написанием плана**: `_section_label`/`eyebrow` использовались ровно в 3 местах (проверено `grep`, не предположено) — их полное удаление безопасно, ничего другого не ссылается.
5. **Автораскрытие — решение, не в пользовательском запросе явно, но логичное следствие**: без него отметка флажка в свёрнутом разделе была бы незаметна пользователю (кнопки массовых операций стали активны, а раздел, где они лежат, всё ещё скрыт) — регрессия открываемости по сравнению с фазой 2, где раздел всегда был на виду. Добавлено с обоснованием в Step 4, а не молча.
