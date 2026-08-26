# -*- coding: utf-8 -*-
"""时长页没有任何记录时，「清除记录」不该还能点——
点了会对着空数据弹「不可恢复」确认框，确认后再报「已清除」，全程是假交互。

全程 offscreen + 临时数据目录，不弹任何窗口。
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYMCL_HOME", tempfile.mkdtemp(prefix="pymcl_test_pt_"))

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from app.backend import BackendAPI  # noqa: E402
from app.pages.playtime_page import PlaytimePage  # noqa: E402


class PlaytimeEmptyClearTests(unittest.TestCase):
    def _page(self, playtime: dict | None = None):
        backend = BackendAPI(None)
        backend.get_all_playtime = lambda: dict(playtime or {})
        page = PlaytimePage(backend)
        page.reload()
        _app.processEvents()
        return page

    def test_clear_disabled_when_no_records(self):
        page = self._page({})
        try:
            self.assertIs(page._body.currentWidget(), page.empty,
                          "无记录时应显示空状态")
            self.assertFalse(page.clear_btn.isEnabled(),
                             "无记录时「清除记录」不该能点")
            self.assertTrue(page.clear_btn.toolTip(),
                            "禁用时应有 tooltip 解释原因")
        finally:
            page.deleteLater()

    def test_clear_enabled_with_records(self):
        data = {"default": {"total": 3600, "versions": {"1.21.1": 3600}}}
        page = self._page(data)
        try:
            self.assertIsNot(page._body.currentWidget(), page.empty)
            self.assertTrue(page.clear_btn.isEnabled(),
                            "有记录时「清除记录」应可点")
        finally:
            page.deleteLater()

    def test_clear_disables_again_after_records_gone(self):
        holder = {"data": {"default": {"total": 60, "versions": {"1.21.1": 60}}}}
        backend = BackendAPI(None)
        backend.get_all_playtime = lambda: dict(holder["data"])
        page = PlaytimePage(backend)
        page.reload()
        _app.processEvents()
        try:
            self.assertTrue(page.clear_btn.isEnabled())
            holder["data"] = {}
            page.reload()
            _app.processEvents()
            self.assertFalse(page.clear_btn.isEnabled(),
                             "清空后按钮应回到禁用")
        finally:
            page.deleteLater()


if __name__ == "__main__":
    unittest.main(verbosity=2)
