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
