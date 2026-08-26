# -*- coding: utf-8 -*-
"""账号页缺凭据不许点火：皮肤站 / 统一通行证登录在邮箱或密码为空时
必须就地提示，不进入「登录中…」、不发起注定失败的网络请求。

全程 offscreen + 临时数据目录，不弹任何窗口、不联网。
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYMCL_HOME", tempfile.mkdtemp(prefix="pymcl_test_acct_"))

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from app.backend import BackendAPI  # noqa: E402
from app.pages.account_page import AccountPage  # noqa: E402


def _page():
    backend = BackendAPI(None)
    calls = {"ygg": [], "n8": []}
    backend.start_authlib_login = (
        lambda api, user, pw: calls["ygg"].append((api, user, pw)) or "t-ygg")
    backend.start_nide8_login = (
        lambda sid, user, pw: calls["n8"].append((sid, user, pw)) or "t-n8")
    page = AccountPage(backend)
    _app.processEvents()
    return page, calls


class AuthlibValidationTests(unittest.TestCase):
    def test_empty_credentials_blocked(self):
        page, calls = _page()
        try:
            page.api.setText("https://littleskin.cn/api/yggdrasil")
            page.user.setText("")
            page.pw.setText("")
            page.yg_btn.click()
            _app.processEvents()
            self.assertEqual(calls["ygg"], [], "缺凭据不该发起登录")
            self.assertTrue(page.yg_btn.isEnabled(), "不该进入登录中状态")
        finally:
            page.deleteLater()

    def test_missing_password_blocked(self):
        page, calls = _page()
        try:
            page.api.setText("https://littleskin.cn/api/yggdrasil")
            page.user.setText("someone@example.com")
            page.pw.setText("")
            page.yg_btn.click()
            _app.processEvents()
            self.assertEqual(calls["ygg"], [])
        finally:
            page.deleteLater()

    def test_full_credentials_fire(self):
        page, calls = _page()
        try:
            page.api.setText("https://littleskin.cn/api/yggdrasil")
            page.user.setText("someone@example.com")
            page.pw.setText("secret")
            page.yg_btn.click()
            _app.processEvents()
            self.assertEqual(len(calls["ygg"]), 1, "凭据齐全应正常发起登录")
            self.assertFalse(page.yg_btn.isEnabled(), "发起后应进入登录中状态")
        finally:
            page.deleteLater()


class Nide8ValidationTests(unittest.TestCase):
    def test_empty_credentials_blocked(self):
        page, calls = _page()
        try:
            page.nide8_id.setText("0" * 32)
            page.nide8_user.setText("")
            page.nide8_pw.setText("")
            page.n8_btn.click()
            _app.processEvents()
            self.assertEqual(calls["n8"], [], "缺凭据不该发起登录")
            self.assertTrue(page.n8_btn.isEnabled())
        finally:
            page.deleteLater()

    def test_full_credentials_fire(self):
        page, calls = _page()
        try:
            page.nide8_id.setText("0" * 32)
            page.nide8_user.setText("player")
            page.nide8_pw.setText("secret")
            page.n8_btn.click()
            _app.processEvents()
            self.assertEqual(len(calls["n8"]), 1)
        finally:
            page.deleteLater()


if __name__ == "__main__":
    unittest.main(verbosity=2)
