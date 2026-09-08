"""Экранная модель встроенного терминала: разбор ANSI/VT100 и отрисовка.

Полная совместимость с xterm целью не ставилась: разбирается то, что
реально приходит от оболочки устройства — печать текста, перевод строки и
возврат каретки, забой, перемещение курсора, очистка экрана и строки,
цвета и начертание SGR. Незнакомые последовательности отбрасываются
целиком: иначе их параметры оказались бы на экране мусором.

Модель состоит из сетки видимого экрана и очереди строк, ушедших вверх.
Строка, вытесненная за верхний край, больше не меняется, поэтому виджет
забирает её один раз и дописывает в документ навсегда, а перерисовывает
только сетку — иначе каждый чанк вывода перерисовывал бы всю историю.
"""
from __future__ import annotations

import html
from dataclasses import dataclass, replace


ESC = "\x1b"
BEL = "\x07"

DEFAULT_COLUMNS = 100
DEFAULT_ROWS = 30
MIN_COLUMNS = 20
MIN_ROWS = 4
MAX_COLUMNS = 500
MAX_ROWS = 200

TAB_WIDTH = 8

# Незавершённая последовательность ждёт следующей порции данных. Ограничение
# спасает от накопления мусора, который никогда не закончится финальным байтом.
MAX_PENDING = 256

# Сколько вытесненных строк хранить, пока их не забрал виджет.
MAX_SCROLLED_OFF = 5000

# Цвета по умолчанию: те же значения использует палитра виджета, поэтому
# инверсия (SGR 7) может подставить их явно.
DEFAULT_FOREGROUND = "#d8d8d8"
DEFAULT_BACKGROUND = "#101014"


def set_default_colors(background: str, foreground: str) -> None:
    """Переопределяет цвета терминала по умолчанию — вызывается при открытии
    нового терминала с цветами активной темы. Уже отрисованная история
    существующих терминалов не перекрашивается задним числом: это цвета
    ПО УМОЛЧАНИЮ (инверсия SGR без явного цвета, фон нового QTextEdit),
    а не биндинг на тему.
    """
    global DEFAULT_BACKGROUND, DEFAULT_FOREGROUND
    DEFAULT_BACKGROUND = background
    DEFAULT_FOREGROUND = foreground


BASE_COLORS = (
    "#000000", "#cd0000", "#00cd00", "#cdcd00",
    "#3465a4", "#cd00cd", "#00cdcd", "#e5e5e5",
)
BRIGHT_COLORS = (
    "#7f7f7f", "#ff5555", "#55ff55", "#ffff55",
    "#5c7ce0", "#ff55ff", "#55ffff", "#ffffff",
)

# Уровни яркости шестигранного куба 256-цветной палитры.
CUBE_LEVELS = (0, 95, 135, 175, 215, 255)


@dataclass(frozen=True, slots=True)
class CellStyle:
    """Оформление одного знакоместа."""

    foreground: str = ""
    background: str = ""
    bold: bool = False
    underline: bool = False
    inverse: bool = False


DEFAULT_STYLE = CellStyle()


def normalize_size(columns: int, rows: int) -> tuple[int, int]:
    """Приводит размер окна к разумным границам."""
    safe_columns = min(max(int(columns), MIN_COLUMNS), MAX_COLUMNS)
    safe_rows = min(max(int(rows), MIN_ROWS), MAX_ROWS)
    return safe_columns, safe_rows


def terminal_size(
    width: float,
    height: float,
    char_width: float,
    line_height: float,
) -> tuple[int, int]:
    """Переводит размер виджета в пикселях в размер окна терминала."""
    if char_width <= 0 or line_height <= 0:
        return DEFAULT_COLUMNS, DEFAULT_ROWS
    return normalize_size(int(width // char_width), int(height // line_height))


def color_256(index: int) -> str:
    """Цвет из 256-цветной палитры."""
    value = min(max(int(index), 0), 255)
    if value < 8:
        return BASE_COLORS[value]
    if value < 16:
        return BRIGHT_COLORS[value - 8]
    if value < 232:
        cube = value - 16
        return "#{:02x}{:02x}{:02x}".format(
            CUBE_LEVELS[cube // 36],
            CUBE_LEVELS[(cube // 6) % 6],
            CUBE_LEVELS[cube % 6],
        )
    grey = 8 + 10 * (value - 232)
    return f"#{grey:02x}{grey:02x}{grey:02x}"


def _extended_color(params: list[int], index: int) -> tuple[str, int]:
    """Разбирает 38/48: возвращает цвет и число разобранных параметров."""
    if index < len(params) and params[index] == 5:
        if index + 1 < len(params):
            return color_256(params[index + 1]), 2
        return "", 1
    if index < len(params) and params[index] == 2:
        if index + 3 < len(params):
            red, green, blue = (min(max(v, 0), 255) for v in params[index + 1:index + 4])
            return f"#{red:02x}{green:02x}{blue:02x}", 4
        return "", len(params) - index
    return "", 1


def apply_sgr(style: CellStyle, params: list[int]) -> CellStyle:
    """Применяет параметры SGR к текущему оформлению."""
    if not params:
        return DEFAULT_STYLE
    result = style
    index = 0
    while index < len(params):
        code = params[index]
        index += 1
        if code == 0:
            result = DEFAULT_STYLE
        elif code == 1:
            result = replace(result, bold=True)
        elif code in (21, 22):
            result = replace(result, bold=False)
        elif code == 4:
            result = replace(result, underline=True)
        elif code == 24:
            result = replace(result, underline=False)
        elif code == 7:
            result = replace(result, inverse=True)
        elif code == 27:
            result = replace(result, inverse=False)
        elif 30 <= code <= 37:
            result = replace(result, foreground=BASE_COLORS[code - 30])
        elif 90 <= code <= 97:
            result = replace(result, foreground=BRIGHT_COLORS[code - 90])
        elif code == 39:
            result = replace(result, foreground="")
        elif 40 <= code <= 47:
            result = replace(result, background=BASE_COLORS[code - 40])
        elif 100 <= code <= 107:
            result = replace(result, background=BRIGHT_COLORS[code - 100])
        elif code == 49:
            result = replace(result, background="")
        elif code in (38, 48):
            color, used = _extended_color(params, index)
            index += used
            if color:
                field = "foreground" if code == 38 else "background"
                result = replace(result, **{field: color})
    return result


def style_to_css(style: CellStyle) -> str:
    """Инлайновый CSS знакоместа. Пустая строка — оформление по умолчанию."""
    foreground = style.foreground
    background = style.background
    if style.inverse:
        foreground, background = (
            background or DEFAULT_BACKGROUND,
            foreground or DEFAULT_FOREGROUND,
        )
    rules: list[str] = []
    if foreground:
        rules.append(f"color:{foreground}")
    if background:
        rules.append(f"background-color:{background}")
    if style.bold:
        rules.append("font-weight:bold")
    if style.underline:
        rules.append("text-decoration:underline")
    return ";".join(rules)


def _escape(text: str) -> str:
    # Пробелы теряются при вставке HTML в документ Qt, а вместе с ними —
    # выравнивание колонок вывода вроде `ls -l` или `htop`.
    return html.escape(text).replace(" ", "&nbsp;")


def render_line(chars: list[str], styles: list[CellStyle]) -> str:
    """Собирает HTML одной строки, объединяя соседние знакоместа с общим стилем."""
    parts: list[str] = []
    run: list[str] = []
    current: CellStyle | None = None
    limit = len(chars)
    while limit > 0 and chars[limit - 1] == " " and styles[limit - 1] == DEFAULT_STYLE:
        limit -= 1
    for index in range(limit):
        style = styles[index]
        if current is None or style != current:
            if run:
                parts.append(_wrap(current, "".join(run)))
                run = []
            current = style
        run.append(chars[index])
    if run:
        parts.append(_wrap(current, "".join(run)))
    return "".join(parts)


def _wrap(style: CellStyle | None, text: str) -> str:
    css = style_to_css(style) if style is not None else ""
    if not css:
        return _escape(text)
    return f'<span style="{css}">{_escape(text)}</span>'


class TerminalScreen:
    """Сетка знакомест терминала и разбор потока вывода устройства."""

    def __init__(self, columns: int = DEFAULT_COLUMNS, rows: int = DEFAULT_ROWS):
        self.columns, self.rows = normalize_size(columns, rows)
        self._chars = [self._blank_chars() for _ in range(self.rows)]
        self._styles = [self._blank_styles() for _ in range(self.rows)]
        self._row = 0
        self._column = 0
        self._saved = (0, 0)
        self.style = DEFAULT_STYLE
        self._pending = ""
        self._scrolled_off: list[tuple[list[str], list[CellStyle]]] = []

    # --- состояние ---------------------------------------------------------

    @property
    def cursor(self) -> tuple[int, int]:
        return self._row, self._column

    def text(self) -> list[str]:
        """Весь известный текст: ещё не забранная история плюс экран."""
        lines = ["".join(chars).rstrip() for chars, _ in self._scrolled_off]
        lines += ["".join(row).rstrip() for row in self._chars]
        return lines

    def screen_text(self) -> list[str]:
        return ["".join(row).rstrip() for row in self._chars]

    def take_scrollback(self) -> list[str]:
        """Отдаёт HTML строк, ушедших вверх, и забывает их."""
        lines = [render_line(chars, styles) for chars, styles in self._scrolled_off]
        self._scrolled_off.clear()
        return lines

    def render_screen(self) -> str:
        """HTML видимой сетки без пустого хвоста под курсором."""
        last = self._row
        for index in range(self.rows - 1, -1, -1):
            if "".join(self._chars[index]).strip():
                last = max(last, index)
                break
        lines = [
            render_line(self._chars[index], self._styles[index])
            for index in range(last + 1)
        ]
        return "<br>".join(lines)

    # --- разбор потока -----------------------------------------------------

    def feed(self, text: str) -> None:
        """Разбирает очередную порцию вывода устройства."""
        data = self._pending + text
        self._pending = ""
        index = 0
        length = len(data)
        while index < length:
            char = data[index]
            if char == ESC:
                consumed = self._handle_escape(data, index)
                if consumed == 0:
                    tail = data[index:]
                    # Мусор без финального байта иначе копился бы вечно.
                    self._pending = tail if len(tail) <= MAX_PENDING else ""
                    return
                index += consumed
                continue
            self._put(char)
            index += 1

    def _put(self, char: str) -> None:
        if char == "\n":
            self._line_feed()
        elif char == "\r":
            self._column = 0
        elif char == "\b":
            self._column = max(0, self._column - 1)
        elif char == "\t":
            self._column = min(
                self.columns - 1,
                ((self._column // TAB_WIDTH) + 1) * TAB_WIDTH,
            )
        elif char in ("\x0b", "\x0c"):
            self._line_feed()
        elif char < " " or char == "\x7f":
            # Звонок и прочие управляющие символы печатать нечем.
            return
        else:
            self._write(char)

    def _write(self, char: str) -> None:
        if self._column >= self.columns:
            self._column = 0
            self._line_feed()
        self._chars[self._row][self._column] = char
        self._styles[self._row][self._column] = self.style
        self._column += 1

    def _line_feed(self) -> None:
        if self._row + 1 < self.rows:
            self._row += 1
            return
        self._scroll_up()

    def _scroll_up(self) -> None:
        self._scrolled_off.append((self._chars.pop(0), self._styles.pop(0)))
        if len(self._scrolled_off) > MAX_SCROLLED_OFF:
            del self._scrolled_off[: len(self._scrolled_off) - MAX_SCROLLED_OFF]
        self._chars.append(self._blank_chars())
        self._styles.append(self._blank_styles())

    def _blank_chars(self) -> list[str]:
        return [" "] * self.columns

    def _blank_styles(self) -> list[CellStyle]:
        return [DEFAULT_STYLE] * self.columns

    # --- escape-последовательности ----------------------------------------

    def _handle_escape(self, data: str, start: int) -> int:
        """Разбирает последовательность. 0 — она пришла не целиком."""
        if start + 1 >= len(data):
            return 0
        kind = data[start + 1]
        if kind == "[":
            return self._handle_csi(data, start)
        if kind == "]":
            return self._skip_osc(data, start)
        if kind in "()*+#%":
            # Выбор набора символов: занимает ещё один байт и ни на что
            # в этой модели не влияет.
            return 3 if start + 2 < len(data) else 0
        if kind == "7":
            self._saved = (self._row, self._column)
            return 2
        if kind == "8":
            self._row, self._column = self._saved
            self._clamp()
            return 2
        if kind == "M":
            if self._row > 0:
                self._row -= 1
            return 2
        if kind in ("D", "E"):
            if kind == "E":
                self._column = 0
            self._line_feed()
            return 2
        return 2

    def _skip_osc(self, data: str, start: int) -> int:
        """Пропускает команду операционной системы (обычно заголовок окна)."""
        index = start + 2
        while index < len(data):
            if data[index] == BEL:
                return index - start + 1
            if data[index] == ESC and index + 1 < len(data) and data[index + 1] == "\\":
                return index - start + 2
            index += 1
        return 0

    def _handle_csi(self, data: str, start: int) -> int:
        index = start + 2
        while index < len(data) and "\x30" <= data[index] <= "\x3f":
            index += 1
        while index < len(data) and "\x20" <= data[index] <= "\x2f":
            index += 1
        if index >= len(data):
            return 0
        final = data[index]
        raw = data[start + 2:index]
        consumed = index - start + 1
        if raw[:1] in ("?", ">", "<", "="):
            # Приватные режимы (альтернативный экран, курсор, мышь) не
            # поддерживаются, но их параметры печатать нельзя.
            return consumed
        self._apply_csi(self._parse_params(raw), final)
        return consumed

    @staticmethod
    def _parse_params(raw: str) -> list[int]:
        params: list[int] = []
        for part in raw.split(";"):
            try:
                params.append(int(part))
            except ValueError:
                params.append(0)
        return params if raw else []

    def _apply_csi(self, params: list[int], final: str) -> None:
        first = params[0] if params else 0
        count = max(1, first)
        if final == "m":
            self.style = apply_sgr(self.style, params)
        elif final == "A":
            self._row = max(0, self._row - count)
        elif final == "B":
            self._row = min(self.rows - 1, self._row + count)
        elif final == "C":
            self._column = min(self.columns - 1, self._column + count)
        elif final == "D":
            self._column = max(0, self._column - count)
        elif final == "E":
            self._row = min(self.rows - 1, self._row + count)
            self._column = 0
        elif final == "F":
            self._row = max(0, self._row - count)
            self._column = 0
        elif final in ("G", "`"):
            self._column = count - 1
        elif final == "d":
            self._row = count - 1
        elif final in ("H", "f"):
            self._row = count - 1
            self._column = (max(1, params[1]) - 1) if len(params) > 1 else 0
        elif final == "J":
            self._erase_display(first)
        elif final == "K":
            self._erase_line(first)
        elif final == "L":
            self._insert_lines(count)
        elif final == "M":
            self._delete_lines(count)
        elif final == "P":
            self._delete_chars(count)
        elif final == "@":
            self._insert_chars(count)
        elif final == "X":
            self._erase_chars(count)
        elif final == "s":
            self._saved = (self._row, self._column)
        elif final == "u":
            self._row, self._column = self._saved
        self._clamp()

    def _clamp(self) -> None:
        self._row = min(max(self._row, 0), self.rows - 1)
        self._column = min(max(self._column, 0), self.columns - 1)

    def _blank_row(self, row: int) -> None:
        self._chars[row] = self._blank_chars()
        self._styles[row] = self._blank_styles()

    def _erase_display(self, mode: int) -> None:
        if mode == 0:
            self._erase_line(0)
            for row in range(self._row + 1, self.rows):
                self._blank_row(row)
        elif mode == 1:
            self._erase_line(1)
            for row in range(0, self._row):
                self._blank_row(row)
        else:
            for row in range(self.rows):
                self._blank_row(row)

    def _erase_line(self, mode: int) -> None:
        if mode == 0:
            span = range(self._column, self.columns)
        elif mode == 1:
            span = range(0, self._column + 1)
        else:
            span = range(0, self.columns)
        for column in span:
            self._chars[self._row][column] = " "
            self._styles[self._row][column] = DEFAULT_STYLE

    def _insert_lines(self, count: int) -> None:
        for _ in range(min(count, self.rows - self._row)):
            self._chars.insert(self._row, self._blank_chars())
            self._styles.insert(self._row, self._blank_styles())
            self._chars.pop()
            self._styles.pop()

    def _delete_lines(self, count: int) -> None:
        for _ in range(min(count, self.rows - self._row)):
            del self._chars[self._row]
            del self._styles[self._row]
            self._chars.append(self._blank_chars())
            self._styles.append(self._blank_styles())

    def _delete_chars(self, count: int) -> None:
        for _ in range(min(count, self.columns - self._column)):
            del self._chars[self._row][self._column]
            del self._styles[self._row][self._column]
            self._chars[self._row].append(" ")
            self._styles[self._row].append(DEFAULT_STYLE)

    def _insert_chars(self, count: int) -> None:
        for _ in range(min(count, self.columns - self._column)):
            self._chars[self._row].insert(self._column, " ")
            self._styles[self._row].insert(self._column, DEFAULT_STYLE)
            self._chars[self._row].pop()
            self._styles[self._row].pop()

    def _erase_chars(self, count: int) -> None:
        for column in range(self._column, min(self.columns, self._column + count)):
            self._chars[self._row][column] = " "
            self._styles[self._row][column] = DEFAULT_STYLE

    # --- изменение размера -------------------------------------------------

    def resize(self, columns: int, rows: int) -> None:
        """Меняет размер сетки, сохраняя видимый текст и положение курсора."""
        new_columns, new_rows = normalize_size(columns, rows)
        if (new_columns, new_rows) == (self.columns, self.rows):
            return
        for row in range(len(self._chars)):
            self._chars[row] = self._fit(self._chars[row], new_columns, " ")
            self._styles[row] = self._fit(self._styles[row], new_columns, DEFAULT_STYLE)
        self.columns = new_columns
        while len(self._chars) > new_rows:
            # Уменьшение окна съедает строки сверху: курсор со свежим выводом
            # должен остаться на экране.
            self._scrolled_off.append((self._chars.pop(0), self._styles.pop(0)))
            self._row -= 1
        while len(self._chars) < new_rows:
            self._chars.append([" "] * new_columns)
            self._styles.append([DEFAULT_STYLE] * new_columns)
        self.rows = new_rows
        self._clamp()

    @staticmethod
    def _fit(row: list, width: int, filler) -> list:
        if len(row) > width:
            return row[:width]
        return row + [filler] * (width - len(row))
