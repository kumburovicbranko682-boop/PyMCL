# -*- coding: utf-8 -*-
"""微软登录的设备码不该让人手抄。

钉住的行为：
- 设备码到达时自动复制进剪贴板，提示语明说「已复制、直接粘贴」；
- 代码文本允许鼠标选中（剪贴板被随后覆盖时还能手动复制）。

全程 offscreen、不 show 不 exec。
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYMCL_HOME", tempfile.mkdtemp(prefix="pymcl_test_code_"))

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mclauncher import feedback as _fb  # noqa: E402

_fb.start_heartbeat = lambda *a, **k: None
_fb.stop_heartbeat = lambda *a, **k: None

_app = None


def setUpModule():
    global _app
    from PySide6.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication([])


class DeviceCodeCopyTests(unittest.TestCase):
    def test_code_is_copied_and_selectable(self):
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtWidgets import QWidget
        from app.widgets import DeviceCodeDialog

        host = QWidget()
        host.resize(800, 600)
        dlg = DeviceCodeDialog(host)
        dlg.show_code("ABCD-EFGH", "https://www.microsoft.com/link")
        _app.processEvents()

        self.assertEqual(QGuiApplication.clipboard().text(), "ABCD-EFGH",
                         "设备码应自动复制进剪贴板")
        self.assertIn(tr_copied(), dlg.hint.text())
        self.assertTrue(dlg.code.textInteractionFlags() & Qt.TextSelectableByMouse,
                        "设备码文本应可鼠标选中")


def tr_copied():
    from mclauncher.i18n import tr
    return tr("代码已复制到剪贴板 — 点「打开浏览器」，在打开的页面直接粘贴：")


if __name__ == "__main__":
    unittest.main(verbosity=2)
