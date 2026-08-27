# -*- coding: utf-8 -*-
"""NBT 带类型读写 + 世界信息编辑（HMCL WorldInfoPage 同款字段）。"""

import gzip
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import PropertyMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mclauncher import nbt, saves  # noqa: E402
from mclauncher.config import CONFIG  # noqa: E402


def _level_root(name="旧世界", gametype=0, hardcore=0, cheats=0, diff=2,
                with_player=True) -> tuple:
    data = {
        "LevelName": (nbt.TAG_STRING, name),
        "GameType": (nbt.TAG_INT, gametype),
        "hardcore": (nbt.TAG_BYTE, hardcore),
        "allowCommands": (nbt.TAG_BYTE, cheats),
        "Difficulty": (nbt.TAG_BYTE, diff),
        "RandomSeed": (nbt.TAG_LONG, 123456789),
        "LastPlayed": (nbt.TAG_LONG, 1700000000000),
        "Version": (nbt.TAG_COMPOUND, {
            "Name": (nbt.TAG_STRING, "1.20.1"),
            "Id": (nbt.TAG_INT, 3465),
        }),
        "ServerBrands": (nbt.TAG_LIST, (nbt.TAG_STRING, ["vanilla"])),
        "Pos": (nbt.TAG_LIST, (nbt.TAG_DOUBLE, [1.5, 64.0, -7.25])),
        "SomeInts": (nbt.TAG_INT_ARRAY, [1, -2, 3]),
        "SomeBytes": (nbt.TAG_BYTE_ARRAY, [0, 255, 7]),
        "SomeLongs": (nbt.TAG_LONG_ARRAY, [2 ** 40, -5]),
    }
    if with_player:
        data["Player"] = (nbt.TAG_COMPOUND, {
            "playerGameType": (nbt.TAG_INT, gametype),
            "Health": (nbt.TAG_FLOAT, 20.0),
            "Score": (nbt.TAG_SHORT, 3),
        })
    return "", (nbt.TAG_COMPOUND, {"Data": (nbt.TAG_COMPOUND, data)})


class TypedRoundTripTests(unittest.TestCase):
    def test_round_trip_gzip(self):
        root_name, root = _level_root()
        blob = nbt.dumps_typed(root_name, root, compress=True)
        self.assertEqual(blob[:2], b"\x1f\x8b")
        name2, root2 = nbt.loads_typed(blob)
        self.assertEqual(name2, root_name)
        self.assertEqual(root2, root)

    def test_round_trip_uncompressed(self):
        root_name, root = _level_root()
        blob = nbt.dumps_typed(root_name, root, compress=False)
        self.assertNotEqual(blob[:2], b"\x1f\x8b")
        self.assertEqual(nbt.loads_typed(blob)[1], root)

    def test_untyped_reader_still_parses_writer_output(self):
        blob = nbt.dumps_typed(*_level_root(), compress=True)
        plain = nbt.loads(blob)
        self.assertEqual(plain["Data"]["LevelName"], "旧世界")
        self.assertEqual(plain["Data"]["SomeLongs"], [2 ** 40, -5])
        self.assertEqual(plain["Data"]["Pos"], [1.5, 64.0, -7.25])

    def test_root_must_be_compound(self):
        with self.assertRaises(nbt.NBTError):
            nbt.dumps_typed("", (nbt.TAG_INT, 1))


class EditWorldTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        self.addCleanup(self._td.cleanup)
        patcher = patch.object(type(CONFIG), "instances_dir",
                               new_callable=PropertyMock,
                               return_value=self.root)
        patcher.start()
        self.addCleanup(patcher.stop)
        from mclauncher.instances import Instance
        (self.root / "inst").mkdir(parents=True)
        self.inst = Instance("inst")
        self.save_dir = self.root / "inst" / "saves" / "myworld"
        self.save_dir.mkdir(parents=True)
        self.level = self.save_dir / "level.dat"
        self.level.write_bytes(nbt.dumps_typed(*_level_root(), compress=True))

    def test_rename_and_toggle_cheats(self):
        out = saves.edit_world(self.inst, "myworld", {
            "level_name": "新世界", "allow_cheats": True,
        })
        self.assertEqual(out["level_name"], "新世界")
        self.assertTrue(out["cheats"])
        # 备份留档
        self.assertTrue((self.save_dir / "level.dat.pymcl_bak").is_file())
        # 其余字段不动
        plain = nbt.read_file(self.level)
        self.assertEqual(plain["Data"]["RandomSeed"], 123456789)
        self.assertEqual(plain["Data"]["Version"]["Name"], "1.20.1")
        # 仍是 gzip
        self.assertEqual(self.level.read_bytes()[:2], b"\x1f\x8b")

    def test_game_mode_updates_player_too(self):
        saves.edit_world(self.inst, "myworld", {"game_mode": "creative"})
        plain = nbt.read_file(self.level)
        self.assertEqual(plain["Data"]["GameType"], 1)
        self.assertEqual(plain["Data"]["hardcore"], 0)
        self.assertEqual(plain["Data"]["Player"]["playerGameType"], 1)

    def test_hardcore_is_survival_plus_flag(self):
        saves.edit_world(self.inst, "myworld", {"game_mode": "hardcore"})
        plain = nbt.read_file(self.level)
        self.assertEqual(plain["Data"]["GameType"], 0)
        self.assertEqual(plain["Data"]["hardcore"], 1)
        self.assertEqual(plain["Data"]["Player"]["playerGameType"], 0)

    def test_difficulty_and_lock(self):
        out = saves.edit_world(self.inst, "myworld", {
            "difficulty": 0, "difficulty_locked": True,
        })
        self.assertEqual(out["difficulty"], "和平")
        self.assertTrue(out["difficulty_locked"])

    def test_numeric_game_mode(self):
        saves.edit_world(self.inst, "myworld", {"game_mode": 3})
        plain = nbt.read_file(self.level)
        self.assertEqual(plain["Data"]["GameType"], 3)

    def test_empty_name_rejected(self):
        with self.assertRaises(saves.SaveError):
            saves.edit_world(self.inst, "myworld", {"level_name": "  "})

    def test_bad_difficulty_rejected(self):
        with self.assertRaises(saves.SaveError):
            saves.edit_world(self.inst, "myworld", {"difficulty": 9})

    def test_bad_mode_rejected(self):
        with self.assertRaises(saves.SaveError):
            saves.edit_world(self.inst, "myworld", {"game_mode": "peaceful?"})

    def test_missing_level_dat(self):
        (self.save_dir.parent / "empty").mkdir()
        with self.assertRaises(saves.SaveError):
            saves.edit_world(self.inst, "empty", {"level_name": "x"})

    def test_path_traversal_blocked(self):
        with self.assertRaises(saves.SaveError):
            saves.edit_world(self.inst, "../myworld", {"level_name": "x"})

    def test_uncompressed_level_dat_stays_uncompressed(self):
        self.level.write_bytes(nbt.dumps_typed(*_level_root(), compress=False))
        saves.edit_world(self.inst, "myworld", {"level_name": "平文件"})
        raw = self.level.read_bytes()
        self.assertNotEqual(raw[:2], b"\x1f\x8b")
        self.assertEqual(nbt.loads(raw)["Data"]["LevelName"], "平文件")

    def test_summary_reports_lock_fields(self):
        info = saves.level_summary(self.save_dir)
        self.assertEqual(info["difficulty_code"], 2)
        self.assertFalse(info["difficulty_locked"])


class FacadeParityTests(unittest.TestCase):
    def test_both_facades_have_edit_world(self):
        from app.backend import BackendAPI as QtBackend
        from bridge.api import BackendAPI as BridgeBackend
        import inspect
        for cls in (QtBackend, BridgeBackend):
            fn = getattr(cls, "edit_world", None)
            self.assertTrue(callable(fn), cls)
            params = list(inspect.signature(fn).parameters)
            self.assertEqual(params[1:], ["instance", "name", "changes", "version"])


if __name__ == "__main__":
    unittest.main()
