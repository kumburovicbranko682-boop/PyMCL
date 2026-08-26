# -*- coding: utf-8 -*-
"""启动页「任务摘要」卡片不许把刚完成的结果抹掉：
后端在任务结束时先发 finished（卡片显示 ✓ 结果），紧跟着发
task_count_changed(0)——以前这一下会把结果覆盖成「暂无任务」，
用户永远看不到最后一个任务干了什么。

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

from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

_app = QApplication.instance() or QApplication([])

from mclauncher.i18n import tr  # noqa: E402
from app.backend import BackendAPI  # noqa: E402
from app.pages.home_cards import TasksBody  # noqa: E402


class _FakePage:
    def __init__(self, backend):
        self.backend = backend


class TasksCardLastResultTests(unittest.TestCase):
    def _body(self):
        backend = BackendAPI(None)
        card = QWidget()
        body = TasksBody(_FakePage(backend), card, None)
        _app.processEvents()
        return backend, card, body

    def test_initially_no_tasks(self):
        _backend, card, body = self._body()
        try:
            self.assertEqual(body.last.text(), tr("暂无任务"))
        finally:
            card.deleteLater()

    def test_last_result_survives_count_zero(self):
        backend, card, body = self._body()
        try:
            # 模拟后端真实顺序：finished 先到，count=0 紧跟着到
            backend.finished.emit("t1", True, "已安装 1.21.1")
            backend.task_count_changed.emit(0)
            _app.processEvents()
            self.assertIn("✓", body.last.text(),
                          "任务全部结束后仍应显示最近一次结果")
            self.assertNotEqual(body.last.text(), tr("暂无任务"),
                                "count=0 不该把刚完成的结果抹成「暂无任务」")
        finally:
            card.deleteLater()

    def test_failure_shown(self):
        backend, card, body = self._body()
        try:
            backend.finished.emit("t1", False, "网络中断")
            backend.task_count_changed.emit(0)
            _app.processEvents()
            self.assertIn("✗", body.last.text())
        finally:
            card.deleteLater()


if __name__ == "__main__":
    unittest.main(verbosity=2)
