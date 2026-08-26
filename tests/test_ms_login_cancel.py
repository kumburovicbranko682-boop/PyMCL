# -*- coding: utf-8 -*-
"""账号页微软登录：关掉设备码对话框必须取消后台轮询任务。

启动页 _login 早修过同一个坑（不取消则任务一直向微软要令牌直到超时，
再点登录还会撞上旧任务回调），账号页此前漏了。
对话框 exec 打桩返回「关闭」，不弹任何窗口。
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYMCL_HOME", tempfile.mkdtemp(prefix="pymcl_test_mscancel_"))

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from app.backend import BackendAPI  # noqa: E402
from app.pages import account_page as ap  # noqa: E402
from app.pages import launch_page as lp_mod  # noqa: E402


class _StubDialog:
    """顶替 DeviceCodeDialog：构造即返回，exec 模拟用户直接关掉。"""

    def __init__(self, *a, **k):
        pass

    def exec(self):
        return 0

    def show_code(self, *a):
        pass

    def show_status(self, *a):
        pass

    def accept(self):
        pass


class _Backend(BackendAPI):
    def __init__(self):
        super().__init__(None)
        self.cancelled: list = []

    def start_microsoft_login(self):
        return "task-ms-1"

    def cancel_task(self, task_id):
        self.cancelled.append(task_id)


class MsLoginCancelTests(unittest.TestCase):
    def test_account_page_cancels_on_dialog_close(self):
        backend = _Backend()
        orig = ap.DeviceCodeDialog
        ap.DeviceCodeDialog = _StubDialog
        try:
            page = ap.AccountPage(backend, None)
            _app.processEvents()
            page._ms()
            self.assertEqual(backend.cancelled, ["task-ms-1"],
                             "关掉设备码框必须取消后台登录任务")
            self.assertIsNone(page._login_task)
            page.deleteLater()
            _app.processEvents()
        finally:
            ap.DeviceCodeDialog = orig

    def test_launch_page_still_cancels(self):
        backend = _Backend()
        orig = lp_mod.DeviceCodeDialog
        lp_mod.DeviceCodeDialog = _StubDialog
        try:
            page = lp_mod.LaunchPage(backend, None)
            _app.processEvents()
            page._login()
            self.assertEqual(backend.cancelled, ["task-ms-1"])
            page.deleteLater()
            _app.processEvents()
        finally:
            lp_mod.DeviceCodeDialog = orig


if __name__ == "__main__":
    unittest.main(verbosity=2)
