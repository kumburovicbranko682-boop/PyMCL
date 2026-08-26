# -*- coding: utf-8 -*-
"""原版游戏页版本清单的三种状态必须说真话、给出路。

钉住的行为：
- 清单还在后台拉取时显示「正在获取版本列表…」，
  不谎报「没有匹配的版本」；
- 拉取失败且本地无缓存时，空状态带「重试」按钮，点了真的重发请求；
- 有清单但筛选无命中时，才显示「没有匹配的版本」。

全程 offscreen，call_async 打桩，不联网。
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYMCL_HOME", tempfile.mkdtemp(prefix="pymcl_test_vlist_"))

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
    CONFIG.save()
    from PySide6.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication([])


def _empty_texts(page):
    from app.widgets import EmptyState
    return [s._text for s in page.findChildren(EmptyState)]


class VersionListStateTests(unittest.TestCase):
    def _page(self, async_calls):
        from unittest import mock
        from app.backend import BackendAPI
        patches = [
            mock.patch.object(
                BackendAPI, "call_async",
                lambda self, fn, ok=None, err=None: async_calls.append((fn, ok, err))),
        ]
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])
        from app.pages.version_page import VersionPage
        return VersionPage(BackendAPI())

    def test_loading_state_not_no_match(self):
        calls = []
        page = self._page(calls)
        _app.processEvents()
        texts = _empty_texts(page)
        self.assertIn(tr("正在获取版本列表…"), texts)
        self.assertNotIn(tr("没有匹配的版本"), texts)

    def test_error_state_offers_working_retry(self):
        from app.widgets import EmptyState
        calls = []
        page = self._page(calls)
        _app.processEvents()
        self.assertEqual(len(calls), 1)
        # 后台拉取失败
        calls[0][2]("boom")
        _app.processEvents()
        err_state = next(
            (s for s in page.findChildren(EmptyState)
             if s.action_btn is not None), None)
        self.assertIsNotNone(err_state, "失败空状态应带重试按钮")
        err_state.action_btn.click()
        _app.processEvents()
        self.assertEqual(len(calls), 2, "点重试应重发清单请求")
        texts = _empty_texts(page)
        self.assertIn(tr("正在获取版本列表…"), texts)

    def test_no_match_only_with_data(self):
        calls = []
        page = self._page(calls)
        _app.processEvents()
        calls[0][1]([{"version": "1.20.1", "type": "release", "date": "2023-06-07"}])
        _app.processEvents()
        page.search.setText("zzz-不存在")
        page._refill()
        _app.processEvents()
        self.assertIn(tr("没有匹配的版本"), _empty_texts(page))


if __name__ == "__main__":
    unittest.main(verbosity=2)
