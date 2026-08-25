# -*- coding: utf-8 -*-
"""世界内数据包管理：列表（含 pack.mcmeta 描述与 level.dat 启用状态）与删除。"""

import gzip
import json
import os
import struct
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import PropertyMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mclauncher import instances, saves  # noqa: E402
from mclauncher.config import CONFIG  # noqa: E402


def _nbt_str(s: str) -> bytes:
    b = s.encode("utf-8")
    return struct.pack(">H", len(b)) + b


def _nbt_str_list(name: str, values: list) -> bytes:
    out = b"\x09" + _nbt_str(name) + b"\x08" + struct.pack(">i", len(values))
    for v in values:
        out += _nbt_str(v)
    return out


def _level_dat(enabled: list, disabled: list) -> bytes:
    """手工构造最小 level.dat：Data.DataPacks.Enabled/Disabled。"""
    inner = _nbt_str_list("Enabled", enabled) + _nbt_str_list("Disabled", disabled)
    packs = b"\x0a" + _nbt_str("DataPacks") + inner + b"\x00"
    data = b"\x0a" + _nbt_str("Data") + packs + b"\x00"
    root = b"\x0a" + _nbt_str("") + data + b"\x00"
    return gzip.compress(root)


def _zip_pack(path: Path, description="A test pack", pack_format=15):
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("pack.mcmeta", json.dumps(
            {"pack": {"description": description, "pack_format": pack_format}}))
        zf.writestr("data/test/functions/hi.mcfunction", "say hi")
    return path


class WorldDatapackTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        env = patch.dict(os.environ, {"XDG_DATA_HOME": str(self.root / "xdg")})
        env.start()
        self.addCleanup(env.stop)
        patcher = patch.object(type(CONFIG), "instances_dir",
                               new_callable=PropertyMock,
                               return_value=self.root / "instances")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._td.cleanup)
        (self.root / "instances").mkdir()
        self.inst = instances.Instance("测试")
        self.inst.create()
        self.world = self.inst.path / "saves" / "MyWorld"
        (self.world / "datapacks").mkdir(parents=True)

    def test_lists_zip_and_folder_packs(self):
        _zip_pack(self.world / "datapacks" / "cool.zip", description="Cool pack")
        folder = self.world / "datapacks" / "folderpack"
        folder.mkdir()
        (folder / "pack.mcmeta").write_text(json.dumps(
            {"pack": {"description": {"text": "Folder pack"}, "pack_format": 12}}),
            encoding="utf-8")
        (folder / "data").mkdir()
        (self.world / "datapacks" / "readme.txt").write_text("junk", "utf-8")

        rows = saves.list_world_datapacks(self.inst, "MyWorld")
        names = [r["name"] for r in rows]
        self.assertEqual(names, ["cool.zip", "folderpack"])
        by = {r["name"]: r for r in rows}
        self.assertEqual(by["cool.zip"]["description"], "Cool pack")
        self.assertEqual(by["cool.zip"]["pack_format"], 15)
        self.assertFalse(by["cool.zip"]["is_dir"])
        self.assertGreater(by["cool.zip"]["bytes"], 0)
        self.assertEqual(by["folderpack"]["description"], "Folder pack")
        self.assertTrue(by["folderpack"]["is_dir"])

    def test_enabled_state_from_level_dat(self):
        _zip_pack(self.world / "datapacks" / "on.zip")
        _zip_pack(self.world / "datapacks" / "off.zip")
        _zip_pack(self.world / "datapacks" / "unknown.zip")
        (self.world / "level.dat").write_bytes(
            _level_dat(["vanilla", "file/on.zip"], ["file/off.zip"]))
        by = {r["name"]: r for r in saves.list_world_datapacks(self.inst, "MyWorld")}
        self.assertIs(by["on.zip"]["enabled"], True)
        self.assertIs(by["off.zip"]["enabled"], False)
        self.assertIsNone(by["unknown.zip"]["enabled"])

    def test_no_level_dat_means_unknown_state(self):
        _zip_pack(self.world / "datapacks" / "p.zip")
        rows = saves.list_world_datapacks(self.inst, "MyWorld")
        self.assertIsNone(rows[0]["enabled"])

    def test_component_list_description(self):
        _zip_pack(self.world / "datapacks" / "c.zip",
                  description=[{"text": "Part1 "}, "Part2"])
        rows = saves.list_world_datapacks(self.inst, "MyWorld")
        self.assertEqual(rows[0]["description"], "Part1 Part2")

    def test_corrupt_pack_mcmeta_tolerated(self):
        p = self.world / "datapacks" / "bad.zip"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"not a zip")
        rows = saves.list_world_datapacks(self.inst, "MyWorld")
        self.assertEqual(rows[0]["name"], "bad.zip")
        self.assertEqual(rows[0]["description"], "")

    def test_missing_world_raises(self):
        with self.assertRaises(saves.SaveError):
            saves.list_world_datapacks(self.inst, "Nope")

    def test_world_without_datapacks_dir(self):
        world2 = self.inst.path / "saves" / "Empty"
        world2.mkdir(parents=True)
        self.assertEqual(saves.list_world_datapacks(self.inst, "Empty"), [])

    def test_delete_goes_to_trash(self):
        _zip_pack(self.world / "datapacks" / "gone.zip")
        saves.delete_world_datapack(self.inst, "MyWorld", "gone.zip")
        self.assertFalse((self.world / "datapacks" / "gone.zip").exists())
        trash_files = self.root / "xdg" / "Trash" / "files"
        self.assertTrue((trash_files / "gone.zip").is_file())

    def test_delete_missing_raises(self):
        with self.assertRaises(saves.SaveError):
            saves.delete_world_datapack(self.inst, "MyWorld", "nope.zip")

    def test_delete_path_traversal_rejected(self):
        with self.assertRaises(saves.SaveError):
            saves.delete_world_datapack(self.inst, "MyWorld", "../level.dat")

    def test_facades_have_methods(self):
        from app.backend import BackendAPI as QtBackend
        from bridge.api import BackendAPI as Bridge
        for cls in (QtBackend, Bridge):
            for name in ("list_world_datapacks", "delete_world_datapack"):
                self.assertTrue(callable(getattr(cls, name, None)), (cls, name))


if __name__ == "__main__":
    unittest.main()
