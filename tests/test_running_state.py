# -*- coding: utf-8 -*-
"""游戏跑起来之后，启动页状态必须说真话。

以前的行为：进度条停在 75%、状态一直是「游戏启动中」，哪怕游戏窗口
已经开了几个小时。钉住新行为：game_started 信号到达且本页启动仍挂着
时，进度到 100、状态写「游戏运行中」；不是本页发起的启动不乱动。

全程 offscreen，信号手动发射，不拉起任何进程。
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYMCL_HOME", tempfile.mkdtemp(prefix="pymcl_test_run_"))

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
    CONFIG.save()
    from PySide6.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication([])


class RunningStateTests(unittest.TestCase):
    def _page(self):
        from unittest import mock
        from app.backend import BackendAPI
        patches = [
            mock.patch.object(BackendAPI, "fetch_news", lambda self: []),
            mock.patch.object(BackendAPI, "cached_news", lambda self: []),
        ]
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])
        from app.pages.launch_page import LaunchPage
        backend = BackendAPI()
        page = LaunchPage(backend)
        _app.processEvents()
        return page, backend

    def test_game_started_updates_status(self):
        page, backend = self._page()
        page._task_id = "task-1"
        page.launch_btn.setEnabled(False)
        backend.game_started.emit()
        _app.processEvents()
        self.assertEqual(page.status_label.text(),
                         tr("游戏运行中 — 点「停止」可强制结束"))
        self.assertEqual(page.progress.value(), 100)

    def test_foreign_game_start_leaves_page_alone(self):
        page, backend = self._page()
        page._task_id = None
        page.status_label.setText(tr("就绪"))
        page.progress.setValue(0)
        backend.game_started.emit()
        _app.processEvents()
        self.assertEqual(page.status_label.text(), tr("就绪"))
        self.assertEqual(page.progress.value(), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
