# -*- coding: utf-8 -*-
"""NBT 树编辑器（HMCL「NBT 编辑」同款后端）。

覆盖：JSON 树往返（含压缩格式保持）、备份、类型/范围校验、
标量与数组解析、空节点、摘要、后缀白名单、两个门面。
"""
import gzip
import tempfile
import unittest
from pathlib import Path

from mclauncher import nbt, nbt_edit
from mclauncher.nbt import NBTError


def _sample_typed():
    """level.dat 缩影：标量全家桶 + 数组 + 列表 + 嵌套复合。"""
    return ("", (nbt.TAG_COMPOUND, {
        "Data": (nbt.TAG_COMPOUND, {
            "LevelName": (nbt.TAG_STRING, "我的世界"),
            "GameType": (nbt.TAG_INT, 1),
            "hardcore": (nbt.TAG_BYTE, 0),
            "Time": (nbt.TAG_LONG, 123456789),
            "BorderSize": (nbt.TAG_DOUBLE, 59999968.0),
            "SpawnAngle": (nbt.TAG_FLOAT, 0.5),
            "RandomSeed": (nbt.TAG_LONG, -42),
            "UUID": (nbt.TAG_INT_ARRAY, [1, -2, 3, -4]),
            "Raw": (nbt.TAG_BYTE_ARRAY, [0, 127, 255]),
            "Longs": (nbt.TAG_LONG_ARRAY, [1 << 40]),
            "ServerBrands": (nbt.TAG_LIST, (nbt.TAG_STRING, ["vanilla", "fabric"])),
            "Empty": (nbt.TAG_LIST, (nbt.TAG_END, [])),
            "Player": (nbt.TAG_COMPOUND, {
                "Health": (nbt.TAG_FLOAT, 20.0),
                "Pos": (nbt.TAG_LIST, (nbt.TAG_DOUBLE, [0.5, 64.0, -7.25])),
            }),
        }),
    }))


class _WithFile(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.dir = Path(self._td.name)

    def _write(self, name="level.dat", compress=True) -> Path:
        root_name, root = _sample_typed()
        p = self.dir / name
        p.write_bytes(nbt.dumps_typed(root_name, root, compress=compress))
        return p


class LoadSaveTests(_WithFile):
    def test_roundtrip_gzip(self):
        p = self._write()
        doc = nbt_edit.load_file(p)
        self.assertTrue(doc["compressed"])
        self.assertEqual(doc["name"], "level.dat")
        backup = nbt_edit.save_file(p, doc)
        self.assertTrue(Path(backup).is_file())
        data = nbt.read_file(p)["Data"]
        self.assertEqual(data["LevelName"], "我的世界")
        self.assertEqual(data["Time"], 123456789)
        self.assertEqual(data["UUID"], [1, -2, 3, -4])
        self.assertEqual(data["Player"]["Pos"], [0.5, 64.0, -7.25])
        # gzip 格式保持
        self.assertEqual(p.read_bytes()[:2], b"\x1f\x8b")

    def test_roundtrip_uncompressed(self):
        p = self._write("servers.dat", compress=False)
        doc = nbt_edit.load_file(p)
        self.assertFalse(doc["compressed"])
        nbt_edit.save_file(p, doc)
        self.assertNotEqual(p.read_bytes()[:2], b"\x1f\x8b")
        self.assertEqual(nbt.read_file(p)["Data"]["GameType"], 1)

    def test_edit_value_persists(self):
        p = self._write()
        doc = nbt_edit.load_file(p)
        data = doc["root"]["v"]["Data"]["v"]
        data["LevelName"]["v"] = "改名了"
        data["GameType"]["v"] = 0
        data["Player"]["v"]["Health"]["v"] = 1.5
        nbt_edit.save_file(p, doc)
        out = nbt.read_file(p)["Data"]
        self.assertEqual(out["LevelName"], "改名了")
        self.assertEqual(out["GameType"], 0)
        self.assertAlmostEqual(out["Player"]["Health"], 1.5, places=3)

    def test_backup_holds_original(self):
        p = self._write()
        original = p.read_bytes()
        doc = nbt_edit.load_file(p)
        doc["root"]["v"]["Data"]["v"]["LevelName"]["v"] = "x"
        backup = nbt_edit.save_file(p, doc)
        self.assertEqual(Path(backup).read_bytes(), original)
        self.assertNotEqual(p.read_bytes(), original)

    def test_add_and_delete_nodes(self):
        p = self._write()
        doc = nbt_edit.load_file(p)
        data = doc["root"]["v"]["Data"]["v"]
        data["NewFlag"] = nbt_edit.empty_node(nbt.TAG_BYTE)
        data["NewFlag"]["v"] = 1
        del data["Raw"]
        data["ServerBrands"]["v"]["items"].append({"t": nbt.TAG_STRING, "v": "quilt"})
        nbt_edit.save_file(p, doc)
        out = nbt.read_file(p)["Data"]
        self.assertEqual(out["NewFlag"], 1)
        self.assertNotIn("Raw", out)
        self.assertEqual(out["ServerBrands"], ["vanilla", "fabric", "quilt"])

    def test_suffix_whitelist(self):
        p = self.dir / "notes.txt"
        p.write_bytes(b"x")
        with self.assertRaises(NBTError):
            nbt_edit.load_file(p)
        with self.assertRaises(NBTError):
            nbt_edit.save_file(p, {})

    def test_missing_file(self):
        with self.assertRaises(NBTError):
            nbt_edit.load_file(self.dir / "ghost.dat")

    def test_corrupt_gzip(self):
        p = self.dir / "bad.dat"
        p.write_bytes(b"\x1f\x8b" + b"junk")
        with self.assertRaises(NBTError):
            nbt_edit.load_file(p)


class ValidationTests(_WithFile):
    def _save(self, root):
        p = self._write("v.dat")
        return nbt_edit.save_file(p, {"root_name": "", "root": root})

    def test_int_range(self):
        with self.assertRaises(NBTError):
            self._save({"t": nbt.TAG_COMPOUND,
                        "v": {"b": {"t": nbt.TAG_BYTE, "v": 200}}})
        with self.assertRaises(NBTError):
            self._save({"t": nbt.TAG_COMPOUND,
                        "v": {"s": {"t": nbt.TAG_SHORT, "v": 40000}}})

    def test_list_elem_type_mismatch(self):
        bad = {"t": nbt.TAG_COMPOUND, "v": {"l": {
            "t": nbt.TAG_LIST,
            "v": {"et": nbt.TAG_INT, "items": [{"t": nbt.TAG_STRING, "v": "x"}]}}}}
        with self.assertRaises(NBTError):
            self._save(bad)

    def test_nonempty_list_needs_elem_type(self):
        bad = {"t": nbt.TAG_COMPOUND, "v": {"l": {
            "t": nbt.TAG_LIST,
            "v": {"et": 0, "items": [{"t": nbt.TAG_INT, "v": 1}]}}}}
        with self.assertRaises(NBTError):
            self._save(bad)

    def test_unknown_tag_and_bad_root(self):
        with self.assertRaises(NBTError):
            self._save({"t": 99, "v": {}})
        with self.assertRaises(NBTError):
            self._save({"t": nbt.TAG_INT, "v": 1})
        with self.assertRaises(NBTError):
            nbt_edit.save_file(self.dir / "v.dat", {"root": None})

    def test_array_elem_range(self):
        with self.assertRaises(NBTError):
            self._save({"t": nbt.TAG_COMPOUND,
                        "v": {"a": {"t": nbt.TAG_BYTE_ARRAY, "v": [999]}}})


class HelperTests(unittest.TestCase):
    def test_parse_scalar(self):
        self.assertEqual(nbt_edit.parse_scalar(nbt.TAG_INT, " 42 "), 42)
        self.assertEqual(nbt_edit.parse_scalar(nbt.TAG_BYTE, "-128"), -128)
        self.assertAlmostEqual(nbt_edit.parse_scalar(nbt.TAG_DOUBLE, "1.5"), 1.5)
        self.assertEqual(nbt_edit.parse_scalar(nbt.TAG_STRING, "a b"), "a b")
        with self.assertRaises(NBTError):
            nbt_edit.parse_scalar(nbt.TAG_BYTE, "128")
        with self.assertRaises(NBTError):
            nbt_edit.parse_scalar(nbt.TAG_INT, "abc")
        with self.assertRaises(NBTError):
            nbt_edit.parse_scalar(nbt.TAG_COMPOUND, "{}")

    def test_parse_array(self):
        self.assertEqual(nbt_edit.parse_array(nbt.TAG_INT_ARRAY, "1, 2  3,4"),
                         [1, 2, 3, 4])
        self.assertEqual(nbt_edit.parse_array(nbt.TAG_LONG_ARRAY, ""), [])
        with self.assertRaises(NBTError):
            nbt_edit.parse_array(nbt.TAG_BYTE_ARRAY, "300")
        with self.assertRaises(NBTError):
            nbt_edit.parse_array(nbt.TAG_STRING, "x")

    def test_empty_node(self):
        self.assertEqual(nbt_edit.empty_node(nbt.TAG_INT), {"t": nbt.TAG_INT, "v": 0})
        self.assertEqual(nbt_edit.empty_node(nbt.TAG_LIST),
                         {"t": nbt.TAG_LIST, "v": {"et": 0, "items": []}})
        self.assertEqual(nbt_edit.empty_node(nbt.TAG_COMPOUND),
                         {"t": nbt.TAG_COMPOUND, "v": {}})
        with self.assertRaises(NBTError):
            nbt_edit.empty_node(0)

    def test_summary(self):
        self.assertEqual(nbt_edit.summary({"t": nbt.TAG_INT, "v": 5}), "5")
        self.assertEqual(nbt_edit.summary({"t": nbt.TAG_COMPOUND, "v": {"a": 1}}), "{1}")
        self.assertEqual(
            nbt_edit.summary({"t": nbt.TAG_LIST,
                              "v": {"et": nbt.TAG_STRING, "items": [1, 2]}}),
            "[2] String")
        self.assertTrue(
            nbt_edit.summary({"t": nbt.TAG_INT_ARRAY, "v": list(range(20))})
            .endswith("…"))


class FacadeTests(_WithFile):
    def test_bridge(self):
        from bridge.api import BackendAPI

        class _Bus:
            def emit(self, *a, **k):
                pass

        api = BackendAPI(_Bus())
        p = self._write()
        doc = api.read_nbt_file(str(p))
        self.assertEqual(doc["root"]["v"]["Data"]["v"]["GameType"]["v"], 1)
        doc["root"]["v"]["Data"]["v"]["GameType"]["v"] = 3
        backup = api.write_nbt_file(str(p), doc)
        self.assertTrue(backup.endswith(nbt_edit.BACKUP_SUFFIX))
        self.assertEqual(nbt.read_file(p)["Data"]["GameType"], 3)

    def test_qt_backend_static(self):
        # 方法体不碰 Qt，直接以 None self 调用（和 test_music 的门面约定一致）
        from app.backend import BackendAPI as QtBackend
        p = self._write()
        doc = QtBackend.read_nbt_file(None, str(p))
        doc["root"]["v"]["Data"]["v"]["LevelName"]["v"] = "facade"
        QtBackend.write_nbt_file(None, str(p), doc)
        self.assertEqual(nbt.read_file(p)["Data"]["LevelName"], "facade")


if __name__ == "__main__":
    unittest.main()
