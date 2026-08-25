from __future__ import annotations

import gzip
import struct
import tempfile
import unittest
from pathlib import Path

from mclauncher import nbt
from mclauncher.saves import level_summary


# ---------------------------------------------------------------- 测试用 NBT 写入器

def _tag_string(s: str) -> bytes:
    raw = s.encode("utf-8")
    return struct.pack(">H", len(raw)) + raw


def _named(tag_id: int, name: str, payload: bytes) -> bytes:
    return struct.pack(">B", tag_id) + _tag_string(name) + payload


def _compound(*children: bytes) -> bytes:
    return b"".join(children) + b"\x00"


def _level_dat_bytes(*, name="My World", version="1.20.1", game_type=1,
                     difficulty=2, hardcore=0, cheats=1, seed=12345,
                     last_played_ms=1700000000000, seed_in_wgs=True) -> bytes:
    data_children = [
        _named(nbt.TAG_STRING, "LevelName", _tag_string(name)),
        _named(nbt.TAG_INT, "GameType", struct.pack(">i", game_type)),
        _named(nbt.TAG_BYTE, "Difficulty", struct.pack(">b", difficulty)),
        _named(nbt.TAG_BYTE, "hardcore", struct.pack(">b", hardcore)),
        _named(nbt.TAG_BYTE, "allowCommands", struct.pack(">b", cheats)),
        _named(nbt.TAG_LONG, "LastPlayed", struct.pack(">q", last_played_ms)),
        _named(nbt.TAG_COMPOUND, "Version",
               _compound(_named(nbt.TAG_STRING, "Name", _tag_string(version)))),
    ]
    if seed_in_wgs:
        data_children.append(_named(
            nbt.TAG_COMPOUND, "WorldGenSettings",
            _compound(_named(nbt.TAG_LONG, "seed", struct.pack(">q", seed)))))
    else:
        data_children.append(_named(nbt.TAG_LONG, "RandomSeed", struct.pack(">q", seed)))
    root = _named(nbt.TAG_COMPOUND, "", _compound(
        _named(nbt.TAG_COMPOUND, "Data", _compound(*data_children))))
    return gzip.compress(root)


class NbtReaderTests(unittest.TestCase):
    def test_reads_all_scalar_types(self):
        payload = _compound(
            _named(nbt.TAG_BYTE, "b", struct.pack(">b", -3)),
            _named(nbt.TAG_SHORT, "s", struct.pack(">h", 300)),
            _named(nbt.TAG_INT, "i", struct.pack(">i", 70000)),
            _named(nbt.TAG_LONG, "l", struct.pack(">q", 1 << 40)),
            _named(nbt.TAG_FLOAT, "f", struct.pack(">f", 1.5)),
            _named(nbt.TAG_DOUBLE, "d", struct.pack(">d", 2.25)),
            _named(nbt.TAG_STRING, "str", _tag_string("你好")),
            _named(nbt.TAG_INT_ARRAY, "ia", struct.pack(">i", 2) + struct.pack(">2i", 7, 8)),
            _named(nbt.TAG_LIST, "list",
                   struct.pack(">B", nbt.TAG_INT) + struct.pack(">i", 2)
                   + struct.pack(">i", 1) + struct.pack(">i", 2)),
        )
        raw = _named(nbt.TAG_COMPOUND, "", payload)
        out = nbt.loads(raw)
        self.assertEqual(out["b"], -3)
        self.assertEqual(out["s"], 300)
        self.assertEqual(out["i"], 70000)
        self.assertEqual(out["l"], 1 << 40)
        self.assertAlmostEqual(out["f"], 1.5)
        self.assertAlmostEqual(out["d"], 2.25)
        self.assertEqual(out["str"], "你好")
        self.assertEqual(out["ia"], [7, 8])
        self.assertEqual(out["list"], [1, 2])

    def test_gzip_auto_detect(self):
        raw = _named(nbt.TAG_COMPOUND, "",
                     _compound(_named(nbt.TAG_INT, "x", struct.pack(">i", 9))))
        self.assertEqual(nbt.loads(gzip.compress(raw))["x"], 9)

    def test_truncated_raises(self):
        raw = _named(nbt.TAG_COMPOUND, "",
                     _compound(_named(nbt.TAG_INT, "x", struct.pack(">i", 9))))
        with self.assertRaises(nbt.NBTError):
            nbt.loads(raw[:-3])

    def test_garbage_raises(self):
        with self.assertRaises(nbt.NBTError):
            nbt.loads(b"\x07not nbt at all")


class LevelSummaryTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.save = Path(self.td.name) / "world"
        self.save.mkdir()

    def test_modern_level_dat(self):
        (self.save / "level.dat").write_bytes(_level_dat_bytes())
        out = level_summary(self.save)
        self.assertEqual(out["level_name"], "My World")
        self.assertEqual(out["mc_version"], "1.20.1")
        self.assertEqual(out["game_mode"], "创造")
        self.assertEqual(out["difficulty"], "普通")
        self.assertFalse(out["hardcore"])
        self.assertTrue(out["cheats"])
        self.assertEqual(out["seed"], "12345")
        self.assertEqual(out["last_played"], 1700000000)

    def test_legacy_random_seed(self):
        (self.save / "level.dat").write_bytes(
            _level_dat_bytes(seed=-42, seed_in_wgs=False, game_type=0))
        out = level_summary(self.save)
        self.assertEqual(out["seed"], "-42")
        self.assertEqual(out["game_mode"], "生存")

    def test_missing_file_returns_empty(self):
        self.assertEqual(level_summary(self.save), {})

    def test_corrupt_file_returns_empty(self):
        (self.save / "level.dat").write_bytes(b"\x1f\x8b broken gzip")
        self.assertEqual(level_summary(self.save), {})


class ListSavesIntegrationTests(unittest.TestCase):
    def test_list_saves_includes_details(self):
        from mclauncher import saves as saves_mod

        class FakeInstance:
            def __init__(self, path):
                self.path = path

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            world = root / "saves" / "TestWorld"
            world.mkdir(parents=True)
            (world / "level.dat").write_bytes(_level_dat_bytes(name="TestWorld"))
            # game_dir 依赖 version_settings；直接打桩到实例根目录
            orig = saves_mod._game_dir
            saves_mod._game_dir = lambda inst, vid="": root
            try:
                rows = saves_mod.list_saves(FakeInstance(root))
            finally:
                saves_mod._game_dir = orig
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["name"], "TestWorld")
        self.assertEqual(row["game_mode"], "创造")
        self.assertEqual(row["mc_version"], "1.20.1")
        self.assertEqual(row["seed"], "12345")


if __name__ == "__main__":
    unittest.main()
