"""Имя текущего пользователя Windows для ACL и планировщика задач.

Область берётся из имени компьютера, а не из `USERDOMAIN`: на машине вне
домена там лежит имя рабочей группы (`WORKGROUP`), которое ни `icacls`, ни
планировщик задач не сопоставляют с учётной записью. Имя компьютера
совпадает с тем, что показывает `whoami`, и резолвится всегда.
"""
from __future__ import annotations

import os


def current_user_principal() -> str:
    name = os.environ.get("USERNAME", "")
    if not name:
        import getpass

        name = getpass.getuser()
    computer = os.environ.get("COMPUTERNAME", "")
    if computer:
        return f"{computer}\\{name}"
    return name
