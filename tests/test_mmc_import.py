# -*- coding: utf-8 -*-
"""MultiMC / Prism 整合包导入（mmc-pack.json）。"""
from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from mclauncher import modpack, modpack_update
from mclauncher.downloader import DownloadManager
from mclauncher.instances import Instance
from mclauncher.modpack import ModpackError


def mmc_pack_json(mc="1.20.1", loader_uid=None, loader_ver=""):
    components = [
        {"uid": "org.lwjgl3", "version": "3.3.1"},
        {"uid": "net.minecraft", "version": mc},
    ]
    if loader_uid:
        components.append({"uid": "net.fabricmc.intermediary", "version": mc})
        components.append({"uid": loader_uid, "version": loader_ver})
    return json.dumps({"formatVersion": 1, "components": components})


class Sandbox(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        patcher = mock.patch(
            "mclauncher.instances.get_instance_path",
            side_effect=lambda name: self.root / "instances" / name)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def make_instance(self, name="mmc-test") -> Instance:
        inst = Instance(name)
        inst.create()
        return inst

    def install(self, zip_path: Path, inst: Instance):
        dm = DownloadManager(threads=1)
        with mock.patch.object(modpack, "Installer"), \
                mock.patch.object(modpack, "install_loader",
                                  return_value="loader-vid") as fake_loader, \
                mock.patch.object(modpack, "_resolve_pack_minecraft",
                                  side_effect=lambda dm_, d, p=None: d):
            meta = modpack.install_cf_zip(dm, str(zip_path), inst)
        return meta, fake_loader


class TestMmcImport(Sandbox):
    def _zip(self, prefix="", loader_uid="net.fabricmc.fabric-loader",
             loader_ver="0.15.11", instance_cfg="name=My Prism Pack\n"):
        p = self.root / "pack.zip"
        with zipfile.ZipFile(p, "w") as z:
            z.writestr(f"{prefix}mmc-pack.json",
                       mmc_pack_json(loader_uid=loader_uid, loader_ver=loader_ver))
            if instance_cfg is not None:
                z.writestr(f"{prefix}instance.cfg", instance_cfg)
            z.writestr(f"{prefix}.minecraft/mods/x.jar", "PK-fake")
            z.writestr(f"{prefix}.minecraft/config/a.cfg", "opt=1\n")
            z.writestr(f"{prefix}.minecraft/logs/latest.log", "junk")
        return p

    def test_basic_import(self):
        inst = self.make_instance()
        meta, fake_loader = self.install(self._zip(), inst)
        self.assertEqual(meta["name"], "My Prism Pack")
        self.assertEqual(meta["source"], "multimc")
        self.assertEqual(meta["mc_version"], "1.20.1")
        self.assertEqual(meta["loader"], "fabric-loader-0.15.11")
        args = fake_loader.call_args[0]
        self.assertEqual((args[1], args[2], args[3]),
                         ("fabric-loader", "0.15.11", "1.20.1"))
        self.assertTrue((inst.path / "mods" / "x.jar").is_file())
        self.assertTrue((inst.path / "config" / "a.cfg").is_file())
        # logs 是运行垃圾，不拷
        self.assertFalse((inst.path / "logs" / "latest.log").exists())
        # 实例 meta 落盘
        self.assertEqual(inst.meta()["modpack"]["source"], "multimc")

    def test_nested_root(self):
        inst = self.make_instance("nested")
        meta, _ = self.install(self._zip(prefix="MyPack/"), inst)
        self.assertEqual(meta["source"], "multimc")
        self.assertTrue((inst.path / "mods" / "x.jar").is_file())

    def test_prism_general_section(self):
        inst = self.make_instance("prism")
        cfg = "[General]\nConfigVersion=1.2\nname=Prism General\niconKey=default\n"
        meta, _ = self.install(self._zip(instance_cfg=cfg), inst)
        self.assertEqual(meta["name"], "Prism General")

    def test_vanilla_pack(self):
        inst = self.make_instance("vanilla")
        meta, fake_loader = self.install(
            self._zip(loader_uid=None, loader_ver=""), inst)
        self.assertEqual(meta["loader"], "vanilla")
        fake_loader.assert_not_called()

    def test_forge_uid(self):
        inst = self.make_instance("forge")
        meta, fake_loader = self.install(
            self._zip(loader_uid="net.minecraftforge", loader_ver="47.2.0"), inst)
        self.assertEqual(meta["loader"], "forge-47.2.0")
        self.assertEqual(fake_loader.call_args[0][1], "forge")

    def test_missing_minecraft_component(self):
        inst = self.make_instance("bad")
        p = self.root / "bad.zip"
        with zipfile.ZipFile(p, "w") as z:
            z.writestr("mmc-pack.json", json.dumps(
                {"components": [{"uid": "org.lwjgl3", "version": "3.3.1"}]}))
        with self.assertRaises(ModpackError) as ctx:
            self.install(p, inst)
        self.assertIn("net.minecraft", str(ctx.exception))

    def test_no_update_source(self):
        inst = self.make_instance("noupd")
        self.install(self._zip(), inst)
        state = modpack_update.pack_state(inst)
        self.assertTrue(state["installed"])
        self.assertFalse(state["can_update"])
        self.assertIn("本地导入", state["reason"])


if __name__ == "__main__":
    unittest.main()
