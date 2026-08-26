# -*- coding: utf-8 -*-
"""Java 页空状态：缓存为空时自动补一轮系统扫描，空状态自带「重新检测」。

以前进页永远只读缓存（scan_system=False），机器上装了 Java 的人也会
看到「未检测到 Java，请从下方下载」。后端打桩，不扫真实磁盘。
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

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from app.backend import BackendAPI  # noqa: E402
from app.widgets import EmptyState  # noqa: E402
from mclauncher.i18n import tr  # noqa: E402


class _Backend(BackendAPI):
    def __init__(self, system_javas):
        super().__init__(None)
        self.system_javas = system_javas
        self.async_calls: list = []

    def get_java_list(self, scan_system: bool = False):
        return list(self.system_javas) if scan_system else []

    def call_async(self, fn, ok, err=None):
        self.async_calls.append((fn, ok, err))


class JavaPageScanTests(unittest.TestCase):
    def _page(self, backend):
        from app.pages.java_page import JavaPage
        return JavaPage(backend, None)

    def test_empty_cache_triggers_auto_scan(self):
        backend = _Backend([{"major": 17, "path": "/usr/bin/java"}])
        page = self._page(backend)
        _app.processEvents()
        try:
            self.assertTrue(backend.async_calls,
                            "缓存为空时应自动发起一次系统扫描")
            fn, ok, _err = backend.async_calls[-1]
            ok(fn())  # 模拟扫描完成
            _app.processEvents()
            from app.pages.java_page import JavaCard
            self.assertTrue(page.findChildren(JavaCard),
                            "扫描结果应显示为 Java 卡片")
        finally:
            page.deleteLater()
            _app.processEvents()

    def test_empty_state_offers_rescan(self):
        backend = _Backend([])
        page = self._page(backend)
        _app.processEvents()
        try:
            empties = page.findChildren(EmptyState)
            btn = next((e.action_btn for e in empties
                        if e.action_btn is not None), None)
            self.assertIsNotNone(btn, "空状态必须带「重新检测」")
            self.assertEqual(btn.text(), tr("重新检测"))
            before = len(backend.async_calls)
            btn.click()
            _app.processEvents()
            self.assertGreater(len(backend.async_calls), before,
                               "点「重新检测」应发起系统扫描")
        finally:
            page.deleteLater()
            _app.processEvents()


if __name__ == "__main__":
    unittest.main(verbosity=2)
