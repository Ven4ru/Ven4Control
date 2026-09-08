"""Экранирование строк для команд PowerShell.

Одна общая функция вместо копий по модулям: `scheduled_task.py` уже
экранировал PowerShell-строки для планировщика (`_quoted`), теперь та же
логика нужна `remote_control.py` для winget/Ven4Tools — переносится сюда
и переиспользуется в обоих местах, а не дублируется во второй раз.
"""
from __future__ import annotations


def quote(value: str) -> str:
    """Строка PowerShell в одинарных кавычках: внутри удваивается только '."""
    return "'" + value.replace("'", "''") + "'"
