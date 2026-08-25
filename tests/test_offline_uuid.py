# -*- coding: utf-8 -*-
"""离线账号自定义 UUID（对齐 HMCL：服务器白名单 / 跨启动器迁移）。"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mclauncher import auth, utils
from mclauncher.auth import AccountManager, AuthError, normalize_uuid

RAW = "069a79f444e94726a5befca90e38aaf5"          # Notch 的正版 UUID（示例）
DASHED = "069a79f4-44e9-4726-a5be-fca90e38aaf5"


class TestNormalizeUuid(unittest.TestCase):
    def test_raw_hex(self):
        self.assertEqual(normalize_uuid(RAW), DASHED)

    def test_already_dashed(self):
        self.assertEqual(normalize_uuid(DASHED), DASHED)

    def test_uppercase_and_spaces(self):
        self.assertEqual(normalize_uuid("  " + RAW.upper() + "  "), DASHED)

    def test_invalid_inputs(self):
        for bad in ("", None, "zzzz", RAW[:-1], RAW + "0", "not-a-uuid",
                    "gggggggg-gggg-gggg-gggg-gggggggggggg"):
            self.assertEqual(normalize_uuid(bad), "", msg=repr(bad))


class Sandbox(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.accounts_file = Path(self.tmp.name) / "accounts.json"
        patcher = mock.patch.object(auth, "ACCOUNTS_FILE", self.accounts_file)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.mgr = AccountManager()


class TestOfflineAccountUuid(Sandbox):
    def test_default_derived_from_name(self):
        acc = self.mgr.offline_account("Dev")
        self.assertEqual(acc["uuid"], utils.offline_uuid("Dev"))

    def test_custom_uuid_kept(self):
        acc = self.mgr.offline_account("Dev", uuid=RAW)
        self.assertEqual(acc["uuid"], DASHED)

    def test_custom_uuid_overrides_skin_presets(self):
        acc = self.mgr.offline_account("Dev", skin="steve", uuid=DASHED)
        self.assertEqual(acc["uuid"], DASHED)
        self.assertEqual(acc["skin"], "steve")

    def test_steve_alex_presets_without_custom(self):
        self.assertEqual(self.mgr.offline_account("A", skin="steve")["uuid"],
                         "8667ba71-b85a-4004-af54-457a9734eed7")
        self.assertEqual(self.mgr.offline_account("A", skin="alex")["uuid"],
                         "ec561538-f3fd-461d-a7c9-7aa354f5bba9")

    def test_invalid_custom_uuid_raises(self):
        with self.assertRaises(AuthError):
            self.mgr.offline_account("Dev", uuid="not-a-uuid")


class TestSetOfflineUuid(Sandbox):
    def add_offline(self, name="Dev"):
        self.mgr.add_account(self.mgr.offline_account(name))

    def test_set_and_persist(self):
        self.add_offline()
        out = self.mgr.set_offline_uuid("Dev", RAW)
        self.assertEqual(out, DASHED)
        stored = json.loads(self.accounts_file.read_text("utf-8"))
        self.assertEqual(stored["accounts"][0]["uuid"], DASHED)
        # 重新加载也读得回来
        fresh = AccountManager()
        self.assertEqual(fresh.get_account("Dev")["uuid"], DASHED)

    def test_empty_restores_derived_default(self):
        self.add_offline()
        self.mgr.set_offline_uuid("Dev", RAW)
        out = self.mgr.set_offline_uuid("Dev", "")
        self.assertEqual(out, utils.offline_uuid("Dev"))

    def test_invalid_uuid_raises_and_keeps_old(self):
        self.add_offline()
        old = self.mgr.get_account("Dev")["uuid"]
        with self.assertRaises(AuthError):
            self.mgr.set_offline_uuid("Dev", "xyz")
        self.assertEqual(self.mgr.get_account("Dev")["uuid"], old)

    def test_unknown_account_raises(self):
        with self.assertRaises(AuthError):
            self.mgr.set_offline_uuid("Nobody", RAW)

    def test_non_offline_account_rejected(self):
        self.mgr.add_account({"type": "microsoft", "name": "MS", "uuid": RAW})
        with self.assertRaises(AuthError):
            self.mgr.set_offline_uuid("MS", RAW)

    def test_launch_props_use_custom_uuid(self):
        self.add_offline()
        self.mgr.set_offline_uuid("Dev", RAW)
        props = self.mgr.launch_props(self.mgr.get_account("Dev"))
        self.assertEqual(props["uuid"], DASHED)
        self.assertEqual(props["user_type"], "legacy")


class TestBridgeFacade(Sandbox):
    """bridge.api 与 backend 对齐：add_offline_account(uuid=) / set_offline_uuid。"""

    def _api(self):
        from bridge.api import BackendAPI
        api = BackendAPI.__new__(BackendAPI)
        api.accounts = self.mgr
        api._emit = lambda *a, **k: None
        return api

    def test_add_with_uuid(self):
        api = self._api()
        name = api.add_offline_account("Dev", uuid=RAW)
        self.assertEqual(name, "Dev")
        self.assertEqual(self.mgr.get_account("Dev")["uuid"], DASHED)

    def test_set_offline_uuid(self):
        api = self._api()
        api.add_offline_account("Dev")
        out = api.set_offline_uuid("Dev", DASHED)
        self.assertEqual(out, DASHED)
        self.assertEqual(self.mgr.get_account("Dev")["uuid"], DASHED)


if __name__ == "__main__":
    unittest.main()
