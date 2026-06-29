from PyInstaller.utils.hooks import collect_submodules


hidden_imports = collect_submodules("keyring.backends")

a = Analysis(
    ["src/ven4control/app.py"],
    pathex=["src"],
    binaries=[],
    datas=[("assets/ven4control.ico", ".")],
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
