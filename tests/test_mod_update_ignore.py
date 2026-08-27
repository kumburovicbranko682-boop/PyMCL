# -*- coding: utf-8 -*-
"""模组更新忽略（PCL2「忽略此版本 / 不再提醒」同款）+ 门面对齐。"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import PropertyMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mclauncher import mod_update  # noqa: E402
from mclauncher.config import CONFIG  # noqa: E402


class _Isolated(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        self.addCleanup(self._td.cleanup)
        patcher = patch.object(type(CONFIG), "instances_dir",
                               new_callable=PropertyMock,
                               return_value=self.root)
        patcher.start()
        self.addCleanup(patcher.stop)
        from mclauncher.instances import Instance
        (self.root / "inst").mkdir(parents=True)
        self.inst = Instance("inst")


class IgnoreStoreTests(_Isolated):
    def test_empty_when_no_file(self):
        self.assertEqual(mod_update.ignores(self.inst), {})

    def test_set_and_clear_round_trip(self):
        data = mod_update.set_ignore(self.inst, "p1", "2.0")
        self.assertEqual(data, {"p1": "2.0"})
        data = mod_update.set_ignore(self.inst, "p2")
        self.assertEqual(data, {"p1": "2.0", "p2": "*"})
        # 落盘可重读
        self.assertEqual(mod_update.ignores(self.inst), {"p1": "2.0", "p2": "*"})
        data = mod_update.clear_ignore(self.inst, "p1")
        self.assertEqual(data, {"p2": "*"})
        # 清不存在的键不报错
        self.assertEqual(mod_update.clear_ignore(self.inst, "nope"), {"p2": "*"})

    def test_empty_project_rejected(self):
        with self.assertRaises(ValueError):
            mod_update.set_ignore(self.inst, "")

    def test_blank_latest_falls_back_to_star(self):
        self.assertEqual(mod_update.set_ignore(self.inst, "p1", ""), {"p1": "*"})

    def test_corrupt_file_treated_as_empty(self):
        (Path(self.inst.path) / mod_update.IGNORE_FILE).write_text("[1,2]")
        self.assertEqual(mod_update.ignores(self.inst), {})


class IsIgnoredTests(unittest.TestCase):
    def test_star_matches_any_latest(self):
        self.assertTrue(mod_update.is_ignored(
            {"project": "p1", "latest": "9.9"}, {"p1": "*"}))

    def test_specific_version_only_matches_itself(self):
        m = {"p1": "2.0"}
        self.assertTrue(mod_update.is_ignored({"project": "p1", "latest": "2.0"}, m))
        self.assertFalse(mod_update.is_ignored({"project": "p1", "latest": "3.0"}, m))

    def test_unknown_project_not_ignored(self):
        self.assertFalse(mod_update.is_ignored({"project": "x", "latest": "1"}, {}))


class CheckUpdatesFilterTests(_Isolated):
    """check_updates 默认吞掉被忽略的行；include_ignored=True 时带 ignored 标记返回。"""

    def setUp(self):
        super().setUp()
        self.mods = Path(self.inst.path) / "mods"
        self.mods.mkdir()
        (self.mods / "a.jar").write_bytes(b"PK\x03\x04fake")

        def fake_modrinth(dm, path, digest, mc_version, loader, info):
            return {
                "filename": path.name, "name": "A", "current": "1.0",
                "latest": self.latest, "project": "p1", "url": "http://x/a2.jar",
                "sha1": "", "size": 1, "filename_new": "a2.jar",
                "source": "modrinth",
            }

        self.latest = "2.0"
        for target, repl in (
            ("_modrinth_update", fake_modrinth),
            ("inspect_jar", lambda p: {"name": "A"}),
        ):
            p = patch.object(mod_update, target, repl)
            p.start()
            self.addCleanup(p.stop)
        p = patch.object(mod_update.utils, "sha1_file", lambda p: "x" * 40)
        p.start()
        self.addCleanup(p.stop)

    def _check(self, **kw):
        return mod_update.check_updates(self.inst, dm=object(),
                                        mods_path=self.mods, **kw)

    def test_not_ignored_row_passes_with_flag_false(self):
        rows = self._check()
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0]["ignored"])

    def test_star_ignore_hides_row(self):
        mod_update.set_ignore(self.inst, "p1", "*")
        self.assertEqual(self._check(), [])

    def test_version_ignore_hides_only_that_version(self):
        mod_update.set_ignore(self.inst, "p1", "2.0")
        self.assertEqual(self._check(), [])
        # 出了更新的版本要重新提醒（PCL2「忽略此版本」语义）
        self.latest = "3.0"
        rows = self._check()
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0]["ignored"])

    def test_include_ignored_returns_tagged_row(self):
        mod_update.set_ignore(self.inst, "p1", "*")
        rows = self._check(include_ignored=True)
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["ignored"])

    def test_unignore_restores_reminder(self):
        mod_update.set_ignore(self.inst, "p1", "*")
        mod_update.clear_ignore(self.inst, "p1")
        self.assertEqual(len(self._check()), 1)


class FacadeParityTests(unittest.TestCase):
    """backend.py 与 bridge/api.py 的更新/忽略方法必须对齐。"""

    METHODS = ("check_mod_updates", "apply_mod_update", "start_mod_updates",
               "list_mod_update_ignores", "ignore_mod_update", "unignore_mod_update")

    def test_both_facades_expose_same_signatures(self):
        import inspect
        from app.backend import BackendAPI as QtBackend
        from bridge.api import BackendAPI
        for name in self.METHODS:
            qt = getattr(QtBackend, name, None)
            br = getattr(BackendAPI, name, None)
            self.assertIsNotNone(qt, f"QtBackend 缺 {name}")
            self.assertIsNotNone(br, f"BackendAPI 缺 {name}")
            self.assertEqual(inspect.signature(qt), inspect.signature(br),
                             f"{name} 签名不一致")


class BridgeRoundTripTests(_Isolated):
    """BackendAPI 上的忽略三件套真实读写实例目录。"""

    def test_ignore_unignore_via_bridge(self):
        from bridge.api import BackendAPI
        api = BackendAPI.__new__(BackendAPI)
        api._bus = type("B", (), {"emit": lambda self, *a, **k: None})()
        from mclauncher.instances import Instance
        with patch.object(BackendAPI, "_instance",
                          lambda self, name: Instance("inst")):
            out = api.ignore_mod_update("inst", "p1", "2.0")
            self.assertEqual(out, {"p1": "2.0"})
            self.assertEqual(api.list_mod_update_ignores("inst"), {"p1": "2.0"})
            out = api.unignore_mod_update("inst", "p1")
            self.assertEqual(out, {})


if __name__ == "__main__":
    unittest.main()
