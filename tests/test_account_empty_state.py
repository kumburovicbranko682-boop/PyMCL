# -*- coding: utf-8 -*-
"""账号页空状态必须说真话：没有账号时皮肤卡不许显示「Steve」装作已登录，
空列表提示要给下一步（登录或存离线名），并且文案不能只提正版/皮肤站——
这个列表其实收所有类型的账号。

全程 offscreen + 临时数据目录，不弹任何窗口、不联网。
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYMCL_HOME", tempfile.mkdtemp(prefix="pymcl_test_acct_empty_"))

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from mclauncher.i18n import tr  # noqa: E402
from app.backend import BackendAPI  # noqa: E402
from app.pages.account_page import AccountPage  # noqa: E402


class AccountEmptyStateTests(unittest.TestCase):
    def _page(self, rows=None):
        backend = BackendAPI(None)
        backend.get_account_rows = lambda: list(rows or [])
        page = AccountPage(backend)
        _app.processEvents()
        return page

    def test_no_accounts_shows_not_signed_in(self):
        page = self._page([])
        try:
            self.assertEqual(page.skin_name.text(), tr("未登录"),
                             "没有账号时不该冒出「Steve」装作已登录")
        finally:
            page.deleteLater()

    def test_empty_list_caption_offers_next_step(self):
        from qfluentwidgets import CaptionLabel
        page = self._page([])
        try:
            caps = []
            for i in range(page.list_box.count()):
                w = page.list_box.itemAt(i).widget()
                if isinstance(w, CaptionLabel):
                    caps.append(w.text())
            self.assertTrue(caps, "空列表应有提示文字")
            text = " ".join(caps)
            self.assertIn(tr("还没有账号。用下方任意方式登录，或填一个离线角色名保存。"),
                          text, "空状态提示应告诉用户下一步做什么")
        finally:
            page.deleteLater()

    def test_active_account_name_shown(self):
        rows = [{"name": "Alice", "type": "offline", "uuid": "u", "api": "",
                 "avatar": "", "body": "", "active": True}]
        page = self._page(rows)
        try:
            self.assertEqual(page.skin_name.text(), "Alice")
        finally:
            page.deleteLater()


if __name__ == "__main__":
    unittest.main(verbosity=2)
