# -*- coding: utf-8 -*-
"""侧栏「编辑布局」不许在看不见的画布上默默开编辑模式。

此前从设置页/下载页点它：页面不切换、启动画布在后台进入编辑态，
按钮看起来像坏的，之后切回启动页还会莫名处于编辑模式。

钉住：在设置页点「编辑布局」→ 立即切到启动页，画布可见且在编辑态。
全程 offscreen + 临时数据目录，不弹任何窗口。
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYMCL_HOME", tempfile.mkdtemp(prefix="pymcl_test_editbtn_"))

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mclauncher import feedback as _fb  # noqa: E402

_fb.start_heartbeat = lambda *a, **k: None
_fb.stop_heartbeat = lambda *a, **k: None

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from app.main_window import MainWindow  # noqa: E402


def _make_window():
    win = MainWindow.__new__(MainWindow)
    win._boot_extras = lambda: None
    MainWindow.__init__(win)
    return win


class EditLayoutEntryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.win = _make_window()
        cls.win.resize(1180, 760)
        cls.win.show()
        _app.processEvents()

    @classmethod
    def tearDownClass(cls):
        cls.win.close()
        _app.processEvents()

    def test_edit_button_brings_canvas_into_view(self):
        win = self.win
        win.switchTo("settings")
        _app.processEvents()
        self.assertEqual(win._visible_key(), "settings")

        win.side.edit_btn.click()
        _app.processEvents()

        self.assertEqual(win._visible_key(), "launch",
                         "点「编辑布局」必须先切到启动页")
        self.assertTrue(win.launch_page.canvas.editing, "画布应进入编辑模式")
        self.assertTrue(win.launch_page.canvas.isVisible(),
                        "编辑模式必须开在看得见的画布上")

        win.launch_page.canvas.set_edit_mode(False)
        _app.processEvents()


if __name__ == "__main__":
    unittest.main(verbosity=2)
