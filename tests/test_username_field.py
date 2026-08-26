# -*- coding: utf-8 -*-
"""启动页用户名字段：只在离线模式可编辑，离线名要跨重启记住。

后端 _launch_game_impl 只有 account == 离线模式 时才使用 username；
此前该输入框永远可编辑（正版账号下填了也被忽略），且硬编码
"Player"，重启即忘。全程 offscreen + 临时数据目录。
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYMCL_HOME", tempfile.mkdtemp(prefix="pymcl_test_user_"))

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from mclauncher.config import CONFIG  # noqa: E402
from mclauncher.i18n import tr  # noqa: E402
from app.backend import BackendAPI  # noqa: E402


def _make_page():
    from app.pages.launch_page import LaunchPage
    return LaunchPage(BackendAPI(None), None)


class UsernameFieldTests(unittest.TestCase):
    def test_remembers_offline_username(self):
        CONFIG.set("offline_username", "Steve0")
        page = _make_page()
        _app.processEvents()
        try:
            self.assertEqual(page.username_edit.text(), "Steve0",
                             "上次的离线用户名应该回填")
        finally:
            page.deleteLater()
            _app.processEvents()

    def test_typing_persists_username(self):
        CONFIG.set("offline_username", "Player")
        page = _make_page()
        _app.processEvents()
        try:
            page.username_edit.setText("Alex7")
            page._persist_launch_defaults_now()  # 冲刷防抖
            self.assertEqual(CONFIG.get("offline_username"), "Alex7")
        finally:
            page.deleteLater()
            _app.processEvents()

    def test_disabled_unless_offline(self):
        page = _make_page()
        _app.processEvents()
        try:
            self.assertTrue(page.username_edit.isEnabled(),
                            "默认离线模式下应可编辑")
            page.account_box.addItem("SomeMicrosoftUser")
            page.account_box.setCurrentText("SomeMicrosoftUser")
            _app.processEvents()
            self.assertFalse(page.username_edit.isEnabled(),
                             "正版账号选中时用户名会被后端忽略，必须禁用")
            self.assertTrue(page.username_edit.toolTip(), "禁用时要说明原因")
            page.account_box.setCurrentText(tr("离线模式"))
            _app.processEvents()
            self.assertTrue(page.username_edit.isEnabled())
        finally:
            page.deleteLater()
            _app.processEvents()


if __name__ == "__main__":
    unittest.main(verbosity=2)
