# -*- coding: utf-8 -*-
"""世界信息修改：world_info / edit_world（对齐 HMCL 世界管理）。"""
from __future__ import annotations

import gzip
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mclauncher import nbt_lite as nbt
from mclauncher import saves
from mclauncher.instances import Instance
from mclauncher.saves import SaveError


def level_bytes(name="My World", game_type=0, difficulty=2, cheats=0, hardcore=0,
                seed=987654321, modern=True, gzipped=True, extra=None) -> bytes:
    data = {
        "LevelName": (nbt.TAG_STRING, name),
        "GameType": (nbt.TAG_INT, game_type),
        "Difficulty": (nbt.TAG_BYTE, difficulty),
        "DifficultyLocked": (nbt.TAG_BYTE, 0),
        "allowCommands": (nbt.TAG_BYTE, cheats),
        "hardcore": (nbt.TAG_BYTE, hardcore),
        "LastPlayed": (nbt.TAG_LONG, 1700000000000),
        "Version": (nbt.TAG_COMPOUND, {"Name": (nbt.TAG_STRING, "1.20.4")}),
    }
    if modern:
        data["WorldGenSettings"] = (nbt.TAG_COMPOUND, {"seed": (nbt.TAG_LONG, seed)})
    else:
        data["RandomSeed"] = (nbt.TAG_LONG, seed)
    if extra:
        data.update(extra)
    raw = nbt.dumps({"Data": (nbt.TAG_COMPOUND, data)}, "")
    return gzip.compress(raw) if gzipped else raw


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
        self.inst = Instance("world-test")
        self.inst.create()

    def make_save(self, folder="TestWorld", **level_kwargs) -> Path:
        path = self.inst.path / "saves" / folder
        path.mkdir(parents=True, exist_ok=True)
        (path / "level.dat").write_bytes(level_bytes(**level_kwargs))
        return path


class TestWorldInfo(Sandbox):
    def test_read_full_info(self):
        self.make_save(name="旧的世界", game_type=1, difficulty=3, cheats=1)
        info = saves.world_info(self.inst, "TestWorld")
        self.assertEqual(info["level_name"], "旧的世界")
        self.assertEqual(info["game_type"], 1)
        self.assertEqual(info["difficulty"], 3)
        self.assertTrue(info["cheats"])
        self.assertFalse(info["hardcore"])
        self.assertEqual(info["seed"], 987654321)
        self.assertEqual(info["version_name"], "1.20.4")
        self.assertEqual(info["name"], "TestWorld")

    def test_legacy_seed_from_random_seed(self):
        self.make_save(modern=False, seed=42)
        info = saves.world_info(self.inst, "TestWorld")
        self.assertEqual(info["seed"], 42)

    def test_missing_save_raises(self):
        with self.assertRaises(SaveError):
            saves.world_info(self.inst, "no-such-world")

    def test_traversal_rejected(self):
        with self.assertRaises(SaveError):
            saves.world_info(self.inst, "../outside")


class TestEditWorld(Sandbox):
    def test_edit_basic_fields(self):
        self.make_save()
        info = saves.edit_world(self.inst, "TestWorld", {
            "level_name": "改名之后",
            "game_type": 1,
            "difficulty": 0,
            "cheats": True,
            "hardcore": True,
            "difficulty_locked": True,
        })
        self.assertEqual(info["level_name"], "改名之后")
        self.assertEqual(info["game_type"], 1)
        self.assertEqual(info["difficulty"], 0)
        self.assertTrue(info["cheats"])
        self.assertTrue(info["hardcore"])
        self.assertTrue(info["difficulty_locked"])
        # 重新从磁盘读，确认真的写进 level.dat
        again = saves.world_info(self.inst, "TestWorld")
        self.assertEqual(again["level_name"], "改名之后")

    def test_preserves_unknown_tags_and_gzip(self):
        extra = {
            "CustomBossEvents": (nbt.TAG_COMPOUND, {
                "id": (nbt.TAG_STRING, "boss"),
            }),
            "ScheduledEvents": (nbt.TAG_LIST, (nbt.TAG_STRING, ["a", "b"])),
            "BorderSize": (nbt.TAG_DOUBLE, 5999.5),
        }
        folder = self.make_save(extra=extra)
        saves.edit_world(self.inst, "TestWorld", {"game_type": 2})
        raw = (folder / "level.dat").read_bytes()
        self.assertEqual(raw[:2], b"\x1f\x8b", "level.dat 应保持 gzip 压缩")
        _n, root = nbt.loads(raw)
        data = root["Data"][1]
        self.assertEqual(data["GameType"], (nbt.TAG_INT, 2))
        self.assertEqual(data["CustomBossEvents"], extra["CustomBossEvents"])
        self.assertEqual(data["ScheduledEvents"], extra["ScheduledEvents"])
        self.assertEqual(data["BorderSize"], extra["BorderSize"])
        # 未压缩的 level.dat（极老存档）也保持未压缩
        (folder / "level.dat").write_bytes(level_bytes(gzipped=False))
        saves.edit_world(self.inst, "TestWorld", {"game_type": 3})
        raw2 = (folder / "level.dat").read_bytes()
        self.assertNotEqual(raw2[:2], b"\x1f\x8b")
        self.assertEqual(nbt.loads(raw2)[1]["Data"][1]["GameType"], (nbt.TAG_INT, 3))

    def test_backs_up_to_level_dat_old(self):
        folder = self.make_save(name="Before")
        original = (folder / "level.dat").read_bytes()
        saves.edit_world(self.inst, "TestWorld", {"level_name": "After"})
        old = folder / "level.dat_old"
        self.assertTrue(old.is_file())
        self.assertEqual(old.read_bytes(), original)

    def test_adds_missing_difficulty_tag(self):
        """老存档没有 Difficulty 标签时，编辑难度应新增该标签。"""
        folder = self.make_save()
        raw = gzip.decompress((folder / "level.dat").read_bytes())
        _n, root = nbt.loads(raw)
        root["Data"][1].pop("Difficulty")
        (folder / "level.dat").write_bytes(gzip.compress(nbt.dumps(root, "")))
        self.assertIsNone(saves.world_info(self.inst, "TestWorld")["difficulty"])
        info = saves.edit_world(self.inst, "TestWorld", {"difficulty": 1})
        self.assertEqual(info["difficulty"], 1)

    def test_validation(self):
        self.make_save()
        with self.assertRaises(SaveError):
            saves.edit_world(self.inst, "TestWorld", {"game_type": 9})
        with self.assertRaises(SaveError):
            saves.edit_world(self.inst, "TestWorld", {"difficulty": -1})
        with self.assertRaises(SaveError):
            saves.edit_world(self.inst, "TestWorld", {"level_name": "   "})
        with self.assertRaises(SaveError):
            saves.edit_world(self.inst, "TestWorld", {"not_a_field": 1})
        # 全部失败后文件应保持原样
        info = saves.world_info(self.inst, "TestWorld")
        self.assertEqual(info["level_name"], "My World")

    def test_empty_changes_noop(self):
        folder = self.make_save()
        before = (folder / "level.dat").read_bytes()
        info = saves.edit_world(self.inst, "TestWorld", {})
        self.assertEqual(info["level_name"], "My World")
        self.assertEqual((folder / "level.dat").read_bytes(), before)
        self.assertFalse((folder / "level.dat_old").exists())

    def test_missing_level_dat(self):
        folder = self.inst.path / "saves" / "Broken"
        folder.mkdir(parents=True)
        with self.assertRaises(SaveError):
            saves.edit_world(self.inst, "Broken", {"cheats": True})

    def test_version_isolated_dir(self):
        from mclauncher import version_settings as vs
        vid = "1.20.4-fabric"
        vdir = self.inst.versions_dir() / vid
        vdir.mkdir(parents=True)
        (vdir / f"{vid}.json").write_text("{}", encoding="utf-8")
        vs.save(self.inst, vid, {"isolation": "all"})
        gdir = vs.game_dir(self.inst, vid)
        folder = gdir / "saves" / "IsoWorld"
        folder.mkdir(parents=True)
        (folder / "level.dat").write_bytes(level_bytes(name="隔离世界"))
        info = saves.world_info(self.inst, "IsoWorld", vid)
        self.assertEqual(info["level_name"], "隔离世界")
        saves.edit_world(self.inst, "IsoWorld", {"cheats": True}, vid)
        self.assertTrue(saves.world_info(self.inst, "IsoWorld", vid)["cheats"])


class TestBridgeFacade(Sandbox):
    def test_bridge_methods(self):
        self.make_save()
        from bridge.api import BackendAPI
        api = BackendAPI.__new__(BackendAPI)
        api._instance = lambda name: self.inst
        info = api.get_world_info("world-test", "TestWorld")
        self.assertEqual(info["level_name"], "My World")
        out = api.edit_world("world-test", "TestWorld", {"game_type": 3})
        self.assertEqual(out["game_type"], 3)


if __name__ == "__main__":
    unittest.main()
