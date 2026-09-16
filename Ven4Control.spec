from PyInstaller.utils.hooks import collect_data_files, collect_submodules


# docx и openpyxl импортируются только в момент экспорта журнала, поэтому
# анализатор их не видит: перечисляем явно вместе с шаблоном документа docx.
hidden_imports = collect_submodules("keyring.backends") + ["docx", "openpyxl"]

a = Analysis(
    ["src/ven4control/app.py"],
    pathex=["src"],
    binaries=[],
    # Уведомление о лицензиях сторонних компонентов обязано ехать вместе с
    # бинарником: внутрь onefile попадают Qt/PySide6 (LGPL-3.0) и asyncssh
    # (EPL-2.0), а их условия требуют уведомить получателя сборки.
    datas=[
        ("assets/ven4control.ico", "."),
        ("THIRD-PARTY-NOTICES.md", "."),
    ]
    + collect_data_files("docx"),
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "PySide6.Qt3D", "PySide6.QtWebEngine"],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Ven4Control",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="assets/ven4control.ico",
    version="version_info.txt",
)
