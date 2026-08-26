# -*- coding: utf-8 -*-
"""版本列表拉取失败不再是死胡同：空状态必须带「重试」，点了真的重新拉。

全程 offscreen + 临时数据目录；call_async 打桩记录调用，不起线程不联网。
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYMCL_HOME", tempfile.mkdtemp(prefix="pymcl_test_retry_"))

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from app.backend import BackendAPI  # noqa: E402
from app.widgets import EmptyState  # noqa: E402
from mclauncher.i18n import tr  # noqa: E402


class VersionPageRetryTests(unittest.TestCase):
    def test_fetch_error_offers_retry_that_refetches(self):
        backend = BackendAPI(None)
        calls: list = []
        backend.call_async = lambda fn, ok, err=None: calls.append(fn)

        from app.pages.version_page import VersionPage

        page = VersionPage(backend, None)
        _app.processEvents()
        self.assertEqual(len(calls), 1, "构造时应发起一次版本清单拉取")
        self.assertEqual(calls[0], backend.fetch_version_list)

        # 模拟拉取失败（干净目录里没有磁盘缓存 → 走空状态分支）
        page._on_versions_err("模拟断网")
        _app.processEvents()

        # deleteLater 的旧空状态可能还没销毁，找带动作按钮的那个
        empties = page.grid_host.findChildren(EmptyState)
        self.assertTrue(empties, "失败后应显示空状态")
        btn = next((e.action_btn for e in empties if e.action_btn is not None), None)
        self.assertIsNotNone(btn, "失败空状态必须带动作按钮")
        self.assertEqual(btn.text(), tr("重试"))

        btn.click()
        _app.processEvents()
        self.assertEqual(len(calls), 2, "点「重试」应重新发起拉取")
        self.assertEqual(calls[1], backend.fetch_version_list)

        page.deleteLater()
        _app.processEvents()

    def test_plain_empty_state_has_no_button(self):
        from qfluentwidgets import FluentIcon as FIF

        es = EmptyState(FIF.SEARCH, "没有匹配的版本")
        self.assertIsNone(es.action_btn)
        es.deleteLater()


if __name__ == "__main__":
    unittest.main(verbosity=2)
