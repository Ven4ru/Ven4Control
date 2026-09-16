"""Генерирует version_info.txt для PyInstaller из единственной константы версии.

Файл больше не правится руками: раньше номер версии в нём расходился с
pyproject.toml, и собранный EXE представлялся не той версией, что на самом
деле собрана.
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from ven4control._version import __version__  # noqa: E402

TEMPLATE = """VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({major}, {minor}, {patch}, 0),
    prodvers=({major}, {minor}, {patch}, 0),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0),
  ),
  kids=[
    StringFileInfo([
      StringTable(
        "041904B0",
        [
          StringStruct("CompanyName", "Ven4ru"),
          StringStruct("FileDescription", "Менеджер SSH-устройств"),
          StringStruct("FileVersion", "{version}"),
          StringStruct("InternalName", "Ven4Control"),
          StringStruct("OriginalFilename", "Ven4Control.exe"),
          StringStruct("ProductName", "Ven4Control"),
          StringStruct("ProductVersion", "{version}"),
        ],
      )
    ]),
    VarFileInfo([VarStruct("Translation", [0x419, 1200])]),
  ],
)
"""


def main() -> None:
    major, minor, patch = (int(part) for part in __version__.split("."))
    target = pathlib.Path(__file__).resolve().parent.parent / "version_info.txt"
    target.write_text(
        TEMPLATE.format(major=major, minor=minor, patch=patch, version=__version__),
        encoding="utf-8",
        newline="\n",
    )
    print(f"version_info.txt: {__version__}")


if __name__ == "__main__":
    main()
