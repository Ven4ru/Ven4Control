# Сайдбар-навигация DeviceControlDialog (фаза 3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Заменить верхние вкладки `DeviceControlDialog` (`QTabWidget`: Обзор/Сервисы/Логи/Фоновый журнал/Файлы/Обслуживание) на `QStackedWidget` + вертикальный список кнопок-разделов слева, стилизованных как `NavButtonStyle`/`ActiveNavButtonStyle` из Ven4Tools — теми же QSS-селекторами `[nav="true"]`/`[navActive="true"]`, которые фаза 1 завела в `theme.py`, но которые до сих пор нигде не применялись.

**Architecture:** Каждая существующая `_create_*_tab()`-фабрика страницы не переписывается — меняется только то, что кладёт готовый виджет в `QStackedWidget.addWidget(...)` вместо `QTabWidget.addTab(...)`, и связка кнопка→индекс страницы вместо клика по вкладке. Активная кнопка помечается динамическим свойством `navActive` — Qt требует `unpolish()`/`polish()` после изменения свойства на уже показанном виджете, иначе QSS не переоценивает селектор (в отличие от фаз 1/2, где свойства выставлялись один раз при создании, до первого показа, и этой пляски не требовалось).

**Tech Stack:** Python 3.12, PySide6, unittest.

## Global Constraints

- `self.tabs` (имя атрибута) не сохраняется — переименовывается в `self.pages` (`QStackedWidget`), это соответствует реальности (больше не вкладки), и ни один тест или другой файл репозитория не ссылается на `self.tabs`/`_tab_changed`/`dialog.tabs.indexOf` (проверено `grep -rn` по `tests/` и `src/ven4control/*.py` — ни одного совпадения вне `control_dialog.py`). Внешние живые проверочные скрипты (не в репозитории) при следующем запуске нужно будет поправить под новое имя — это ожидаемо, не регрессия.
- `self.files_page` — то же самое имя атрибута, тот же смысл («вкладка Файлы» → «страница Файлы»), используется для ленивого открытия SFTP-соединения при первом заходе (`is self.files_page` сравнение) — логика не меняется, только `self.tabs.widget(index)` → `self.pages.widget(index)`.
- Ни одна из шести `_create_*_tab()`-фабрик не редактируется — эта задача про контейнер навигации, не про содержимое страниц.
- Код и UI-строки — на русском, в стиле остального проекта. Комментарии — только там, где решение неочевидно.
- Ни одного упоминания Claude/Anthropic/AI нигде — ни в коде, ни в commit message. Commit message — обычный текст в стиле `git log` этого репозитория, без трейлеров.
- Полный набор тестов (`.venv/Scripts/python.exe -m pytest -q`, venv в корне репозитория) остаётся зелёным на каждом коммите, включая все существующие 364.
- Раскладка проверяется живым запуском (обязательный шаг) — в этом проекте тесты не создают настоящий `QApplication`/виджеты.

---

## Task 1: Тест на существование `[nav="true"]`/`[navActive="true"]` в QSS

Эти селекторы уже есть в `theme.py` с фазы 1 (заведены заранее, но нигде не применялись до этой задачи) — теста на них ни разу не было. Закрыть пробел перед тем, как эта задача начнёт на них полагаться.

**Files:**
- Modify: `tests/test_theme.py`

**Interfaces:**
- Ничего нового не производит — только тестирует уже существующий `stylesheet_for`.

- [ ] **Step 1: Написать тест**

Добавить в `tests/test_theme.py`, в класс `StylesheetTests`:

```python
    def test_stylesheet_styles_dialog_nav_buttons(self) -> None:
        css = stylesheet_for(DEFAULT_THEME)
        self.assertIn('[nav="true"]', css)
        self.assertIn('[navActive="true"]', css)
```

- [ ] **Step 2: Прогнать — уже должен пройти**

Run: `.venv\Scripts\python.exe -m pytest tests/test_theme.py::StylesheetTests::test_stylesheet_styles_dialog_nav_buttons -v`
Expected: PASS сразу (селекторы уже существуют с фазы 1 — это фиксирующий тест, не TDD-разработка новой функциональности).

- [ ] **Step 3: Прогнать весь набор**

Run: `.venv\Scripts\python.exe -m pytest -q`
Expected: `365 passed` (364 + 1).

- [ ] **Step 4: Commit**

```bash
git add tests/test_theme.py
git commit -m "Добавлен тест на QSS-селекторы навигации диалогов"
```

---

## Task 2: `QStackedWidget` + вертикальная навигация в `DeviceControlDialog`

**Files:**
- Modify: `src/ven4control/control_dialog.py`

**Interfaces:**
- Consumes: `[nav="true"]`/`[navActive="true"]` (Task 1, уже в `theme.py`).
- Produces: `DeviceControlDialog.pages: QStackedWidget` (заменяет `self.tabs`), `DeviceControlDialog._page_changed(index: int) -> None` (заменяет `_tab_changed`). `self.files_page` — то же имя, та же роль.

- [ ] **Step 1: Обновить импорты**

В `src/ven4control/control_dialog.py`, в блоке `from PySide6.QtWidgets import (...)` — убрать `QTabWidget`, добавить `QStackedWidget` (сохранить алфавитный порядок остальных имён как есть):
```python
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
```

- [ ] **Step 2: Заменить блок создания вкладок**

Найти в `__init__` (свериться `grep -n "self.tabs = QTabWidget" src/ven4control/control_dialog.py` перед правкой — номер строки мог сдвинуться) блок от `self.tabs = QTabWidget()` до `self.tabs.currentChanged.connect(self._tab_changed)` включительно (в текущем коде это строки 106-117). Заменить на:

```python
        self.pages = QStackedWidget()
        nav_panel = QWidget()
        nav_panel.setObjectName("dialogNav")
        nav_panel.setFixedWidth(170)
        nav_layout = QVBoxLayout(nav_panel)
        nav_layout.setContentsMargins(8, 8, 8, 8)
        nav_layout.setSpacing(4)

        self.files_page = self._create_files_tab()
        page_titles = [
            ("Обзор", self._create_overview_tab()),
            ("Сервисы", self._create_services_tab()),
            ("Логи", self._create_logs_tab()),
            ("Фоновый журнал", self._create_background_tab()),
            ("Файлы", self.files_page),
            ("Обслуживание", self._create_maintenance_tab()),
        ]
        self._nav_buttons: list[QPushButton] = []
        for title, page in page_titles:
            button = QPushButton(title)
            button.setProperty("nav", True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            index = self.pages.count()
            button.clicked.connect(lambda _checked, i=index: self.pages.setCurrentIndex(i))
            nav_layout.addWidget(button)
            self._nav_buttons.append(button)
            self.pages.addWidget(page)
        nav_layout.addStretch()

        # SFTP-соединение открывается только когда его действительно
        # попросили: за обзором и логами пользователь на страницу файлов
        # может не зайти ни разу.
        self.pages.currentChanged.connect(self._page_changed)
        self._page_changed(0)
```

**Порядок создания страниц не меняется** — `self.files_page = self._create_files_tab()` по-прежнему создаётся до остальных фабрик в списке (как и в исходном коде, где строка с `_create_files_tab()` стояла между «Фоновый журнал» и «Обслуживание») — переставлять фабрики местами нельзя, только форма, в которой они собираются.

- [ ] **Step 3: Заменить раскладку диалога — добавить `nav_panel` рядом со страницами**

Найти (см. текущий код, строки ~119-125):
```python
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(title)
        layout.addWidget(self.tabs, 1)
        layout.addWidget(self.status)
```
Заменить на:
```python
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)

        content_row = QHBoxLayout()
        content_row.setContentsMargins(0, 0, 0, 0)
        content_row.setSpacing(0)
        content_row.addWidget(nav_panel)
        content_row.addWidget(self.pages, 1)

        layout = QVBoxLayout(self)
        layout.addWidget(title)
        layout.addLayout(content_row, 1)
        layout.addWidget(self.status)
```
(Строки после `layout.addWidget(self.status)`, включая добавление `buttons`, не трогать — они остаются как есть, только не показаны здесь для краткости диффа.)

- [ ] **Step 4: Заменить `_tab_changed` на `_page_changed`**

Найти (см. текущий код, ~строка 397-399):
```python
    def _tab_changed(self, index: int) -> None:
        if self.tabs.widget(index) is self.files_page:
            self._start_files_session()
```
Заменить на:
```python
    def _page_changed(self, index: int) -> None:
        # Активная кнопка навигации — уже показанный виджет, свойство
        # меняется в рантайме, а не при создании (как в фазах 1/2) —
        # unpolish/polish обязательны, иначе QSS не переоценит селектор.
        for i, button in enumerate(self._nav_buttons):
            button.setProperty("navActive", i == index)
            button.style().unpolish(button)
            button.style().polish(button)
        if self.pages.widget(index) is self.files_page:
            self._start_files_session()
```

- [ ] **Step 5: Прогнать весь набор тестов**

Run: `.venv\Scripts\python.exe -m pytest -q`
Expected: `365 passed` (без изменений в числе — эта задача не добавляет новых юнит-тестируемых чистых функций, раскладка проверяется живым запуском в Step 6).

- [ ] **Step 6: Живая проверка (обязательный шаг)**

Run: `.venv\Scripts\python.exe -m ven4control.app`, выбрать любое устройство, нажать «Управление устройством».

Проверить глазами:
- Слева — узкая колонка с 6 кнопками (Обзор/Сервисы/Логи/Фоновый журнал/Файлы/Обслуживание) вместо вкладок сверху.
- «Обзор» активен сразу при открытии — подсвечен (акцентный фон/текст через `navActive`), остальные — обычные.
- Клик по «Сервисы» — переключает страницу, подсветка переходит на «Сервисы», «Обзор» становится обычной кнопкой.
- Клик по «Файлы» — страница переключается, СРАЗУ начинается подключение SFTP (как раньше при переходе на вкладку «Файлы») — то есть `_start_files_session`/ленивое открытие соединения продолжает срабатывать.
- Пройтись по всем 6 разделам по очереди — каждый показывает своё содержимое (Обзор — показатели системы, Сервисы — таблица служб, Логи — окно журнала, Фоновый журнал — статус сессии, Файлы — SFTP-браузер, Обслуживание — кнопки обновления/бэкапа/перезагрузки), ничего не пустое и не задваивается.
- Смена темы через главное окно (закрыть диалог, «Настройки», сменить тему, открыть диалог заново) — подсветка активного раздела и сама навигация перекрашиваются вместе с остальным интерфейсом.
- Закрыть диалог (кнопка «Закрыть» / крестик) — не должно быть исключений в консоли, обычное закрытие как раньше.

Закрыть приложение после проверки.

- [ ] **Step 7: Commit**

```bash
git add src/ven4control/control_dialog.py
git commit -m "Вкладки DeviceControlDialog заменены сайдбар-навигацией в стиле Ven4Tools"
```

---

## Self-Review (выполнено при написании плана)

1. **Покрытие спеки**: раздел «DeviceControlDialog» спеки → Task 2 целиком; Task 1 закрывает предсуществующий пробел в тестах (селекторы фазы 1 без проверки), обслуживает Task 2.
2. **Плейсхолдеров нет.**
3. **Согласованность**: `self.pages`/`self._nav_buttons`/`self.files_page` — одни и те же имена в определении (Task 2, Step 2) и использовании (Step 3, Step 4). `_page_changed(index: int)` — сигнатура та же, что была у `_tab_changed`.
4. **Реальная проверка перед написанием плана**: `grep -rn` подтвердил, что `self.tabs`/`_tab_changed`/`files_page` используются ТОЛЬКО внутри `control_dialog.py`, 9 обращений, все перечислены и учтены — переименование `tabs`→`pages` не ломает ничего снаружи файла.
5. **Реальная Qt-ловушка задокументирована заранее, не постфактум**: смена `navActive` в рантайме (в отличие от фаз 1/2, где свойства темы выставлялись один раз при создании до первого показа) требует `unpolish()`/`polish()` — без них QSS не переоценил бы селектор при клике, кнопка осталась бы неподсвеченной. Указано явно в Architecture и в коде Step 4, а не оставлено на усмотрение исполнителя.
6. **Живая проверка обязательна** (Step 6) — включает специально пункт про повторное открытие диалога после смены темы, поскольку это первый раз, когда `[navActive="true"]` вообще на что-то влияет в реальном UI.
