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

        # 空状态分支要求 _all_versions 为空。全量跑测试时所有模块共享
        # 同一个 PYMCL_HOME（setdefault 只有第一个生效），前面测试的后台
        # 线程可能已把版本清单缓存写进共享目录，构造时读到缓存就不为空。
        # 这里显式清空，保证测的是「没有任何版本数据 + 拉取失败」这条路。
        page._all_versions = []
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


class CatalogSearchRetryTests(unittest.TestCase):
    def test_search_error_offers_retry_that_searches_again(self):
        backend = BackendAPI(None)
        calls: list = []
        backend.call_async = lambda fn, ok, err=None: calls.append(fn)

        from app.pages.catalog_page import ModPage

        page = ModPage(backend, None)
        _app.processEvents()
        page._search()
        base = len(calls)
        self.assertGreaterEqual(base, 1, "搜索应通过 call_async 发起")

        page._on_search_err(page._search_token, "模拟断网")
        _app.processEvents()
        empties = page.findChildren(EmptyState)
        btn = next((e.action_btn for e in empties if e.action_btn is not None), None)
        self.assertIsNotNone(btn, "搜索失败空状态必须带重试按钮")
        self.assertEqual(btn.text(), tr("重试"))

        btn.click()
        _app.processEvents()
        self.assertGreater(len(calls), base, "点「重试」应重新发起搜索")

        page.deleteLater()
        _app.processEvents()


if __name__ == "__main__":
    unittest.main(verbosity=2)
