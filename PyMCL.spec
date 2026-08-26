# -*- mode: python ; coding: utf-8 -*-
"""Slim onefile spec: Fluent UI + terracotta + AI, drop unused Qt stacks."""
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

_ICON = "icon.ico" if Path("icon.ico").is_file() else None
_VERSION = "pack/file_version_info.txt" if Path("pack/file_version_info.txt").is_file() else None

_SKIP_SUBSTR = (
    "multimedia", "webengine", "image_utils",
)


def _hidden(pkg):
    return [
        name for name in collect_submodules(pkg)
        if not any(token in name.lower() for token in _SKIP_SUBSTR)
    ]


def _datas(pkg):
    rows = []
    for src, dest in collect_data_files(pkg):
        path = str(src).replace("\\", "/").lower()
        if any(token in path for token in _SKIP_SUBSTR):
            continue
        if path.endswith(".qm") and "zh" not in path:
            continue
        rows.append((src, dest))
    return rows


datas = _datas("qfluentwidgets") + _datas("qframelesswindow")
# 语言包是 JSON 数据文件，collect_submodules 带不进来
datas += [("mclauncher/locales", "mclauncher/locales")]
try:
    datas += collect_data_files("certifi")
except Exception:
    pass
hiddenimports = (
    _hidden("app")
    + _hidden("mclauncher")
    + _hidden("qfluentwidgets")
    + _hidden("qframelesswindow")
    + ["mclauncher.terracotta", "app.pages.multiplayer_page", "app.pages.ai_page",
       "mclauncher.feedback", "mclauncher.feedback_defaults", "mclauncher.sysinfo",
       "app.pages.feedback_page", "app.ui_alive", "app.pages.file_pick",
       "app.pages.install_wizard", "app.pages.first_run", "app.pages.global_mods_dialog",
       "keyring", "keyring.backends.Windows", "keyring.backends.fail",
       "keyring.backends.null", "keyring.backends.chainer"]
)

excludes = [
    "tkinter", "_tkinter", "turtle", "turtledemo", "test", "unittest",
    "pydoc", "doctest", "xmlrpc", "nntplib", "lib2to3", "ensurepip",
    "idlelib", "venv", "gui", "setuptools", "pkg_resources",
    "numpy", "PIL", "Pillow", "scipy", "matplotlib", "colorthief",
    "PySide6.Qt3DAnimation", "PySide6.Qt3DCore", "PySide6.Qt3DExtras",
    "PySide6.Qt3DInput", "PySide6.Qt3DLogic", "PySide6.Qt3DRender",
    "PySide6.QtBluetooth", "PySide6.QtCharts", "PySide6.QtDataVisualization",
    "PySide6.QtGraphs", "PySide6.QtHttpServer", "PySide6.QtLocation",
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets",
    "PySide6.QtNfc", "PySide6.QtPdf", "PySide6.QtPdfWidgets",
    "PySide6.QtPositioning", "PySide6.QtQml", "PySide6.QtQmlModels",
    "PySide6.QtQuick", "PySide6.QtQuick3D", "PySide6.QtQuickControls2",
    "PySide6.QtQuickWidgets", "PySide6.QtRemoteObjects", "PySide6.QtSensors",
    "PySide6.QtSerialBus", "PySide6.QtSerialPort", "PySide6.QtSpatialAudio",
    "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtTextToSpeech",
    "PySide6.QtWebChannel", "PySide6.QtWebEngine", "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineQuick", "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebSockets", "PySide6.QtWebView",
    "qfluentwidgets.multimedia", "qframelesswindow.webengine",
]

_DROP_IN_BUNDLE = (
    "webengine", "/qml/", "qt6quick", "qt6qml", "qt63d", "multimedia",
    "virtualkeyboard", "qt6pdf", "qt6labs", "shadertools", "webview",
    "webchannel", "websockets", "scxml", "remoteobjects", "bluetooth",
    "positioning", "sensors", "nfc", "serialport", "serialbus",
    "texttospeech", "httpserver", "spatialaudio", "charts",
    "datavisualization", "graphs", "designer", "qt6sql",
    "opengl32sw", "qtwebengine", "resources/icudtl",
    "resources/qtwebengine",
)


def _keep_bundle_path(dest):
    path = dest.replace("\\", "/").lower()
    if "translations/" in path and path.endswith(".qm"):
        return "zh_cn" in path or "zh_tw" in path
    return not any(token in path for token in _DROP_IN_BUNDLE)


a = Analysis(
    ["launcher_entry.py"],
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
a.binaries = [item for item in a.binaries if _keep_bundle_path(item[0])]
a.datas = [item for item in a.datas if _keep_bundle_path(item[0])]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="PyMCL",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=_ICON,
    version=_VERSION,
)
