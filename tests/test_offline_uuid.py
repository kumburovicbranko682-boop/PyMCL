# -*- coding: utf-8 -*-
"""离线账号自定义 UUID（HMCL 同款）。"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mclauncher import utils  # noqa: E402
from mclauncher.auth import AccountManager, AuthError  # noqa: E402


class SetOfflineUuidTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        # 整个用例期间账号文件都指向临时目录，避免碰真实 accounts.json
        self._patcher = patch("mclauncher.auth.ACCOUNTS_FILE", self.dir / "accounts.json")
        self._patcher.start()
        self.mgr = AccountManager()
        acc = self.mgr.offline_account("Steve233")
        self.mgr.add_account(acc)

    def tearDown(self):
        self._patcher.stop()
        self._tmp.cleanup()

    def test_set_custom_uuid_dashed(self):
        out = self.mgr.set_offline_uuid("Steve233", "0f75a81d-70e5-43c1-b0b1-8fb32d3eff01")
        self.assertEqual(out, "0f75a81d-70e5-43c1-b0b1-8fb32d3eff01")
        self.assertEqual(self.mgr.get_account("Steve233").get("uuid"), out)

    def test_set_custom_uuid_plain_hex_normalized(self):
        out = self.mgr.set_offline_uuid("Steve233", "0F75A81D70E543C1B0B18FB32D3EFF01")
        self.assertEqual(out, "0f75a81d-70e5-43c1-b0b1-8fb32d3eff01")

    def test_empty_resets_to_derived(self):
        self.mgr.set_offline_uuid("Steve233", "0f75a81d70e543c1b0b18fb32d3eff01")
        out = self.mgr.set_offline_uuid("Steve233", "")
        self.assertEqual(out, utils.offline_uuid("Steve233"))

    def test_invalid_uuid_raises(self):
        for bad in ("xyz", "1234", "0f75a81d-70e5-43c1-b0b1-8fb32d3eff0", "g" * 32):
            with self.assertRaises(AuthError):
                self.mgr.set_offline_uuid("Steve233", bad)

    def test_non_offline_account_raises(self):
        self.mgr.add_account({"type": "microsoft", "name": "Premium", "uuid": "a" * 32})
        with self.assertRaises(AuthError):
            self.mgr.set_offline_uuid("Premium", "0f75a81d70e543c1b0b18fb32d3eff01")

    def test_missing_account_raises(self):
        with self.assertRaises(AuthError):
            self.mgr.set_offline_uuid("Nobody", "0f75a81d70e543c1b0b18fb32d3eff01")

    def test_launch_props_use_custom_uuid(self):
        self.mgr.set_offline_uuid("Steve233", "0f75a81d70e543c1b0b18fb32d3eff01")
        props = self.mgr.launch_props(self.mgr.get_account("Steve233"))
        self.assertEqual(props["uuid"], "0f75a81d-70e5-43c1-b0b1-8fb32d3eff01")
        self.assertEqual(props["user_type"], "legacy")


if __name__ == "__main__":
    unittest.main()
