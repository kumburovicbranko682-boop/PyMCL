# -*- coding: utf-8 -*-
"""MultiMC / Prism 实例包导入：mmc-pack.json 解析与 zip 分发。"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mclauncher import modpack


class ParseMmcPackTests(unittest.TestCase):
    def _pack(self, *components):
        return {"formatVersion": 1,
                "components": [{"uid": u, "version": v} for u, v in components]}

    def test_fabric(self):
        ver = modpack.parse_mmc_pack(self._pack(
            ("net.minecraft", "1.20.4"),
            ("net.fabricmc.intermediary", "1.20.4"),
            ("net.fabricmc.fabric-loader", "0.15.11")))
        self.assertEqual(ver, {"mc": "1.20.4", "loader": "fabric-loader",
                               "loader_version": "0.15.11"})

    def test_forge(self):
        ver = modpack.parse_mmc_pack(self._pack(
            ("net.minecraft", "1.20.1"),
            ("net.minecraftforge", "47.2.0")))
        self.assertEqual(ver["loader"], "forge")
        self.assertEqual(ver["loader_version"], "47.2.0")

    def test_neoforge_and_quilt(self):
        self.assertEqual(modpack.parse_mmc_pack(self._pack(
            ("net.minecraft", "1.20.4"),
            ("net.neoforged", "20.4.190")))["loader"], "neoforge")
        self.assertEqual(modpack.parse_mmc_pack(self._pack(
            ("net.minecraft", "1.20.4"),
            ("org.quiltmc.quilt-loader", "0.23.1")))["loader"], "quilt-loader")

    def test_vanilla_only(self):
        ver = modpack.parse_mmc_pack(self._pack(("net.minecraft", "1.21"),
                                                ("org.lwjgl3", "3.3.3")))
        self.assertEqual(ver, {"mc": "1.21", "loader": None, "loader_version": ""})

    def test_empty(self):
        self.assertEqual(modpack.parse_mmc_pack({})["mc"], "")


class ParseInstanceCfgTests(unittest.TestCase):
    def test_prism_general_section(self):
        cfg = modpack.parse_instance_cfg(
            "[General]\nConfigVersion=1.2\nname=My Pack\niconKey=default\n")
        self.assertEqual(cfg["name"], "My Pack")
        self.assertEqual(cfg["ConfigVersion"], "1.2")

    def test_comments_and_blank(self):
        cfg = modpack.parse_instance_cfg("# c\n; c2\n\nname=X\n")
        self.assertEqual(cfg, {"name": "X"})


class FakeInstance:
    def __init__(self, root):
        self.path = Path(root) / "inst"
        self.name = "t"
        self._meta = {}

    def ensure_standard_dirs(self):
        self.path.mkdir(parents=True, exist_ok=True)

    def create(self):
        self.ensure_standard_dirs()

    def meta(self):
        return dict(self._meta)

    def set_meta(self, key, value):
        self._meta[key] = value


def _make_mmc_zip(dest: Path, nested: bool = True):
    prefix = "MyPack/" if nested else ""
    with zipfile.ZipFile(dest, "w") as z:
        z.writestr(prefix + "mmc-pack.json", json.dumps({
            "formatVersion": 1,
            "components": [
                {"uid": "net.minecraft", "version": "1.20.4"},
                {"uid": "net.fabricmc.fabric-loader", "version": "0.15.11"},
            ],
        }))
        z.writestr(prefix + "instance.cfg",
                   "[General]\nname=Cool MMC Pack\niconKey=default\n")
        z.writestr(prefix + ".minecraft/mods/sodium.jar", b"PK\x03\x04jar")
        z.writestr(prefix + ".minecraft/config/foo.toml", "x=1")
        z.writestr(prefix + ".minecraft/options.txt", "fov:0.5")


class InstallMultimcZipTests(unittest.TestCase):
    def _run(self, nested=True):
        td = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(td, ignore_errors=True))
        zip_path = Path(td) / "pack.zip"
        _make_mmc_zip(zip_path, nested=nested)
        inst = FakeInstance(td)
        with mock.patch.object(modpack, "Installer") as installer_cls, \
             mock.patch.object(modpack, "install_loader",
                               return_value="fabric-loader-0.15.11-1.20.4") as il, \
             mock.patch.object(modpack, "_resolve_pack_minecraft",
                               return_value="1.20.4"):
            meta = modpack.install_cf_zip(mock.MagicMock(), zip_path, inst)
        return inst, meta, installer_cls, il

    def test_nested_zip_dispatch_and_meta(self):
        inst, meta, installer_cls, il = self._run(nested=True)
        self.assertEqual(meta["source"], "multimc")
        self.assertEqual(meta["name"], "Cool MMC Pack")
        self.assertEqual(meta["mc_version"], "1.20.4")
        self.assertEqual(meta["loader"], "fabric-loader-0.15.11")
        installer_cls.return_value.install_version.assert_called_once_with(
            "1.20.4", force=False, java=None)
        il.assert_called_once()
        args = il.call_args[0]
        self.assertEqual(args[1], "fabric-loader")
        self.assertEqual(args[2], "0.15.11")
        # .minecraft 数据复制进实例
        self.assertTrue((inst.path / "mods" / "sodium.jar").is_file())
        self.assertTrue((inst.path / "config" / "foo.toml").is_file())
        self.assertTrue((inst.path / "options.txt").is_file())
        # 元数据落到实例 meta
        self.assertEqual(inst.meta()["modpack"]["source"], "multimc")
        self.assertEqual(inst.meta()["mc_version"],
                         "fabric-loader-0.15.11-1.20.4")

    def test_top_level_zip(self):
        _inst, meta, _cls, _il = self._run(nested=False)
        self.assertEqual(meta["source"], "multimc")

    def test_missing_minecraft_component(self):
        with tempfile.TemporaryDirectory() as td:
            zip_path = Path(td) / "bad.zip"
            with zipfile.ZipFile(zip_path, "w") as z:
                z.writestr("mmc-pack.json", json.dumps(
                    {"components": [{"uid": "org.lwjgl3", "version": "3.3.3"}]}))
            inst = FakeInstance(td)
            with self.assertRaises(modpack.ModpackError) as ctx:
                modpack.install_cf_zip(mock.MagicMock(), zip_path, inst)
            self.assertIn("net.minecraft", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
