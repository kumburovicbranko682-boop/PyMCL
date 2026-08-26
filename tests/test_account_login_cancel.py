# -*- coding: utf-8 -*-
"""账号页关掉设备码框必须取消后台的微软登录轮询。

启动页早有这个处理（关框 = 放弃登录，取消任务），账号页漏了：
轮询继续问微软要令牌直到超时，然后凭空弹「登录失败」。
钉住：关框后 cancel_task 被调用、_login_task 清空。

全程 offscreen，对话框 exec 打桩为直接拒绝，不弹窗、不联网。
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYMCL_HOME", tempfile.mkdtemp(prefix="pymcl_test_msl_"))

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mclauncher import feedback as _fb  # noqa: E402

_fb.start_heartbeat = lambda *a, **k: None
_fb.stop_heartbeat = lambda *a, **k: None

_app = None


def setUpModule():
    global _app
    from PySide6.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication([])


class AccountLoginCancelTests(unittest.TestCase):
    def test_closing_device_dialog_cancels_poll(self):
        from unittest import mock
        from app.backend import BackendAPI
        from app.widgets import DeviceCodeDialog

        cancelled = []
        patches = [
            mock.patch.object(
                BackendAPI, "start_microsoft_login", lambda self: "ms-task-1"),
            mock.patch.object(
                BackendAPI, "cancel_task",
                lambda self, tid: cancelled.append(tid)),
            # 模拟用户直接关掉设备码框（拒绝）
            mock.patch.object(DeviceCodeDialog, "exec", lambda self: 0),
        ]
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])

        from app.pages.account_page import AccountPage
        page = AccountPage(BackendAPI())
        _app.processEvents()
        page._ms()
        _app.processEvents()
        self.assertEqual(cancelled, ["ms-task-1"],
                         "关掉设备码框应取消后台登录任务")
        self.assertIsNone(page._login_task)


if __name__ == "__main__":
    unittest.main(verbosity=2)
