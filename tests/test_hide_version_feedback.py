# -*- coding: utf-8 -*-
"""隐藏版本不许无声消失：点「隐藏 / 取消隐藏」后必须告诉用户发生了什么、
去哪找回来——不知道「显示隐藏」复选框的人会以为版本被删了。

全程 offscreen + 临时数据目录，不弹任何窗口。
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYMCL_HOME", tempfile.mkdtemp(prefix="pymcl_test_hide_"))

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from qfluentwidgets import InfoBar  # noqa: E402

from mclauncher.i18n import tr  # noqa: E402
from app.backend import BackendAPI  # noqa: E402
from app.pages.version_page import VersionPage  # noqa: E402


def _page(hidden_now: bool):
    backend = BackendAPI(None)
    backend.call_async = lambda fn, ok, err=None: None
    backend.get_installed_versions = lambda inst, include_hidden=False: []
    backend.get_version_settings = lambda inst, ver: {"hidden": hidden_now}
    calls = []
    backend.hide_version = lambda inst, ver, on: calls.append((inst, ver, on))
    page = VersionPage(backend, None)
    _app.processEvents()
    return page, calls


class HideVersionFeedbackTests(unittest.TestCase):
    def test_hide_shows_how_to_get_it_back(self):
        page, calls = _page(hidden_now=False)
        try:
            page._show_hidden = False
            page._hide("default", "1.21.1")
            _app.processEvents()
            self.assertEqual(calls, [("default", "1.21.1", True)],
                             "应真调 backend.hide_version 去隐藏")
            bars = page.findChildren(InfoBar)
            self.assertTrue(bars, "隐藏后应有提示，不能无声消失")
            titles = " ".join(b.title for b in bars)
            contents = " ".join(b.content for b in bars)
            self.assertIn("1.21.1", titles)
            self.assertIn(tr("勾选右上角「显示隐藏」可以再看到它"), contents,
                          "提示必须告诉用户去哪找回来")
        finally:
            page.deleteLater()
            _app.processEvents()

    def test_unhide_confirms(self):
        page, calls = _page(hidden_now=True)
        try:
            page._hide("default", "1.21.1")
            _app.processEvents()
            self.assertEqual(calls, [("default", "1.21.1", False)],
                             "应真调 backend.hide_version 取消隐藏")
            bars = page.findChildren(InfoBar)
            self.assertTrue(bars, "取消隐藏也该有确认提示")
            titles = " ".join(b.title for b in bars)
            self.assertIn("1.21.1", titles)
        finally:
            page.deleteLater()
            _app.processEvents()


if __name__ == "__main__":
    unittest.main(verbosity=2)
