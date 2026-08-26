# -*- coding: utf-8 -*-
"""整合包导出向导：候选清单、include 勾选过滤、meta 覆盖、对话框 payload。"""
import json
import sys
import unittest
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mclauncher import export_pack


class _FakeInstance:
    def __init__(self, root: Path, meta=None):
        self.path = root
        self.name = "seltest"
        self._meta = meta or {}

    def meta(self):
        return self._meta


def _make_tree(root: Path):
    (root / "mods").mkdir(parents=True)
    (root / "mods" / "a.jar").write_bytes(b"PK\x03\x04a")
    (root / "mods" / "cfg").mkdir()
    (root / "mods" / "cfg" / "a.toml").write_text("x=1", encoding="utf-8")
    (root / "config").mkdir()
    (root / "config" / "c.toml").write_text("y=2", encoding="utf-8")
    (root / "saves").mkdir()
    (root / "saves" / "world" ).mkdir()
    (root / "saves" / "world" / "level.dat").write_bytes(b"\x0a\x00")
    (root / "options.txt").write_text("lang:zh_cn", encoding="utf-8")
    (root / "versions").mkdir()
    (root / "versions" / "1.20.1.json").write_text("{}", encoding="utf-8")
    (root / "libraries").mkdir()
    (root / "libraries" / "x.jar").write_bytes(b"PK")
    (root / "logs").mkdir()
    (root / "logs" / "latest.log").write_text("log", encoding="utf-8")
    (root / ".instance.json").write_text("{}", encoding="utf-8")


class CandidateTests(unittest.TestCase):
    def test_lists_defaults_and_optionals(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_tree(root)
            rows = export_pack.list_export_candidates(_FakeInstance(root))
            paths = [r["path"] for r in rows]
            self.assertIn("mods", paths)
            self.assertIn("config", paths)
            self.assertIn("options.txt", paths)
            self.assertIn("saves", paths)
            # 游戏本体 / 日志 / 隐藏文件永不列出
            for banned in ("versions", "libraries", "logs", ".instance.json"):
                self.assertNotIn(banned, paths)
            by = {r["path"]: r for r in rows}
            self.assertTrue(by["mods"]["default"])
            self.assertTrue(by["options.txt"]["default"])
            self.assertFalse(by["saves"]["default"])
            self.assertEqual(by["mods"]["files"], 2)
            self.assertFalse(by["options.txt"]["dir"])
            # 默认勾选的排前面
            self.assertLess(paths.index("mods"), paths.index("saves"))

    def test_empty_dirs_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "resourcepacks").mkdir()
            rows = export_pack.list_export_candidates(_FakeInstance(root))
            self.assertEqual(rows, [])

    def test_missing_root(self):
        rows = export_pack.list_export_candidates(
            _FakeInstance(Path("/nonexistent/xyz")))
        self.assertEqual(rows, [])


class CollectIncludedTests(unittest.TestCase):
    def test_collects_dirs_and_files(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_tree(root)
            got = export_pack._collect_included(
                _FakeInstance(root), ["config", "options.txt", "missing"])
            rels = sorted(r for r, _p in got)
            self.assertEqual(rels, ["config/c.toml", "options.txt"])

    def test_excluded_names_ignored(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_tree(root)
            got = export_pack._collect_included(
                _FakeInstance(root), ["versions", "libraries"])
            self.assertEqual(got, [])


class MmcIncludeTests(unittest.TestCase):
    def test_include_subset(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_tree(root)
            inst = _FakeInstance(root, meta={"mc_version": "1.20.1"})
            path = export_pack.export_mmc_zip(
                inst, root / "out.zip", include=["mods", "saves"])
            with zipfile.ZipFile(path) as zf:
                names = set(zf.namelist())
            self.assertIn(".minecraft/mods/a.jar", names)
            self.assertIn(".minecraft/mods/cfg/a.toml", names)
            self.assertIn(".minecraft/saves/world/level.dat", names)
            self.assertNotIn(".minecraft/config/c.toml", names)
            self.assertNotIn(".minecraft/options.txt", names)

    def test_meta_override_changes_cfg_name(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            inst = _FakeInstance(root, meta={"mc_version": "1.19.4"})
            path = export_pack.export_mmc_zip(
                inst, root / "out.zip", include=[],
                meta_override={"name": "Renamed", "author": "me"})
            with zipfile.ZipFile(path) as zf:
                cfg = zf.read("instance.cfg").decode("utf-8")
            self.assertIn("name=Renamed", cfg)


class CfIncludeTests(unittest.TestCase):
    def test_mods_excluded_skips_fingerprinting(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_tree(root)
            inst = _FakeInstance(root, meta={"modpack": {
                "mc_version": "1.20.1", "loader": "forge",
                "loader_version": "47.2.0"}})
            from mclauncher import mods
            with patch.object(mods, "cf_match_fingerprints") as match:
                path = export_pack.export_cf_zip(
                    inst, root / "out.zip", dm=object(), include=["config"])
            match.assert_not_called()
            with zipfile.ZipFile(path) as zf:
                names = set(zf.namelist())
                manifest = json.loads(zf.read("manifest.json"))
            self.assertEqual(manifest["files"], [])
            self.assertIn("overrides/config/c.toml", names)
            self.assertNotIn("overrides/mods/a.jar", names)

    def test_mods_included_bundles_subdir_extras(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_tree(root)
            inst = _FakeInstance(root, meta={})
            from mclauncher import mods
            with patch.object(mods, "cf_match_fingerprints", return_value={}):
                path = export_pack.export_cf_zip(
                    inst, root / "out.zip", dm=object(), include=["mods"])
            with zipfile.ZipFile(path) as zf:
                names = sorted(zf.namelist())
            self.assertIn("overrides/mods/a.jar", names)
            self.assertIn("overrides/mods/cfg/a.toml", names)
            # 没有重复条目
            self.assertEqual(len(names), len(set(names)))
            self.assertNotIn("overrides/config/c.toml", names)

    def test_meta_override_in_manifest(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            inst = _FakeInstance(root, meta={"modpack": {
                "name": "Old", "version": "1.0", "mc_version": "1.20.1"}})
            path = export_pack.export_cf_zip(
                inst, root / "out.zip", dm=object(), include=[],
                meta_override={"name": "New", "version": "2.0", "author": "me"})
            with zipfile.ZipFile(path) as zf:
                manifest = json.loads(zf.read("manifest.json"))
            self.assertEqual(manifest["name"], "New")
            self.assertEqual(manifest["version"], "2.0")
            self.assertEqual(manifest["author"], "me")


class MrpackIncludeTests(unittest.TestCase):
    class _DM:
        def fetch_json(self, url, timeout=15):
            raise RuntimeError("offline")

    def test_include_and_meta(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_tree(root)
            inst = _FakeInstance(root, meta={"modpack": {
                "mc_version": "1.20.1", "loader": "fabric",
                "loader_version": "0.15.11"}})
            path = export_pack.export_mrpack(
                inst, root / "out.mrpack", dm=self._DM(),
                include=["mods", "options.txt"],
                meta_override={"name": "Sel", "version": "9.9"})
            with zipfile.ZipFile(path) as zf:
                names = set(zf.namelist())
                index = json.loads(zf.read("modrinth.index.json"))
            self.assertEqual(index["name"], "Sel")
            self.assertEqual(index["versionId"], "9.9")
            self.assertIn("overrides/mods/a.jar", names)
            self.assertIn("overrides/mods/cfg/a.toml", names)
            self.assertIn("overrides/options.txt", names)
            self.assertNotIn("overrides/config/c.toml", names)

    def test_default_behavior_unchanged_without_include(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_tree(root)
            inst = _FakeInstance(root, meta={})
            path = export_pack.export_mrpack(inst, root / "out.mrpack", dm=self._DM())
            with zipfile.ZipFile(path) as zf:
                names = set(zf.namelist())
            self.assertIn("overrides/mods/a.jar", names)
            self.assertIn("overrides/config/c.toml", names)
            # 老行为：mods 子目录额外文件与 saves 不进包
            self.assertNotIn("overrides/mods/cfg/a.toml", names)
            self.assertNotIn("overrides/saves/world/level.dat", names)


class FacadeTests(unittest.TestCase):
    def test_both_facades_have_wizard_api(self):
        import inspect
        from app.backend import BackendAPI as QtBackend
        from bridge.api import BackendAPI as BridgeBackend
        for cls in (QtBackend, BridgeBackend):
            sig = inspect.signature(cls.export_modpack)
            self.assertIn("include", sig.parameters)
            self.assertIn("meta", sig.parameters)
            self.assertTrue(callable(getattr(cls, "export_pack_info")))


if __name__ == "__main__":
    unittest.main()
