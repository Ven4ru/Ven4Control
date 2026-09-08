import unittest

from PySide6.QtCore import Qt

from ven4control.terminal_dialog import key_sequence


NONE = Qt.KeyboardModifier.NoModifier
CONTROL = Qt.KeyboardModifier.ControlModifier
SHIFT = Qt.KeyboardModifier.ShiftModifier
ALT = Qt.KeyboardModifier.AltModifier


class KeySequenceTests(unittest.TestCase):
    def test_printable_character_is_sent_as_is(self) -> None:
        self.assertEqual("a", key_sequence(Qt.Key.Key_A, NONE, "a"))
        self.assertEqual("A", key_sequence(Qt.Key.Key_A, SHIFT, "A"))
        self.assertEqual("щ", key_sequence(Qt.Key.Key_O, NONE, "щ"))

    def test_enter_sends_a_carriage_return(self) -> None:
        """Оболочка ждёт возврата каретки: с переводом строки команда не идёт."""
        self.assertEqual("\r", key_sequence(Qt.Key.Key_Return, NONE, "\r"))
        self.assertEqual("\r", key_sequence(Qt.Key.Key_Enter, NONE, "\r"))

    def test_backspace_sends_delete(self) -> None:
        self.assertEqual("\x7f", key_sequence(Qt.Key.Key_Backspace, NONE, "\b"))

    def test_tab_is_completion_not_a_widget_jump(self) -> None:
        self.assertEqual("\t", key_sequence(Qt.Key.Key_Tab, NONE, "\t"))
        self.assertEqual("\x1b[Z", key_sequence(Qt.Key.Key_Backtab, SHIFT, ""))

    def test_arrows_walk_the_history(self) -> None:
        self.assertEqual("\x1b[A", key_sequence(Qt.Key.Key_Up, NONE, ""))
        self.assertEqual("\x1b[B", key_sequence(Qt.Key.Key_Down, NONE, ""))
        self.assertEqual("\x1b[C", key_sequence(Qt.Key.Key_Right, NONE, ""))
        self.assertEqual("\x1b[D", key_sequence(Qt.Key.Key_Left, NONE, ""))

    def test_navigation_keys_are_translated(self) -> None:
        self.assertEqual("\x1b[H", key_sequence(Qt.Key.Key_Home, NONE, ""))
        self.assertEqual("\x1b[F", key_sequence(Qt.Key.Key_End, NONE, ""))
        self.assertEqual("\x1b[3~", key_sequence(Qt.Key.Key_Delete, NONE, ""))
        self.assertEqual("\x1b[5~", key_sequence(Qt.Key.Key_PageUp, NONE, ""))

    def test_function_keys_are_translated(self) -> None:
        self.assertEqual("\x1bOP", key_sequence(Qt.Key.Key_F1, NONE, ""))
        self.assertEqual("\x1b[24~", key_sequence(Qt.Key.Key_F12, NONE, ""))

    def test_control_letters_become_control_codes(self) -> None:
        self.assertEqual("\x03", key_sequence(Qt.Key.Key_C, CONTROL, "\x03"))
        self.assertEqual("\x04", key_sequence(Qt.Key.Key_D, CONTROL, "\x04"))
        self.assertEqual("\x1a", key_sequence(Qt.Key.Key_Z, CONTROL, "\x1a"))

    def test_control_symbols_are_translated(self) -> None:
        self.assertEqual("\x00", key_sequence(Qt.Key.Key_Space, CONTROL, ""))
        self.assertEqual("\x1c", key_sequence(Qt.Key.Key_Backslash, CONTROL, ""))
        self.assertEqual("\x1f", key_sequence(Qt.Key.Key_Underscore, CONTROL, ""))

    def test_escape_is_sent(self) -> None:
        self.assertEqual("\x1b", key_sequence(Qt.Key.Key_Escape, NONE, "\x1b"))

    def test_alt_works_as_meta(self) -> None:
        self.assertEqual("\x1bb", key_sequence(Qt.Key.Key_B, ALT, "b"))

    def test_modifier_alone_sends_nothing(self) -> None:
        self.assertEqual("", key_sequence(Qt.Key.Key_Shift, SHIFT, ""))
        self.assertEqual("", key_sequence(Qt.Key.Key_Control, CONTROL, ""))

    def test_unknown_key_without_text_sends_nothing(self) -> None:
        self.assertEqual("", key_sequence(Qt.Key.Key_CapsLock, NONE, ""))


if __name__ == "__main__":
    unittest.main()
