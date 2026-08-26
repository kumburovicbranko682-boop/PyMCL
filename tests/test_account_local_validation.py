# -*- coding: utf-8 -*-
"""皮肤站 / 统一通行证登录：空凭据在本地拦下，不发给服务器。

钉住的行为：
- 邮箱/用户名或密码为空时，不发起登录任务、按钮不进入「登录中…」；
- 凭据齐全时才调用 start_authlib_login / start_nide8_login。

全程 offscreen，登录后端打桩，不联网。
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYMCL_HOME", tempfile.mkdtemp(prefix="pymcl_test_val_"))

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


class AccountLocalValidationTests(unittest.TestCase):
    def _page(self, calls):
        from unittest import mock
        from app.backend import BackendAPI
        patches = [
            mock.patch.object(
                BackendAPI, "start_authlib_login",
                lambda self, *a: calls.append(("ygg", a)) or "t1"),
            mock.patch.object(
                BackendAPI, "start_nide8_login",
                lambda self, *a: calls.append(("n8", a)) or "t2"),
        ]
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])
        from app.pages.account_page import AccountPage
        page = AccountPage(BackendAPI())
        _app.processEvents()
        return page

    def test_empty_credentials_blocked_locally(self):
        calls = []
        page = self._page(calls)
        page.api.setText("https://littleskin.cn/api/yggdrasil")
        page.user.setText("")
        page.pw.setText("")
        page._ygg()
        _app.processEvents()
        self.assertEqual(calls, [], "空凭据不应发给皮肤站")
        self.assertEqual(page.yg_btn.text(), tr("登录皮肤站"),
                         "被拦下时按钮不应卡在「登录中…」")

        page.nide8_id.setText("0" * 32)
        page.nide8_user.setText("someone")
        page.nide8_pw.setText("")
        page._nide8()
        _app.processEvents()
        self.assertEqual(calls, [], "缺密码不应发给统一通行证")

    def test_full_credentials_pass_through(self):
        calls = []
        page = self._page(calls)
        page.api.setText("https://littleskin.cn/api/yggdrasil")
        page.user.setText("me@example.com")
        page.pw.setText("secret")
        page._ygg()
        _app.processEvents()
        self.assertEqual([c[0] for c in calls], ["ygg"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
