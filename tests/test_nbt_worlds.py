# -*- coding: utf-8 -*-
"""NBT 读取器与 level.dat 世界信息解析。"""
from __future__ import annotations

import gzip
import struct
import tempfile
import unittest
from pathlib import Path

from mclauncher import nbt
from mclauncher import saves


# ---------------------------------------------------------------- NBT 编码 helper（仅测试用）

def _name(s: str) -> bytes:
    b = s.encode("utf-8")
    return struct.pack(">H", len(b)) + b


def t_byte(name, v):
    return b"\x01" + _name(name) + struct.pack(">b", v)


def t_int(name, v):
    return b"\x03" + _name(name) + struct.pack(">i", v)


def t_long(name, v):
    return b"\x04" + _name(name) + struct.pack(">q", v)


def t_double(name, v):
    return b"\x06" + _name(name) + struct.pack(">d", v)


def t_string(name, v):
    return b"\x08" + _name(name) + _name(v)


def t_list_int(name, values):
    body = b"".join(struct.pack(">i", v) for v in values)
    return b"\x09" + _name(name) + b"\x03" + struct.pack(">i", len(values)) + body


def t_compound(name, children: bytes):
    return b"\x0a" + _name(name) + children + b"\x00"


def t_long_array(name, values):
    body = b"".join(struct.pack(">q", v) for v in values)
    return b"\x0c" + _name(name) + struct.pack(">i", len(values)) + body


def level_dat(data_children: bytes, compress=True) -> bytes:
    raw = t_compound("", t_compound("Data", data_children))
    return gzip.compress(raw) if compress else raw


class NbtReaderTests(unittest.TestCase):
    def test_mixed_types(self):
        children = (t_byte("b", -5) + t_int("i", 123456) + t_long("l", 2**40)
                    + t_double("d", 1.5) + t_string("s", "你好 world")
                    + t_list_int("li", [1, 2, 3])
                    + t_long_array("la", [7, -8]))
        blob = t_compound("", children)
        root = nbt.loads(blob)
        self.assertEqual(root["b"], -5)
        self.assertEqual(root["i"], 123456)
        self.assertEqual(root["l"], 2**40)
        self.assertAlmostEqual(root["d"], 1.5)
        self.assertEqual(root["s"], "你好 world")
        self.assertEqual(root["li"], [1, 2, 3])
        self.assertEqual(root["la"], [7, -8])

    def test_gzip_and_plain(self):
        blob = t_compound("", t_int("x", 9))
        self.assertEqual(nbt.loads(blob)["x"], 9)
        self.assertEqual(nbt.loads(gzip.compress(blob))["x"], 9)

    def test_truncated(self):
        with self.assertRaises(nbt.NBTError):
            nbt.loads(b"\x0a\x00\x00\x03\x00\x01x")

    def test_non_compound_root(self):
        with self.assertRaises(nbt.NBTError):
            nbt.loads(b"\x03" + _name("x") + struct.pack(">i", 1))


class WorldInfoTests(unittest.TestCase):
    def _make_world(self, children: bytes) -> Path:
        d = Path(tempfile.mkdtemp(prefix="pymcl-world-"))
        (d / "level.dat").write_bytes(level_dat(children))
        return d

    def test_modern_world(self):
        children = (
            t_string("LevelName", "我的世界")
            + t_int("GameType", 1)
            + t_byte("hardcore", 0)
            + t_byte("allowCommands", 1)
            + t_byte("Difficulty", 2)
            + t_long("LastPlayed", 1724500000000)
            + t_compound("Version", t_string("Name", "1.21"))
            + t_compound("WorldGenSettings", t_long("seed", -776953)))
        info = saves.world_info(self._make_world(children))
        self.assertEqual(info["level_name"], "我的世界")
        self.assertEqual(info["game_mode"], "创造")
        self.assertEqual(info["seed"], "-776953")
        self.assertTrue(info["cheats"])
        self.assertEqual(info["difficulty"], "普通")
        self.assertEqual(info["version"], "1.21")
        self.assertEqual(info["last_played"], 1724500000)

    def test_legacy_seed_and_hardcore(self):
        children = (t_int("GameType", 0) + t_byte("hardcore", 1)
                    + t_long("RandomSeed", 42))
        info = saves.world_info(self._make_world(children))
        self.assertEqual(info["seed"], "42")
        self.assertEqual(info["game_mode"], "硬核")
        self.assertTrue(info["hardcore"])

    def test_corrupt_level_dat(self):
        d = Path(tempfile.mkdtemp(prefix="pymcl-world-"))
        (d / "level.dat").write_bytes(b"garbage data not nbt")
        self.assertEqual(saves.world_info(d), {})

    def test_missing_level_dat(self):
        d = Path(tempfile.mkdtemp(prefix="pymcl-world-"))
        self.assertEqual(saves.world_info(d), {})


if __name__ == "__main__":
    unittest.main()
