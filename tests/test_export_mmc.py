# -*- coding: utf-8 -*-
"""MultiMC / Prism 格式实例导出：mmc-pack 组件、instance.cfg、文件打包。"""
import configparser
import json
import sys
import unittest
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mclauncher import export_pack


class _FakeInstance:
    def __init__(self, root: Path, meta=None):
        self.path = root
        self.name = "mmctest"
        self._meta = meta or {}

    def meta(self):
        return self._meta


class MmcComponentsTests(unittest.TestCase):
    def test_fabric_includes_intermediary(self):
        comps = export_pack._mmc_components(
            {"mc_version": "1.20.1", "loader": "fabric", "loader_version": "0.15.11"})
        self.assertEqual(comps, [
            {"uid": "net.minecraft", "version": "1.20.1", "important": True},
            {"uid": "net.fabricmc.intermediary", "version": "1.20.1"},
            {"uid": "net.fabricmc.fabric-loader", "version": "0.15.11"},
        ])

    def test_quilt_uses_quilt_loader_uid(self):
        comps = export_pack._mmc_components(
            {"mc_version": "1.20.4", "loader": "quilt", "loader_version": "0.24.0"})
        uids = [c["uid"] for c in comps]
        self.assertEqual(uids, ["net.minecraft", "net.fabricmc.intermediary",
                                "org.quiltmc.quilt-loader"])

    def test_forge_has_no_intermediary(self):
        comps = export_pack._mmc_components(
            {"mc_version": "1.20.1", "loader": "forge", "loader_version": "47.2.0"})
        self.assertEqual(comps, [
            {"uid": "net.minecraft", "version": "1.20.1", "important": True},
            {"uid": "net.minecraftforge", "version": "47.2.0"},
        ])

    def test_neoforge_uid(self):
        comps = export_pack._mmc_components(
            {"mc_version": "1.21", "loader": "neoforge", "loader_version": "21.0.167"})
        self.assertIn({"uid": "net.neoforged", "version": "21.0.167"}, comps)

    def test_vanilla_only(self):
        comps = export_pack._mmc_components({"mc_version": "1.19.4", "loader": ""})
        self.assertEqual(comps, [
            {"uid": "net.minecraft", "version": "1.19.4", "important": True},
        ])

    def test_loader_without_version_still_listed(self):
        comps = export_pack._mmc_components(
            {"mc_version": "1.20.1", "loader": "forge", "loader_version": ""})
        self.assertEqual(comps[-1], {"uid": "net.minecraftforge"})

    def test_unknown_loader_ignored(self):
        comps = export_pack._mmc_components(
            {"mc_version": "1.20.1", "loader": "rift", "loader_version": "1.0"})
        self.assertEqual([c["uid"] for c in comps], ["net.minecraft"])


class ExportMmcZipTests(unittest.TestCase):
    def _make_instance(self, root: Path):
        (root / "mods").mkdir(parents=True)
        (root / "mods" / "sodium.jar").write_bytes(b"PK\x03\x04sodium")
        (root / "mods" / "old.jar.disabled").write_bytes(b"PK\x03\x04old")
        (root / "mods" / "readme.txt").write_text("not a mod", encoding="utf-8")
        (root / "config").mkdir()
        (root / "config" / "some.toml").write_text("x = 1", encoding="utf-8")
        (root / "options.txt").write_text("lang:zh_cn", encoding="utf-8")
        (root / "servers.dat").write_bytes(b"\x0a\x00\x00")
        return _FakeInstance(root, meta={"modpack": {
            "name": "MyPack", "version": "2.1", "mc_version": "1.20.1",
            "loader": "Fabric", "loader_version": "0.15.11",
        }})

    def test_zip_structure(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            inst = self._make_instance(root)
            notes = []
            path = export_pack.export_mmc_zip(
                inst, root / "out.zip",
                on_note=lambda m, a, b: notes.append((m, a, b)))
            with zipfile.ZipFile(path) as zf:
                names = set(zf.namelist())
                pack = json.loads(zf.read("mmc-pack.json"))
                cfg_text = zf.read("instance.cfg").decode("utf-8")
            self.assertIn("instance.cfg", names)
            self.assertIn("mmc-pack.json", names)
            self.assertIn(".minecraft/mods/sodium.jar", names)
            self.assertIn(".minecraft/mods/old.jar.disabled", names)
            self.assertNotIn(".minecraft/mods/readme.txt", names)
            self.assertIn(".minecraft/config/some.toml", names)
            self.assertIn(".minecraft/options.txt", names)
            self.assertIn(".minecraft/servers.dat", names)
            self.assertEqual(pack["formatVersion"], 1)
            uids = [c["uid"] for c in pack["components"]]
            self.assertEqual(uids, ["net.minecraft", "net.fabricmc.intermediary",
                                    "net.fabricmc.fabric-loader"])
            parser = configparser.ConfigParser()
            parser.read_string(cfg_text)
            self.assertEqual(parser["General"]["name"], "MyPack")
            self.assertEqual(parser["General"]["InstanceType"], "OneSix")
            self.assertTrue(notes and notes[-1][0] == "导出完成")

    def test_export_empty_instance(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            inst = _FakeInstance(root, meta={"mc_version": "1.19.4"})
            path = export_pack.export_mmc_zip(inst, root / "out.zip")
            with zipfile.ZipFile(path) as zf:
                names = set(zf.namelist())
                pack = json.loads(zf.read("mmc-pack.json"))
            self.assertEqual(names, {"instance.cfg", "mmc-pack.json"})
            self.assertEqual(pack["components"], [
                {"uid": "net.minecraft", "version": "1.19.4", "important": True},
            ])

    def test_instance_name_fallback(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            inst = _FakeInstance(root)
            path = export_pack.export_mmc_zip(inst, root / "out.zip")
            with zipfile.ZipFile(path) as zf:
                cfg_text = zf.read("instance.cfg").decode("utf-8")
            parser = configparser.ConfigParser()
            parser.read_string(cfg_text)
            self.assertEqual(parser["General"]["name"], "mmctest")


class FacadeTests(unittest.TestCase):
    def test_bridge_api_handles_multimc(self):
        import inspect
        from bridge.api import BackendAPI
        src = inspect.getsource(BackendAPI._export_pack_impl)
        self.assertIn("multimc", src)
        self.assertIn("export_mmc_zip", src)

    def test_qt_backend_handles_multimc(self):
        import ast
        src = Path(__file__).resolve().parents[1] / "app" / "backend.py"
        tree = ast.parse(src.read_text(encoding="utf-8"))
        found = False
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_export_pack_impl":
                found = "multimc" in ast.dump(node)
        self.assertTrue(found)


if __name__ == "__main__":
    unittest.main()
