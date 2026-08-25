# -*- coding: utf-8 -*-
"""复制实例（对齐 HMCL「复制实例」/ PCL2「复制版本」）。"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mclauncher import config as config_mod
from mclauncher import instances, utils
from mclauncher.instances import Instance, InstanceError, duplicate_instance


class Sandbox(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        for p in (mock.patch.object(utils, "ROOT", self.root),
                  mock.patch.object(config_mod, "CONFIG_FILE",
                                    self.root / "config.json")):
            p.start()
            self.addCleanup(p.stop)
        self._saved = config_mod.CONFIG.data
        config_mod.CONFIG.data = dict(config_mod.DEFAULT_CONFIG)
        self.addCleanup(self._restore)

    def _restore(self):
        config_mod.CONFIG.data = self._saved

    def make_instance(self, name="alpha") -> Instance:
        inst = Instance(name)
        inst.create()
        vdir = inst.path / "versions" / "1.20.4"
        vdir.mkdir(parents=True)
        (vdir / "1.20.4.json").write_text('{"id": "1.20.4"}', encoding="utf-8")
        (inst.path / "mods" / "sodium.jar").write_bytes(b"JARDATA" * 100)
        (inst.path / "saves" / "World1").mkdir(parents=True)
        (inst.path / "saves" / "World1" / "level.dat").write_bytes(b"\x0a\x00\x00")
        (inst.path / "options.txt").write_text("lang:zh_cn\n", encoding="utf-8")
        (inst.path / "logs" / "latest.log").write_text("boom", encoding="utf-8")
        (inst.path / "crash-reports" / "crash-1.txt").write_text("x", encoding="utf-8")
        return inst


class TestDuplicate(Sandbox):
    def test_full_copy_with_default_name(self):
        inst = self.make_instance()
        inst.set_java_pref("/usr/bin/java17")
        new_name = duplicate_instance("alpha")
        self.assertEqual(new_name, "alpha-副本")
        copy = Instance(new_name)
        # 版本 / 模组 / 存档 / 配置全带走
        self.assertEqual(copy.installed_ids(), ["1.20.4"])
        self.assertEqual((copy.path / "mods" / "sodium.jar").read_bytes(),
                         b"JARDATA" * 100)
        self.assertTrue((copy.path / "saves" / "World1" / "level.dat").is_file())
        self.assertEqual((copy.path / "options.txt").read_text("utf-8"),
                         "lang:zh_cn\n")
        # 运行残留不带
        self.assertFalse((copy.path / "logs" / "latest.log").exists())
        self.assertFalse((copy.path / "crash-reports" / "crash-1.txt").exists())
        # 元数据：名字换成新实例名，Java 偏好保留
        meta = json.loads((copy.path / ".instance.json").read_text("utf-8"))
        self.assertEqual(meta["name"], new_name)
        self.assertEqual(copy.java_pref(), "/usr/bin/java17")
        # 两个实例都在列表里，原实例没被动过
        self.assertIn("alpha", instances.list_instances())
        self.assertIn(new_name, instances.list_instances())
        self.assertTrue((inst.path / "logs" / "latest.log").is_file())

    def test_unique_names_on_repeat(self):
        self.make_instance()
        self.assertEqual(duplicate_instance("alpha"), "alpha-副本")
        self.assertEqual(duplicate_instance("alpha"), "alpha-副本-2")

    def test_custom_name_sanitized(self):
        self.make_instance()
        self.assertEqual(duplicate_instance("alpha", "my:copy"), "my-copy")

    def test_missing_source(self):
        with self.assertRaises(InstanceError):
            duplicate_instance("ghost")

    def test_progress_reaches_total(self):
        self.make_instance()
        calls = []
        duplicate_instance("alpha", on_progress=lambda d, t: calls.append((d, t)))
        self.assertTrue(calls)
        done, total = calls[-1]
        self.assertEqual(done, total)
        self.assertGreater(total, 0)
        # done 单调递增
        self.assertEqual([d for d, _ in calls], sorted(d for d, _ in calls))

    def test_empty_dirs_preserved(self):
        inst = self.make_instance()
        (inst.path / "coremods").mkdir()
        duplicate_instance("alpha")
        self.assertTrue((Instance("alpha-副本").path / "coremods").is_dir())

    def test_partial_copy_cleaned_up_on_failure(self):
        self.make_instance()
        real_copy2 = __import__("shutil").copy2
        state = {"n": 0}

        def flaky(src, dst, **kw):
            state["n"] += 1
            if state["n"] >= 3:
                raise OSError("disk full")
            return real_copy2(src, dst, **kw)

        with mock.patch("shutil.copy2", flaky):
            with self.assertRaises(OSError):
                duplicate_instance("alpha")
        self.assertNotIn("alpha-副本", instances.list_instances())
        self.assertFalse((config_mod.CONFIG.instances_dir / "alpha-副本").exists())


class TestDuplicateExternal(Sandbox):
    def make_external(self) -> Path:
        folder = self.root / "elsewhere" / "dotmc"
        vdir = folder / "versions" / "1.19.2"
        vdir.mkdir(parents=True)
        (vdir / "1.19.2.json").write_text('{"id": "1.19.2"}', encoding="utf-8")
        (folder / "saves" / "Old").mkdir(parents=True)
        (folder / "saves" / "Old" / "level.dat").write_bytes(b"\x0a")
        return folder

    def test_external_becomes_managed_copy(self):
        folder = self.make_external()
        instances.link_external_instance("官方目录", str(folder))
        new_name = duplicate_instance("官方目录")
        self.assertEqual(new_name, "官方目录-副本")
        # 副本是托管实例（在 instances 目录下、带 .instance.json、非外部）
        copy = Instance(new_name)
        self.assertEqual(copy.path.parent.resolve(),
                         config_mod.CONFIG.instances_dir.resolve())
        self.assertFalse(instances.is_external(new_name))
        self.assertIn(new_name, instances.list_instances())
        self.assertEqual(copy.installed_ids(), ["1.19.2"])
        self.assertTrue((copy.path / "saves" / "Old" / "level.dat").is_file())
        meta = json.loads((copy.path / ".instance.json").read_text("utf-8"))
        self.assertEqual(meta["name"], new_name)
        # 原目录一个字节都没动
        self.assertFalse((folder / ".instance.json").exists())


class TestBridgeFacade(Sandbox):
    def test_duplicate_impl(self):
        self.make_instance()
        from bridge.api import BackendAPI
        api = BackendAPI.__new__(BackendAPI)
        api._emit = lambda *a, **k: None
        logs = []
        final = api._duplicate_instance_impl(
            lambda *a: None, logs.append, "alpha", "副本B")
        self.assertEqual(final, "副本B")
        self.assertTrue(Instance("副本B").has_version("1.20.4"))
        self.assertTrue(any("副本B" in m for m in logs))


if __name__ == "__main__":
    unittest.main()
