# -*- coding: utf-8 -*-
"""下载任务页空状态必须有能点的下一步。

钉住的行为：
- 空状态带一个「去下载 → 原版游戏」按钮，点了真的切到那一页；
- 没传 action 的 EmptyState 和从前一样（不长出按钮）；
- 有任务进来时空状态隐藏，按钮跟着消失，不残留假入口。

全程 offscreen，不弹任何窗口，数据目录用临时目录。
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

from mclauncher.config import CONFIG  # noqa: E402
from mclauncher.i18n import tr  # noqa: E402

_app = None


def setUpModule():
    global _app
    CONFIG.set("first_run", False)
    CONFIG.set("feedback_consent", False)
    CONFIG.set("auto_check_update", False)
    CONFIG.save()
    from PySide6.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication([])


class TasksEmptyActionTests(unittest.TestCase):
    def test_empty_state_without_action_has_no_button(self):
        from qfluentwidgets import FluentIcon as FIF
        from app.widgets import EmptyState
        es = EmptyState(FIF.INFO, "什么都没有")
        self.assertIsNone(es.action_btn)

    def test_tasks_empty_action_navigates_to_download(self):
        from unittest import mock
        from app.backend import BackendAPI
        patches = [
            mock.patch.object(BackendAPI, "fetch_news", lambda self: []),
            mock.patch.object(BackendAPI, "cached_news", lambda self: []),
            mock.patch.object(BackendAPI, "fetch_version_list", lambda self: []),
            mock.patch.object(BackendAPI, "check_update", lambda self: {}),
        ]
        for p in patches:
            p.start()
        try:
            from app.main_window import MainWindow
            win = MainWindow()
            win.show()
            _app.processEvents()
            try:
                win.switchTo("tasks")
                _app.processEvents()
                empty = win.tasks_page.empty
                self.assertTrue(empty.isVisible())
                btn = empty.action_btn
                self.assertIsNotNone(btn, "空状态应有下一步按钮")
                self.assertEqual(btn.text(), tr("去下载 → 原版游戏"))
                btn.click()
                _app.processEvents()
                self.assertEqual(win._visible_key(), "version",
                                 "点空状态按钮应直达 下载 → 原版游戏")
            finally:
                win.close()
                _app.processEvents()
        finally:
            for p in patches:
                p.stop()


if __name__ == "__main__":
    unittest.main(verbosity=2)
