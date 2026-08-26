# -*- coding: utf-8 -*-
"""启动页要记住上次用的离线用户名。

钉住的行为：
- 点「启动游戏」时把输入的用户名写进 last_username；
- 重建启动页后用户名框恢复上次的名字，而不是固定的 Player。

全程 offscreen，launch_game / preflight 打桩，不真的拉起游戏。
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYMCL_HOME", tempfile.mkdtemp(prefix="pymcl_test_name_"))

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mclauncher import feedback as _fb  # noqa: E402

_fb.start_heartbeat = lambda *a, **k: None
_fb.stop_heartbeat = lambda *a, **k: None

from mclauncher.config import CONFIG  # noqa: E402

_app = None


def setUpModule():
    global _app
    CONFIG.set("first_run", False)
    CONFIG.set("feedback_consent", False)
    CONFIG.set("auto_check_update", False)
    CONFIG.save()
    from PySide6.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication([])


class RememberUsernameTests(unittest.TestCase):
    def test_launch_saves_and_new_page_restores(self):
        from unittest import mock
        from app.backend import BackendAPI

        launched = []
        patches = [
            mock.patch.object(BackendAPI, "fetch_news", lambda self: []),
            mock.patch.object(BackendAPI, "cached_news", lambda self: []),
            mock.patch.object(
                BackendAPI, "preflight_launch",
                lambda self, **kw: {"ok": True, "items": []}),
            mock.patch.object(
                BackendAPI, "launch_game",
                lambda self, **kw: launched.append(kw) or "task-1"),
        ]
        for p in patches:
            p.start()
        try:
            from app.pages.launch_page import LaunchPage
            backend = BackendAPI()
            page = LaunchPage(backend)
            _app.processEvents()
            page.version_box.addItem("1.20.1")
            page.version_box.setCurrentText("1.20.1")
            page.username_edit.setText("Notch")
            page._on_launch()
            _app.processEvents()
            self.assertEqual(len(launched), 1)
            self.assertEqual(CONFIG.get("last_username"), "Notch")

            page2 = LaunchPage(backend)
            _app.processEvents()
            self.assertEqual(page2.username_edit.text(), "Notch",
                             "重建启动页后应恢复上次的用户名")
        finally:
            for p in patches:
                p.stop()


if __name__ == "__main__":
    unittest.main(verbosity=2)
