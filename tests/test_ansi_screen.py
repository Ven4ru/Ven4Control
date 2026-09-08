import unittest

from ven4control.ansi_screen import (
    DEFAULT_COLUMNS,
    DEFAULT_ROWS,
    DEFAULT_STYLE,
    MAX_COLUMNS,
    MAX_ROWS,
    MIN_COLUMNS,
    MIN_ROWS,
    CellStyle,
    TerminalScreen,
    apply_sgr,
    color_256,
    normalize_size,
    render_line,
    style_to_css,
    terminal_size,
)


class SizeTests(unittest.TestCase):
    def test_size_is_kept_inside_the_limits(self) -> None:
        self.assertEqual((MIN_COLUMNS, MIN_ROWS), normalize_size(1, 1))
        self.assertEqual((MAX_COLUMNS, MAX_ROWS), normalize_size(9000, 9000))
        self.assertEqual((80, 24), normalize_size(80, 24))

    def test_widget_size_is_translated_to_characters(self) -> None:
        self.assertEqual((80, 24), terminal_size(800, 480, 10, 20))

    def test_remainder_of_a_line_is_not_counted(self) -> None:
        self.assertEqual((80, 24), terminal_size(809, 499, 10, 20))

    def test_unknown_font_metrics_fall_back_to_the_default(self) -> None:
        """Метрики нулевые до раскладки виджета: делить на ноль нельзя."""
        self.assertEqual((DEFAULT_COLUMNS, DEFAULT_ROWS), terminal_size(800, 480, 0, 0))


class ColorTests(unittest.TestCase):
    def test_first_sixteen_colors_come_from_the_base_palette(self) -> None:
        self.assertEqual("#000000", color_256(0))
        self.assertEqual("#ffffff", color_256(15))

    def test_cube_and_greyscale_are_computed(self) -> None:
        self.assertEqual("#000000", color_256(16))
        self.assertEqual("#ffffff", color_256(231))
        self.assertEqual("#080808", color_256(232))

    def test_index_outside_the_palette_is_clamped(self) -> None:
        self.assertEqual(color_256(0), color_256(-5))
        self.assertEqual(color_256(255), color_256(999))


class SgrTests(unittest.TestCase):
    def test_empty_parameters_reset_the_style(self) -> None:
        style = CellStyle(foreground="#cd0000", bold=True)
        self.assertEqual(DEFAULT_STYLE, apply_sgr(style, []))
        self.assertEqual(DEFAULT_STYLE, apply_sgr(style, [0]))

    def test_basic_colors_and_weight(self) -> None:
        style = apply_sgr(DEFAULT_STYLE, [1, 31, 44])
        self.assertTrue(style.bold)
        self.assertEqual("#cd0000", style.foreground)
        self.assertEqual("#3465a4", style.background)

    def test_bright_colors_are_separate_from_bold(self) -> None:
        style = apply_sgr(DEFAULT_STYLE, [92])
        self.assertEqual("#55ff55", style.foreground)
        self.assertFalse(style.bold)

    def test_default_color_codes_clear_the_color(self) -> None:
        style = apply_sgr(apply_sgr(DEFAULT_STYLE, [31, 42]), [39, 49])
        self.assertEqual("", style.foreground)
        self.assertEqual("", style.background)

    def test_extended_colors_are_parsed(self) -> None:
        palette = apply_sgr(DEFAULT_STYLE, [38, 5, 196])
        self.assertEqual(color_256(196), palette.foreground)
        exact = apply_sgr(DEFAULT_STYLE, [48, 2, 10, 20, 30])
        self.assertEqual("#0a141e", exact.background)

    def test_truncated_extended_color_does_not_break_the_rest(self) -> None:
        style = apply_sgr(DEFAULT_STYLE, [38, 5])
        self.assertEqual("", style.foreground)

    def test_unknown_codes_are_ignored(self) -> None:
        self.assertEqual(DEFAULT_STYLE, apply_sgr(DEFAULT_STYLE, [53, 60]))

    def test_inverse_swaps_colors_only_when_rendered(self) -> None:
        style = apply_sgr(DEFAULT_STYLE, [31, 7])
        self.assertEqual("#cd0000", style.foreground)
        self.assertIn("background-color:#cd0000", style_to_css(style))


class RenderTests(unittest.TestCase):
    def test_default_style_has_no_markup(self) -> None:
        self.assertEqual("", style_to_css(DEFAULT_STYLE))

    def test_spaces_survive_the_html(self) -> None:
        chars = ["a", " ", "b"]
        self.assertEqual("a&nbsp;b", render_line(chars, [DEFAULT_STYLE] * 3))

    def test_markup_of_the_device_is_escaped(self) -> None:
        chars = list("<b>")
        self.assertEqual("&lt;b&gt;", render_line(chars, [DEFAULT_STYLE] * 3))

    def test_neighbours_with_one_style_share_a_span(self) -> None:
        red = CellStyle(foreground="#cd0000")
        line = render_line(["о", "к"], [red, red])
        self.assertEqual(1, line.count("<span"))
        self.assertIn("ок", line)

    def test_trailing_spaces_are_dropped(self) -> None:
        chars = ["x"] + [" "] * 20
        self.assertEqual("x", render_line(chars, [DEFAULT_STYLE] * 21))


class ScreenTests(unittest.TestCase):
    def screen(self, columns: int = 20, rows: int = 5) -> TerminalScreen:
        return TerminalScreen(columns, rows)

    def test_plain_text_is_printed(self) -> None:
        screen = self.screen()
        screen.feed("привет")
        self.assertEqual("привет", screen.screen_text()[0])

    def test_carriage_return_overwrites_the_line(self) -> None:
        """Так рисуется прогресс: `\\r` возвращает каретку, а не переводит строку."""
        screen = self.screen()
        screen.feed("10%\r20%")
        self.assertEqual("20%", screen.screen_text()[0])

    def test_line_feed_moves_to_the_next_row(self) -> None:
        screen = self.screen()
        screen.feed("один\r\nдва")
        self.assertEqual(["один", "два"], screen.screen_text()[:2])

    def test_backspace_erases_the_previous_character(self) -> None:
        screen = self.screen()
        screen.feed("abc\b \bd")
        self.assertEqual("abd", screen.screen_text()[0])

    def test_tab_stops_every_eight_columns(self) -> None:
        screen = self.screen()
        screen.feed("a\tb")
        self.assertEqual("a       b", screen.screen_text()[0])

    def test_long_line_wraps_to_the_next_row(self) -> None:
        screen = self.screen(columns=MIN_COLUMNS, rows=3)
        screen.feed("a" * (MIN_COLUMNS + 2))
        self.assertEqual(["a" * MIN_COLUMNS, "aa"], screen.screen_text()[:2])

    def test_output_below_the_last_row_scrolls_the_screen(self) -> None:
        screen = self.screen(columns=10, rows=MIN_ROWS)
        screen.feed("\r\n".join(str(number) for number in range(MIN_ROWS + 2)))
        self.assertEqual(["0", "1"], screen.text()[:2])
        self.assertEqual(str(MIN_ROWS + 1), screen.screen_text()[-1])

    def test_taken_scrollback_is_given_out_once(self) -> None:
        screen = self.screen(columns=10, rows=MIN_ROWS)
        screen.feed("\r\n".join(str(number) for number in range(MIN_ROWS + 1)))
        self.assertEqual(["0"], screen.take_scrollback())
        self.assertEqual([], screen.take_scrollback())

    def test_colors_reach_the_markup(self) -> None:
        screen = self.screen()
        screen.feed("\x1b[31mошибка\x1b[0m")
        self.assertIn("color:#cd0000", screen.render_screen())

    def test_style_stops_at_the_reset(self) -> None:
        screen = self.screen()
        screen.feed("\x1b[1mжирный\x1b[0mобычный")
        self.assertEqual(1, screen.render_screen().count("font-weight:bold"))

    def test_cursor_movement_rewrites_the_place(self) -> None:
        screen = self.screen()
        screen.feed("abcdef\x1b[1;1Hxy")
        self.assertEqual("xycdef", screen.screen_text()[0])

    def test_erase_line_removes_the_tail(self) -> None:
        screen = self.screen()
        screen.feed("длинная строка\r\x1b[7Cx\x1b[K")
        self.assertEqual("длиннаяx", screen.screen_text()[0])

    def test_erase_display_clears_everything(self) -> None:
        screen = self.screen()
        screen.feed("один\r\nдва\x1b[2J")
        self.assertEqual(["", ""], screen.screen_text()[:2])

    def test_unknown_sequence_leaves_no_garbage(self) -> None:
        screen = self.screen()
        screen.feed("\x1b[?25lтекст\x1b[?25h")
        self.assertEqual("текст", screen.screen_text()[0])

    def test_window_title_is_not_printed(self) -> None:
        screen = self.screen()
        screen.feed("\x1b]0;root@router\x07готово")
        self.assertEqual("готово", screen.screen_text()[0])

    def test_sequence_split_between_packets_is_assembled(self) -> None:
        """Чанк канала рвёт последовательность где угодно, включая середину."""
        screen = self.screen()
        screen.feed("\x1b[3")
        screen.feed("1mкрасный")
        self.assertEqual("красный", screen.screen_text()[0])
        self.assertIn("color:#cd0000", screen.render_screen())

    def test_multibyte_text_split_by_the_parser_is_kept(self) -> None:
        screen = self.screen()
        screen.feed("да")
        screen.feed("нет")
        self.assertEqual("данет", screen.screen_text()[0])

    def test_endless_garbage_does_not_grow_forever(self) -> None:
        screen = self.screen()
        screen.feed("\x1b[" + "1;" * 500)
        screen.feed("текст")
        self.assertIn("текст", screen.screen_text()[0])

    def test_resize_keeps_the_visible_text(self) -> None:
        screen = self.screen(columns=20, rows=6)
        screen.feed("строка")
        screen.resize(40, 10)
        self.assertEqual(40, screen.columns)
        self.assertEqual(10, screen.rows)
        self.assertEqual("строка", screen.screen_text()[0])

    def test_shrinking_keeps_the_cursor_on_screen(self) -> None:
        screen = self.screen(columns=20, rows=10)
        screen.feed("\r\n".join(str(number) for number in range(10)))
        screen.resize(20, MIN_ROWS)
        row, _column = screen.cursor
        self.assertEqual(MIN_ROWS, screen.rows)
        self.assertTrue(0 <= row < MIN_ROWS)
        self.assertEqual("9", screen.screen_text()[row])

    def test_screen_render_has_no_empty_tail(self) -> None:
        screen = self.screen(columns=10, rows=10)
        screen.feed("строка")
        self.assertNotIn("<br>", screen.render_screen())


if __name__ == "__main__":
    unittest.main()
