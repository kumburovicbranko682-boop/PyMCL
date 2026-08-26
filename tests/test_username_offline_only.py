# -*- coding: utf-8 -*-
"""用户名框只对离线模式生效，选在线账号时不能装作有用。

钉住的行为：
- 选中在线账号（微软/皮肤站等）时用户名输入框被禁用并给出解释性
  tooltip——后端此时用账号自己的名字，改这个框毫无效果；
- 切回「离线模式」立刻恢复可编辑；
- 启动页重建（reload）后状态依然正确。

全程 offscreen，账号列表打桩，不联网、不弹窗。
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYMCL_HOME", tempfile.mkdtemp(prefix="pymcl_test_uname_"))

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


class UsernameOfflineOnlyTests(unittest.TestCase):
    def test_online_account_disables_username(self):
        from unittest import mock
        from app.backend import BackendAPI

        patches = [
            mock.patch.object(BackendAPI, "fetch_news", lambda self: []),
            mock.patch.object(BackendAPI, "cached_news", lambda self: []),
            mock.patch.object(
                BackendAPI, "get_accounts",
                lambda self: [tr("离线模式"), "Steve"]),
            mock.patch.object(
                BackendAPI, "get_account_rows",
                lambda self: [{"name": "Steve", "active": False, "kind": "microsoft"}]),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

        from app.pages.launch_page import LaunchPage
        backend = BackendAPI()
        page = LaunchPage(backend)
        _app.processEvents()
        page.reload()
        _app.processEvents()

        page.account_box.setCurrentText(tr("离线模式"))
        _app.processEvents()
        self.assertTrue(page.username_edit.isEnabled(),
                        "离线模式下用户名必须可编辑")

        page.account_box.setCurrentText("Steve")
        _app.processEvents()
        self.assertFalse(page.username_edit.isEnabled(),
                         "在线账号下用户名不生效，必须禁用而不是装作有用")
        self.assertTrue(page.username_edit.toolTip(),
                        "禁用时要有 tooltip 解释原因")

        page.account_box.setCurrentText(tr("离线模式"))
        _app.processEvents()
        self.assertTrue(page.username_edit.isEnabled(),
                        "切回离线模式应恢复可编辑")


if __name__ == "__main__":
    unittest.main(verbosity=2)
