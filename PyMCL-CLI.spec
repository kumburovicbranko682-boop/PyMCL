# -*- mode: python ; coding: utf-8 -*-
"""CLI onefile spec: mclauncher + locales, no Qt / Fluent UI."""
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

_ICON = "icon.ico" if Path("icon.ico").is_file() else None
_VERSION = (
    "pack/file_version_info_cli.txt"
    if Path("pack/file_version_info_cli.txt").is_file()
    else None
)

datas = [("mclauncher/locales", "mclauncher/locales")]
try:
    datas += collect_data_files("certifi")
except Exception:
    pass

hiddenimports = collect_submodules("mclauncher") + [
    "keyring",
    "keyring.backends.Windows",
    "keyring.backends.fail",
    "keyring.backends.null",
    "keyring.backends.chainer",
]

excludes = [
    "tkinter", "_tkinter", "turtle", "turtledemo", "test", "unittest",
    "pydoc", "doctest", "xmlrpc", "nntplib", "lib2to3", "ensurepip",
    "idlelib", "venv", "gui", "setuptools", "pkg_resources",
    "numpy", "PIL", "Pillow", "scipy", "matplotlib", "colorthief",
    "PySide6", "shiboken6", "qfluentwidgets", "qframelesswindow", "app",
]

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="PyMCL-CLI",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=_ICON,
    version=_VERSION,
)
