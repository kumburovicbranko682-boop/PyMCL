# -*- coding: utf-8 -*-
"""任务页空状态：必须带「去下载」按钮，点了真的落到下载区。

全程 offscreen + 临时数据目录，不弹任何窗口。
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYMCL_HOME", tempfile.mkdtemp(prefix="pymcl_test_tasks_"))

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mclauncher import feedback as _fb  # noqa: E402

_fb.start_heartbeat = lambda *a, **k: None
_fb.stop_heartbeat = lambda *a, **k: None

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from app.main_window import MainWindow  # noqa: E402
from mclauncher.i18n import tr  # noqa: E402


class TasksEmptyStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        win = MainWindow.__new__(MainWindow)
        win._boot_extras = lambda: None
        MainWindow.__init__(win)
        win.resize(1180, 760)
        win.show()
        _app.processEvents()
        cls.win = win

    @classmethod
    def tearDownClass(cls):
        cls.win.close()
        _app.processEvents()

    def test_empty_state_action_goes_to_download(self):
        win = self.win
        win.switchTo("tasks")
        _app.processEvents()
        empty = win.tasks_page.empty
        self.assertTrue(empty.isVisible(), "无任务时应显示空状态")
        self.assertIsNotNone(empty.action_btn, "空状态必须有下一步按钮")
        self.assertEqual(empty.action_btn.text(), tr("去下载"))

        empty.action_btn.click()
        _app.processEvents()
        self.assertEqual(win.stackedWidget.currentWidget(), win.download_section,
                         "点「去下载」应直接落到下载区")

    def test_empty_copy_uses_sidebar_names(self):
        text = self.win.tasks_page.empty._text
        self.assertIn(tr("原版游戏"), text, "空状态叫法要和下载横条一致")


if __name__ == "__main__":
    unittest.main(verbosity=2)
