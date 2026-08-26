# -*- coding: utf-8 -*-
"""目录页搜索失败必须给「重试」，不能只甩一行裸异常。

钉住的行为：
- 搜索出错后，空状态带「重试」按钮；
- 点重试真的发起新一轮搜索（token 前进、进入「正在搜索…」态）。

全程 offscreen，错误路径直接注入，不联网。
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


class SearchErrorRetryTests(unittest.TestCase):
    def test_error_state_offers_working_retry(self):
        from unittest import mock
        from app.backend import BackendAPI
        from app.widgets import EmptyState

        with mock.patch.object(BackendAPI, "call_async", lambda self, *a, **k: None):
            from app.pages.catalog_page import ModPage
            backend = BackendAPI()
            page = ModPage(backend)
            _app.processEvents()

            token = page._search_token
            page._on_search_err(token, "connection reset")
            _app.processEvents()

            states = page.findChildren(EmptyState)
            err_state = next(
                (s for s in states if tr("重试") == getattr(s.action_btn, "text", lambda: "")()),
                None)
            self.assertIsNotNone(err_state, "搜索失败的空状态应带「重试」按钮")
            self.assertIn("connection reset", err_state._text)

            before = page._search_token
            err_state.action_btn.click()
            _app.processEvents()
            self.assertGreater(page._search_token, before,
                               "点「重试」应发起新一轮搜索")


if __name__ == "__main__":
    unittest.main(verbosity=2)
