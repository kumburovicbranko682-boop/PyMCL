# -*- coding: utf-8 -*-
"""版本菜单「导出启动脚本」：点了必须看得出任务已经开始。

钉住的行为：
- 菜单项走 _export_script 包装（真的调 export_launch_script，不是裸调后再无声）；
- 成功发起后出 InfoBar，告知进度在「下载任务」、文件落在 exports 文件夹；
- 后端抛错时走错误提示（打桩 MessageBox，不弹窗）。

全程 offscreen，版本清单拉取与后端打桩，不联网。
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYMCL_HOME", tempfile.mkdtemp(prefix="pymcl_test_bat_"))

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mclauncher import feedback as _fb  # noqa: E402

_fb.start_heartbeat = lambda *a, **k: None
_fb.stop_heartbeat = lambda *a, **k: None

from mclauncher.i18n import tr  # noqa: E402

_app = None


def setUpModule():
    global _app
    from PySide6.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication([])


class ExportScriptFeedbackTests(unittest.TestCase):
    def _page(self, export_raises=False):
        from unittest import mock
        from app.backend import BackendAPI

        exports = []

        def fake_export(self, inst, ver, dest=""):
            if export_raises:
                raise RuntimeError("boom")
            exports.append((inst, ver))
            return "task-1"

        patches = [
            mock.patch.object(
                BackendAPI, "call_async",
                lambda self, fn, ok=None, err=None: None),
            mock.patch.object(BackendAPI, "export_launch_script", fake_export),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        from app.pages.version_page import VersionPage
        page = VersionPage(BackendAPI())
        _app.processEvents()
        return page, exports

    def test_menu_routes_through_wrapper(self):
        src = (ROOT / "app" / "pages" / "version_page.py").read_text(encoding="utf-8")
        self.assertIn("self._export_script(instance, version)", src,
                      "菜单项必须走带反馈的包装")
        self.assertNotIn(
            "lambda: self.backend.export_launch_script", src,
            "不要退回菜单里裸调后端、点了无声的写法")

    def test_export_starts_with_notification(self):
        from qfluentwidgets import InfoBar
        page, exports = self._page()
        page._export_script("default", "1.20.1")
        _app.processEvents()
        self.assertEqual(exports, [("default", "1.20.1")])
        self.assertTrue(page.findChildren(InfoBar),
                        "任务开始必须有可见反馈")

    def test_export_error_shows_error_not_success(self):
        from unittest import mock
        boxes = []

        class FakeBox:
            def __init__(self, title, msg, parent=None):
                boxes.append((title, msg))

            def exec(self):
                return True

        page, exports = self._page(export_raises=True)
        with mock.patch("app.pages.version_page.MessageBox", FakeBox):
            page._export_script("default", "1.20.1")
        _app.processEvents()
        self.assertEqual(exports, [])
        self.assertEqual(len(boxes), 1, "失败时应有错误提示")
        self.assertEqual(boxes[0][0], tr("导出失败"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
