# -*- coding: utf-8 -*-
"""世界数据包管理：列出 / 启用禁用 / 删除（对齐 HMCL 世界管理的数据包页）。"""
from __future__ import annotations

import gzip
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from mclauncher import nbt_lite as nbt
from mclauncher import saves
from mclauncher.instances import Instance
from mclauncher.saves import SaveError


def level_bytes(datapacks=None, gzipped=True) -> bytes:
    """构造带 DataPacks 标签的 level.dat；datapacks=None 表示老存档没有该标签。"""
    data = {
        "LevelName": (nbt.TAG_STRING, "DP World"),
        "GameType": (nbt.TAG_INT, 0),
        "LastPlayed": (nbt.TAG_LONG, 1700000000000),
    }
    if datapacks is not None:
        enabled, disabled = datapacks
        data["DataPacks"] = (nbt.TAG_COMPOUND, {
            "Enabled": (nbt.TAG_LIST, (nbt.TAG_STRING, list(enabled))),
            "Disabled": (nbt.TAG_LIST, (nbt.TAG_STRING, list(disabled))),
        })
    raw = nbt.dumps({"Data": (nbt.TAG_COMPOUND, data)}, "")
    return gzip.compress(raw) if gzipped else raw


def make_zip_pack(path: Path, description="A test pack"):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("pack.mcmeta", json.dumps(
            {"pack": {"pack_format": 15, "description": description}}))
        zf.writestr("data/test/tags/blocks/x.json", "{}")


def read_dp_lists(level: Path):
    raw = level.read_bytes()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    _n, root = nbt.loads(raw)
    dp = root["Data"][1]["DataPacks"][1]
    def as_list(key):
        tag = dp.get(key)
        return [str(x) for x in tag[1][1]] if tag else []
    return as_list("Enabled"), as_list("Disabled")


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
        self.inst = Instance("dp-test")
        self.inst.create()

    def make_save(self, folder="DPWorld", datapacks=("vanilla",), disabled=(),
                  gzipped=True) -> Path:
        path = self.inst.path / "saves" / folder
        (path / "datapacks").mkdir(parents=True, exist_ok=True)
        packs = None if datapacks is None else (datapacks, disabled)
        (path / "level.dat").write_bytes(level_bytes(packs, gzipped))
        return path


class TestList(Sandbox):
    def test_list_with_status_and_description(self):
        folder = self.make_save(disabled=("file/off.zip",))
        make_zip_pack(folder / "datapacks" / "cool.zip", "很酷的数据包")
        make_zip_pack(folder / "datapacks" / "off.zip")
        # 文件夹形式的数据包
        dir_pack = folder / "datapacks" / "folderpack"
        dir_pack.mkdir()
        (dir_pack / "pack.mcmeta").write_text(json.dumps(
            {"pack": {"pack_format": 15,
                      "description": {"text": "chat ", "extra": ["组件"]}}}),
            encoding="utf-8")
        # 非数据包文件应被忽略
        (folder / "datapacks" / "readme.txt").write_text("x", encoding="utf-8")

        rows = saves.list_world_datapacks(self.inst, "DPWorld")
        by_name = {r["filename"]: r for r in rows}
        self.assertEqual(set(by_name), {"cool.zip", "off.zip", "folderpack"})
        self.assertTrue(by_name["cool.zip"]["enabled"])
        self.assertFalse(by_name["off.zip"]["enabled"])
        self.assertTrue(by_name["folderpack"]["enabled"])
        self.assertEqual(by_name["cool.zip"]["description"], "很酷的数据包")
        self.assertEqual(by_name["folderpack"]["description"], "chat 组件")
        self.assertGreater(by_name["cool.zip"]["bytes"], 0)

    def test_empty_and_missing(self):
        self.make_save()
        self.assertEqual(saves.list_world_datapacks(self.inst, "DPWorld"), [])
        with self.assertRaises(SaveError):
            saves.list_world_datapacks(self.inst, "no-such")
        with self.assertRaises(SaveError):
            saves.list_world_datapacks(self.inst, "../escape")

    def test_no_level_dat_still_lists(self):
        folder = self.inst.path / "saves" / "Bare" / "datapacks"
        folder.mkdir(parents=True)
        make_zip_pack(folder / "p.zip")
        rows = saves.list_world_datapacks(self.inst, "Bare")
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["enabled"])


class TestToggle(Sandbox):
    def test_disable_then_enable(self):
        folder = self.make_save(datapacks=("vanilla", "file/p.zip"))
        make_zip_pack(folder / "datapacks" / "p.zip")

        saves.set_world_datapack_enabled(self.inst, "DPWorld", "p.zip", False)
        enabled, disabled = read_dp_lists(folder / "level.dat")
        self.assertNotIn("file/p.zip", enabled)
        self.assertIn("file/p.zip", disabled)
        self.assertIn("vanilla", enabled, "不能动 vanilla 等内置包")
        rows = saves.list_world_datapacks(self.inst, "DPWorld")
        self.assertFalse(rows[0]["enabled"])

        saves.set_world_datapack_enabled(self.inst, "DPWorld", "p.zip", True)
        enabled, disabled = read_dp_lists(folder / "level.dat")
        self.assertIn("file/p.zip", enabled)
        self.assertNotIn("file/p.zip", disabled)

    def test_preserves_gzip_and_backup(self):
        folder = self.make_save()
        make_zip_pack(folder / "datapacks" / "p.zip")
        before = (folder / "level.dat").read_bytes()
        saves.set_world_datapack_enabled(self.inst, "DPWorld", "p.zip", False)
        raw = (folder / "level.dat").read_bytes()
        self.assertEqual(raw[:2], b"\x1f\x8b", "应保持 gzip 压缩")
        self.assertEqual((folder / "level.dat_old").read_bytes(), before)

    def test_errors(self):
        folder = self.make_save()
        with self.assertRaises(SaveError):
            saves.set_world_datapack_enabled(self.inst, "DPWorld", "nope.zip", False)
        with self.assertRaises(SaveError):
            saves.set_world_datapack_enabled(self.inst, "DPWorld", "../../evil", False)
        # 老存档（无 DataPacks 标签）应报错而不是静默成功
        old = self.make_save(folder="OldWorld", datapacks=None)
        make_zip_pack(old / "datapacks" / "p.zip")
        with self.assertRaises(SaveError):
            saves.set_world_datapack_enabled(self.inst, "OldWorld", "p.zip", False)


class TestDelete(Sandbox):
    def test_delete_zip_cleans_lists(self):
        folder = self.make_save(datapacks=("vanilla", "file/p.zip"),
                                disabled=("file/gone.zip",))
        make_zip_pack(folder / "datapacks" / "p.zip")
        make_zip_pack(folder / "datapacks" / "gone.zip")

        saves.delete_world_datapack(self.inst, "DPWorld", "p.zip")
        self.assertFalse((folder / "datapacks" / "p.zip").exists())
        enabled, disabled = read_dp_lists(folder / "level.dat")
        self.assertNotIn("file/p.zip", enabled)

        saves.delete_world_datapack(self.inst, "DPWorld", "gone.zip")
        enabled, disabled = read_dp_lists(folder / "level.dat")
        self.assertNotIn("file/gone.zip", disabled)

    def test_delete_directory_pack(self):
        folder = self.make_save()
        dir_pack = folder / "datapacks" / "dpack"
        dir_pack.mkdir()
        (dir_pack / "pack.mcmeta").write_text("{}", encoding="utf-8")
        saves.delete_world_datapack(self.inst, "DPWorld", "dpack")
        self.assertFalse(dir_pack.exists())

    def test_delete_missing_raises(self):
        self.make_save()
        with self.assertRaises(SaveError):
            saves.delete_world_datapack(self.inst, "DPWorld", "nope.zip")


class TestBridgeFacade(Sandbox):
    def test_bridge_methods(self):
        folder = self.make_save()
        make_zip_pack(folder / "datapacks" / "p.zip")
        from bridge.api import BackendAPI
        api = BackendAPI.__new__(BackendAPI)
        api._instance = lambda name: self.inst
        rows = api.list_world_datapacks("dp-test", "DPWorld")
        self.assertEqual(rows[0]["filename"], "p.zip")
        self.assertFalse(api.set_world_datapack_enabled(
            "dp-test", "DPWorld", "p.zip", False))
        self.assertFalse(api.list_world_datapacks("dp-test", "DPWorld")[0]["enabled"])
        api.delete_world_datapack("dp-test", "DPWorld", "p.zip")
        self.assertEqual(api.list_world_datapacks("dp-test", "DPWorld"), [])


if __name__ == "__main__":
    unittest.main()
