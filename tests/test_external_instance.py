# -*- coding: utf-8 -*-
"""外部游戏目录：原地接入已有 .minecraft（对齐 HMCL「游戏目录」）。"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mclauncher import config as config_mod
from mclauncher import instances, utils
from mclauncher.instances import Instance, InstanceError


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

    def make_external(self, name="dotmc", with_version="") -> Path:
        folder = self.root / "elsewhere" / name
        (folder / "saves").mkdir(parents=True)
        if with_version:
            vdir = folder / "versions" / with_version
            vdir.mkdir(parents=True)
            (vdir / f"{with_version}.json").write_text("{}", encoding="utf-8")
        return folder


class TestLink(Sandbox):
    def test_link_and_use_in_place(self):
        folder = self.make_external(with_version="1.20.4")
        final = instances.link_external_instance("官方目录", str(folder))
        self.assertEqual(final, "官方目录")
        self.assertIn("官方目录", instances.list_instances())
        self.assertTrue(instances.is_external("官方目录"))
        inst = Instance("官方目录")
        self.assertEqual(inst.path, folder.resolve())
        self.assertEqual(inst.installed_ids(), ["1.20.4"])

    def test_registry_persists_via_config(self):
        folder = self.make_external()
        instances.link_external_instance("ext", str(folder))
        stored = json.loads((self.root / "config.json").read_text("utf-8"))
        self.assertEqual(stored["external_instances"], {"ext": str(folder.resolve())})
        # external_instances 在 DEFAULT_CONFIG 里声明过，重启后能回读
        fresh = config_mod.Config()
        self.assertEqual(fresh.get("external_instances"),
                         {"ext": str(folder.resolve())})

    def test_link_validations(self):
        folder = self.make_external()
        with self.assertRaises(InstanceError):
            instances.link_external_instance("x", str(self.root / "nope"))
        # 实例目录内部的路径不需要外部接入
        inside = config_mod.CONFIG.instances_dir / "normal"
        inside.mkdir(parents=True)
        with self.assertRaises(InstanceError):
            instances.link_external_instance("inner", str(inside))
        instances.link_external_instance("ext", str(folder))
        # 同名 / 同目录都拒绝
        with self.assertRaises(InstanceError):
            instances.link_external_instance("ext", str(self.make_external("two")))
        with self.assertRaises(InstanceError):
            instances.link_external_instance("dup", str(folder))
        # 与内部实例重名拒绝
        Instance("existing").create()
        with self.assertRaises(InstanceError):
            instances.link_external_instance(
                "existing", str(self.make_external("three")))

    def test_create_refuses_registered_name(self):
        instances.link_external_instance("ext", str(self.make_external()))
        with self.assertRaises(InstanceError):
            Instance("ext").create()

    def test_stale_dir_not_listed(self):
        folder = self.make_external()
        instances.link_external_instance("ext", str(folder))
        import shutil
        shutil.rmtree(folder)
        self.assertNotIn("ext", instances.list_instances())


class TestDeleteRename(Sandbox):
    def test_delete_keeps_files(self):
        folder = self.make_external()
        marker = folder / "saves" / "keep.txt"
        marker.write_text("data", encoding="utf-8")
        instances.link_external_instance("ext", str(folder))
        Instance("ext").delete()
        self.assertFalse(instances.is_external("ext"))
        self.assertNotIn("ext", instances.list_instances())
        self.assertTrue(marker.is_file(), "删除外部实例绝不能碰用户文件")

    def test_delete_resets_default_instance(self):
        instances.link_external_instance("ext", str(self.make_external()))
        config_mod.CONFIG.set("default_instance", "ext")
        Instance("ext").delete()
        self.assertNotEqual(config_mod.CONFIG.get("default_instance"), "ext")

    def test_rename_registration_only(self):
        folder = self.make_external()
        instances.link_external_instance("旧名", str(folder))
        inst = Instance("旧名")
        inst.rename("新名")
        self.assertEqual(inst.name, "新名")
        self.assertEqual(inst.path, folder.resolve())
        self.assertTrue(instances.is_external("新名"))
        self.assertFalse(instances.is_external("旧名"))
        # 文件夹本身没有被移动
        self.assertTrue(folder.is_dir())
        # 不往用户目录里写 .instance.json
        self.assertFalse((folder / instances.INSTANCE_META).exists())

    def test_rename_conflicts(self):
        instances.link_external_instance("a", str(self.make_external("one")))
        instances.link_external_instance("b", str(self.make_external("two")))
        Instance("normal").create()
        with self.assertRaises(InstanceError):
            Instance("a").rename("b")
        with self.assertRaises(InstanceError):
            Instance("a").rename("normal")
        with self.assertRaises(InstanceError):
            Instance("a").rename("bad/name")


class TestFacade(Sandbox):
    def test_bridge_rows_and_link(self):
        from bridge.api import BackendAPI
        api = BackendAPI.__new__(BackendAPI)
        api._ensure_default_instance = lambda: None
        api.instance_java_label = lambda name: "自动"
        api._emit = lambda *a, **k: None
        Instance("normal").create()
        final = api.link_external_instance("ext", str(self.make_external(with_version="1.19.2")))
        self.assertEqual(final, "ext")
        rows = {r["name"]: r for r in api.get_instances()}
        self.assertTrue(rows["ext"]["external"])
        self.assertFalse(rows["normal"]["external"])
        self.assertEqual(rows["ext"]["versions"], 1)
        self.assertTrue(rows["ext"]["path"])


if __name__ == "__main__":
    unittest.main()
