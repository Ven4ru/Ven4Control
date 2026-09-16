import unittest

import ven4control
from ven4control._version import __version__ as raw_version


class VersionTests(unittest.TestCase):
    def test_version_is_a_plain_three_part_number(self) -> None:
        self.assertRegex(raw_version, r"^\d+\.\d+\.\d+$")

    def test_package_exposes_the_same_version(self) -> None:
        """В собранном EXE importlib.metadata недоступна и давала "0.0.0"."""
        self.assertEqual(raw_version, ven4control.__version__)
        self.assertNotEqual("0.0.0", ven4control.__version__)


if __name__ == "__main__":
    unittest.main()
