# -*- coding: utf-8 -*-
"""认证失败降级离线启动：令牌刷新失败时保留 name/UUID 以离线身份进游戏。"""
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mclauncher import auth
from mclauncher import utils


def _manager(tmp: Path) -> auth.AccountManager:
    with patch.object(auth, "ACCOUNTS_FILE", tmp / "accounts.json"):
        mgr = auth.AccountManager()
    return mgr


class EnsureValidOrFallbackTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.addCleanup(self._td.cleanup)
        patcher = patch.object(auth, "ACCOUNTS_FILE", self.tmp / "accounts.json")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.mgr = auth.AccountManager()

    def test_valid_token_passthrough(self):
        acc = {"type": "microsoft", "name": "Steve", "uuid": "a" * 32,
               "access_token": "tok", "expires_at": time.time() + 3600}
        out, reason = self.mgr.ensure_valid_or_fallback(acc)
        self.assertEqual(reason, "")
        self.assertIs(out, acc)

    def test_offline_account_passthrough(self):
        acc = {"type": "offline", "name": "Steve"}
        out, reason = self.mgr.ensure_valid_or_fallback(acc)
        self.assertEqual(reason, "")
        self.assertEqual(out, acc)

    def test_expired_without_refresh_token_falls_back(self):
        acc = {"type": "microsoft", "name": "Steve", "uuid": "ab" * 16,
               "access_token": "old", "expires_at": time.time() - 10}
        out, reason = self.mgr.ensure_valid_or_fallback(acc)
        self.assertTrue(reason)
        self.assertEqual(out["type"], "offline")
        self.assertEqual(out["name"], "Steve")
        self.assertEqual(out["uuid"], "ab" * 16)
        # 降级账号是临时的，不能写回账号存储
        self.assertFalse((self.tmp / "accounts.json").exists())
        self.assertEqual(self.mgr.accounts, [])

    def test_refresh_network_error_falls_back(self):
        acc = {"type": "microsoft", "name": "Alex", "uuid": "cd" * 16,
               "access_token": "old", "refresh_token": "r",
               "expires_at": time.time() - 10}
        with patch.object(auth.MicrosoftAuthenticator, "refresh",
                          side_effect=OSError("network unreachable")):
            out, reason = self.mgr.ensure_valid_or_fallback(acc)
        self.assertIn("network", reason)
        self.assertEqual(out["type"], "offline")
        self.assertEqual(out["name"], "Alex")

    def test_authlib_refresh_failure_falls_back(self):
        acc = {"type": "authlib", "name": "SkinUser", "uuid": "ef" * 16,
               "access_token": "", "expires_at": time.time() - 10,
               "api": "https://littleskin.cn/api/yggdrasil"}
        import mclauncher.authlib as authlib_mod
        with patch.object(authlib_mod, "refresh",
                          side_effect=auth.AuthError("token invalid")):
            out, reason = self.mgr.ensure_valid_or_fallback(acc)
        self.assertTrue(reason)
        self.assertEqual(out["type"], "offline")
        self.assertEqual(out["uuid"], "ef" * 16)

    def test_no_name_reraises(self):
        acc = {"type": "microsoft", "name": "", "uuid": "a" * 32,
               "access_token": "old", "expires_at": time.time() - 10}
        with self.assertRaises(auth.AuthError):
            self.mgr.ensure_valid_or_fallback(acc)

    def test_fallback_launch_props_keep_identity(self):
        acc = {"type": "microsoft", "name": "Steve", "uuid": "ab" * 16,
               "access_token": "old", "expires_at": time.time() - 10}
        out, reason = self.mgr.ensure_valid_or_fallback(acc)
        self.assertTrue(reason)
        props = self.mgr.launch_props(out)
        self.assertEqual(props["name"], "Steve")
        self.assertEqual(props["uuid"], utils.dashed_uuid("ab" * 16))
        self.assertEqual(props["token"], "0")
        self.assertEqual(props["user_type"], "legacy")


if __name__ == "__main__":
    unittest.main()
