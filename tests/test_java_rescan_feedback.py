# -*- coding: utf-8 -*-
"""Java 页「重新检测」：扫描时必须看得见在忙，完成时必须有个结果。

钉住的行为：
- 点「重新检测」后按钮立刻禁用并显示「检测中…」（扫盘要花好几秒，
  之前毫无反馈，只能再点一次赌它有没有听见）；
- 扫描期间再点不会叠加第二次扫描；
- 扫描完成：按钮恢复、列表刷新、出一条「检测完成」提示；
- 扫描失败：按钮同样恢复，不会永远卡在「检测中…」。

全程 offscreen，后台扫描打桩（回调由测试手动触发），不扫真机、不联网。
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYMCL_HOME", tempfile.mkdtemp(prefix="pymcl_test_java_"))

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mclauncher import feedback as _fb  # noqa: E402

_fb.start_heartbeat = lambda *a, **k: None
_fb.stop_heartbeat = lambda *a, **k: None

from mclauncher.i18n import tr  # noqa: E402

_app = None


def setUpModule():
    global _app
    from PySide6.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication([])


class JavaRescanFeedbackTests(unittest.TestCase):
    def _page(self):
        from unittest import mock
        from app.backend import BackendAPI

        async_calls = []

        def fake_call_async(self, fn, ok=None, err=None, **kw):
            async_calls.append({"fn": fn, "ok": ok, "err": err})

        patches = [
            mock.patch.object(
                BackendAPI, "get_java_list", lambda self, scan_system=False: []),
            mock.patch.object(BackendAPI, "call_async", fake_call_async),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

        from app.pages.java_page import JavaPage
        page = JavaPage(BackendAPI())
        _app.processEvents()
        return page, async_calls

    def test_rescan_shows_busy_and_blocks_reentry(self):
        page, calls = self._page()
        self.assertEqual(page.refresh_btn.text(), tr("重新检测"))
        self.assertTrue(page.refresh_btn.isEnabled())

        page.refresh_btn.click()
        _app.processEvents()
        self.assertEqual(len(calls), 1, "点一次应该只发起一次扫描")
        self.assertFalse(page.refresh_btn.isEnabled(),
                         "扫描期间按钮应禁用，否则看起来像没反应")
        self.assertEqual(page.refresh_btn.text(), tr("检测中…"))

        # 忙碌中直接调 reload 也不会叠加扫描
        page.reload(scan_system=True)
        _app.processEvents()
        self.assertEqual(len(calls), 1, "扫描中再触发不该叠加第二次")

    def test_scan_done_restores_and_reports(self):
        from qfluentwidgets import InfoBar
        page, calls = self._page()
        page.refresh_btn.click()
        _app.processEvents()

        calls[0]["ok"]([{"name": "Temurin 17", "major": "17", "path": "/opt/j17"}])
        _app.processEvents()
        self.assertTrue(page.refresh_btn.isEnabled(), "扫描完成后按钮应恢复")
        self.assertEqual(page.refresh_btn.text(), tr("重新检测"))
        self.assertTrue(page.findChildren(InfoBar),
                        "扫描完成应有可见的结果提示")
        # 列表被刷新为扫描结果
        widgets = [page.env_layout.itemAt(i).widget()
                   for i in range(page.env_layout.count())]
        from app.pages.java_page import JavaCard
        self.assertTrue(any(isinstance(w, JavaCard) for w in widgets),
                        "扫描到的 Java 应显示为卡片")

    def test_scan_error_restores_button(self):
        page, calls = self._page()
        page.refresh_btn.click()
        _app.processEvents()
        calls[0]["err"](RuntimeError("boom"))
        _app.processEvents()
        self.assertTrue(page.refresh_btn.isEnabled(),
                        "扫描失败后按钮不能永远卡在「检测中…」")
        self.assertEqual(page.refresh_btn.text(), tr("重新检测"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
