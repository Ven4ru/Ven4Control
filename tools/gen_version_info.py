"""Генерирует из единственной константы версии всё, что её знает.

Два файла: version_info.txt для PyInstaller и installer/version.nsh для NSIS.
Оба раньше правились руками, и оба расходились с pyproject.toml — собранный EXE
представлялся не той версией, что на самом деле собрана.

Для установщика расхождение хуже, чем косметика. Имя ассета в релизе берётся из
той же версии, а приложение сравнивает свой `__version__` с тегом релиза: если
установщик назвали 0.6.0, а код внутри отчитывается как 0.5.0, обновлённое
приложение снова видит 0.6.0 новее себя и предлагает то же обновление
бесконечно.
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


NSH_TEMPLATE = """; Генерируется tools/gen_version_info.py — правки здесь будут перезаписаны.
!define VERSION "{version}"
"""


def main() -> None:
    major, minor, patch = (int(part) for part in __version__.split("."))
    root = pathlib.Path(__file__).resolve().parent.parent

    (root / "version_info.txt").write_text(
        TEMPLATE.format(major=major, minor=minor, patch=patch, version=__version__),
        encoding="utf-8",
        newline="\n",
    )
    # Имя ассета складывается из этой версии и обязано остаться вида
    # Ven4Control.Setup-X.Y.Z.exe: приложение ищет установщик по строгому
    # шаблону, и суффикс вроде «-beta» в имени файла оставил бы его без
    # обновлений навсегда. Поэтому сюда попадают только три числа, а суффикс
    # живёт в теге релиза.
    (root / "installer" / "version.nsh").write_text(
        NSH_TEMPLATE.format(version=__version__),
        encoding="utf-8",
        newline="\n",
    )
    print(f"version_info.txt и installer/version.nsh: {__version__}")


if __name__ == "__main__":
    main()
