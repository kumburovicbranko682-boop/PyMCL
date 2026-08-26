# -*- coding: utf-8 -*-
"""实例级「禁止更新 Mod」锁定（PCL 2.10.7 同款，防整合包玩家误更新拆包）：

1. 锁开关读写与持久化（实例 meta）；
2. 锁定后 check_updates / apply_update 直接拒绝，报错可读；
3. bridge 门面的 get/set 往返。
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import PropertyMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mclauncher import mod_update, utils  # noqa: E402
from mclauncher.config import CONFIG  # noqa: E402


class LockCoreTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        self.addCleanup(self._td.cleanup)
        for p in (
            patch.object(utils, "ROOT", self.root),
            patch.object(type(CONFIG), "instances_dir",
                         new_callable=PropertyMock,
                         return_value=self.root / "instances"),
        ):
            p.start()
            self.addCleanup(p.stop)
        from mclauncher.instances import Instance
        (self.root / "instances" / "inst").mkdir(parents=True)
        self.inst = Instance("inst")

    def test_default_unlocked(self):
        self.assertFalse(mod_update.is_locked(self.inst))

    def test_set_and_persist(self):
        self.assertTrue(mod_update.set_locked(self.inst, True))
        self.assertTrue(mod_update.is_locked(self.inst))
        # meta 落盘，新的 Instance 对象也能读到
        from mclauncher.instances import Instance
        self.assertTrue(mod_update.is_locked(Instance("inst")))
        self.assertFalse(mod_update.set_locked(self.inst, False))
        self.assertFalse(mod_update.is_locked(self.inst))

    def test_check_updates_rejected_when_locked(self):
        mod_update.set_locked(self.inst, True)
        with self.assertRaises(mod_update.UpdateLockedError) as ctx:
            mod_update.check_updates(self.inst, dm=object())
        self.assertIn("锁定", str(ctx.exception))

    def test_apply_update_rejected_when_locked(self):
        mod_update.set_locked(self.inst, True)
        with self.assertRaises(mod_update.UpdateLockedError):
            mod_update.apply_update(self.inst, {"url": "https://x/y.jar",
                                                "filename": "y.jar"}, dm=object())

    def test_unlocked_check_still_works(self):
        # 没有 mods 目录时应返回空列表（原有行为不受影响）
        self.assertEqual(mod_update.check_updates(self.inst, dm=object()), [])


class BridgeFacadeTests(unittest.TestCase):
    def setUp(self):
        from mclauncher.config import DEFAULT_CONFIG

        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        self.addCleanup(self._td.cleanup)
        data = {k: (list(v) if isinstance(v, list) else dict(v) if isinstance(v, dict) else v)
                for k, v in DEFAULT_CONFIG.items()}
        for p in (patch.object(utils, "ROOT", self.root),
                  patch.object(CONFIG, "data", data),
                  patch.object(CONFIG, "save", lambda: None)):
            p.start()
            self.addCleanup(p.stop)

        from bridge.api import BackendAPI

        class _Bus:
            def emit(self, *a, **k):
                pass

        self.api = BackendAPI(_Bus())

    def test_roundtrip(self):
        self.assertFalse(self.api.get_mod_update_lock("default"))
        self.assertTrue(self.api.set_mod_update_lock("default", True))
        self.assertTrue(self.api.get_mod_update_lock("default"))
        self.assertFalse(self.api.set_mod_update_lock("default", False))
        self.assertFalse(self.api.get_mod_update_lock("default"))


if __name__ == "__main__":
    unittest.main()
