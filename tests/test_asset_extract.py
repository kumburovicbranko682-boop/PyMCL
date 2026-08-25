# -*- coding: utf-8 -*-
"""游戏资源提取：索引解析、类别过滤、按真实文件名导出。"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mclauncher import asset_extract


class _Inst:
    def __init__(self, root: Path, version_jsons: dict):
        self.root = root
        self._jsons = version_jsons

    def assets_dir(self):
        return self.root / "assets"

    def version_json(self, vid):
        return self._jsons.get(vid)


def _setup(root: Path):
    objects = {
        "minecraft/sounds/music/game/calm1.ogg": {"hash": "a" * 40, "size": 100},
        "minecraft/sounds/records/cat.ogg": {"hash": "b" * 40, "size": 200},
        "minecraft/sounds/mob/cow/say1.ogg": {"hash": "c" * 40, "size": 50},
        "minecraft/lang/zh_cn.json": {"hash": "d" * 40, "size": 300},
        "icons/icon_32x32.png": {"hash": "e" * 40, "size": 10},
    }
    idx_dir = root / "assets" / "indexes"
    idx_dir.mkdir(parents=True)
    (idx_dir / "17.json").write_text(json.dumps({"objects": objects}), encoding="utf-8")
    # 只落地一部分对象，calm1 与 zh_cn 存在，其余缺失
    for h, content in (("a" * 40, b"OGG1"), ("d" * 40, b"{}")):
        p = root / "assets" / "objects" / h[:2] / h
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
    return _Inst(root, {
        "1.20.1": {"id": "1.20.1", "assetIndex": {"id": "17"}},
        "1.20.1-fabric": {"id": "1.20.1-fabric", "inheritsFrom": "1.20.1"},
        "legacy": {"id": "legacy", "assets": "17"},
        "broken": {"id": "broken"},
    })


class ListAssetsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.inst = _setup(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_music_category(self):
        rows = asset_extract.list_assets(self.inst, "1.20.1", category="music")
        self.assertEqual([r["name"] for r in rows],
                         ["minecraft/sounds/music/game/calm1.ogg"])
        self.assertTrue(rows[0]["present"])

    def test_sounds_includes_all_audio(self):
        rows = asset_extract.list_assets(self.inst, "1.20.1", category="sounds")
        self.assertEqual(len(rows), 3)

    def test_query_filter(self):
        rows = asset_extract.list_assets(self.inst, "1.20.1",
                                         category="all", query="zh_cn")
        self.assertEqual([r["name"] for r in rows], ["minecraft/lang/zh_cn.json"])

    def test_missing_object_marked(self):
        rows = asset_extract.list_assets(self.inst, "1.20.1", category="records")
        self.assertFalse(rows[0]["present"])

    def test_inherited_version_resolves_parent_index(self):
        rows = asset_extract.list_assets(self.inst, "1.20.1-fabric", category="music")
        self.assertEqual(len(rows), 1)

    def test_legacy_assets_field(self):
        rows = asset_extract.list_assets(self.inst, "legacy", category="lang")
        self.assertEqual(len(rows), 1)

    def test_no_index_raises(self):
        with self.assertRaises(asset_extract.AssetExtractError):
            asset_extract.list_assets(self.inst, "broken")
        with self.assertRaises(asset_extract.AssetExtractError):
            asset_extract.list_assets(self.inst, "not-installed")


class ExtractTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.inst = _setup(Path(self.tmp.name))
        self.dest = Path(self.tmp.name) / "out"

    def tearDown(self):
        self.tmp.cleanup()

    def test_extract_restores_real_names(self):
        result = asset_extract.extract_assets(
            self.inst, "1.20.1",
            ["minecraft/sounds/music/game/calm1.ogg", "minecraft/lang/zh_cn.json"],
            self.dest)
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["skipped"], [])
        self.assertEqual(
            (self.dest / "minecraft/sounds/music/game/calm1.ogg").read_bytes(), b"OGG1")
        self.assertEqual(
            (self.dest / "minecraft/lang/zh_cn.json").read_bytes(), b"{}")

    def test_missing_objects_skipped(self):
        result = asset_extract.extract_assets(
            self.inst, "1.20.1",
            ["minecraft/sounds/music/game/calm1.ogg",
             "minecraft/sounds/records/cat.ogg"],
            self.dest)
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["skipped"], ["minecraft/sounds/records/cat.ogg"])

    def test_all_missing_raises(self):
        with self.assertRaises(asset_extract.AssetExtractError):
            asset_extract.extract_assets(
                self.inst, "1.20.1", ["minecraft/sounds/records/cat.ogg"], self.dest)

    def test_empty_selection_raises(self):
        with self.assertRaises(asset_extract.AssetExtractError):
            asset_extract.extract_assets(self.inst, "1.20.1", [], self.dest)


if __name__ == "__main__":
    unittest.main()
