# -*- coding: utf-8 -*-
"""多游戏目录管理（HMCL 目录列表 / PCL2 文件夹列表）：核心逻辑与门面对齐。"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mclauncher import game_dirs, utils  # noqa: E402
from mclauncher.config import CONFIG, DEFAULT_CONFIG  # noqa: E402


class _Isolated(unittest.TestCase):
    """把 utils.ROOT 和 CONFIG 指到临时目录，save() 不落盘。"""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        self.addCleanup(self._td.cleanup)
        data = {k: (list(v) if isinstance(v, list) else dict(v) if isinstance(v, dict) else v)
                for k, v in DEFAULT_CONFIG.items()}
        for p in (patch.object(utils, "ROOT", self.root),
                  patch.object(CONFIG, "data", data),
                  patch.object(CONFIG, "save", lambda: None)):
            p.start()
            self.addCleanup(p.stop)


class EntriesTests(_Isolated):
    def test_default_only(self):
        rows = game_dirs.entries()
        self.assertEqual(len(rows), 1)
        e = rows[0]
        self.assertEqual(e["name"], game_dirs.DEFAULT_NAME)
        self.assertEqual(e["path"], str((self.root / ".minecraft").resolve()))
        self.assertTrue(e["active"])
        self.assertFalse(e["removable"])

    def test_unregistered_active_dir_is_listed(self):
        loose = self.root / "elsewhere"
        loose.mkdir()
        CONFIG.set("instances_dir", str(loose))
        rows = game_dirs.entries()
        self.assertEqual(len(rows), 2)
        active = [r for r in rows if r["active"]]
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["path"], str(loose.resolve()))

    def test_missing_dir_flagged(self):
        gone = self.root / "not-created"
        CONFIG.set("game_dirs", [{"name": "旧盘", "path": str(gone)}])
        rows = game_dirs.entries()
        row = [r for r in rows if r["name"] == "旧盘"][0]
        self.assertFalse(row["exists"])


class RegisterTests(_Isolated):
    def test_register_creates_and_lists(self):
        target = self.root / "第二目录"
        entry = game_dirs.register(str(target))
        self.assertTrue(target.is_dir())
        self.assertEqual(entry["name"], "第二目录")
        rows = game_dirs.entries()
        self.assertEqual(len(rows), 2)
        row = [r for r in rows if r["path"] == str(target.resolve())][0]
        self.assertFalse(row["active"])
        self.assertTrue(row["removable"])

    def test_register_dedupes_by_resolved_path(self):
        target = self.root / "dup"
        game_dirs.register(str(target))
        game_dirs.register(str(target) + "/")
        self.assertEqual(len(CONFIG.get("game_dirs")), 1)

    def test_register_with_name_renames_existing(self):
        target = self.root / "d"
        game_dirs.register(str(target))
        game_dirs.register(str(target), name="模组盘")
        self.assertEqual(CONFIG.get("game_dirs")[0]["name"], "模组盘")

    def test_register_default_is_noop(self):
        game_dirs.register(game_dirs.DEFAULT_RAW)
        self.assertEqual(CONFIG.get("game_dirs"), [])

    def test_empty_path_raises(self):
        with self.assertRaises(game_dirs.GameDirError):
            game_dirs.register("  ")

    def test_file_path_raises(self):
        f = self.root / "afile"
        f.write_text("x")
        with self.assertRaises(game_dirs.GameDirError):
            game_dirs.register(str(f))


class ActivateTests(_Isolated):
    def test_activate_switches_and_registers(self):
        target = self.root / "d2"
        out = game_dirs.activate(str(target))
        self.assertEqual(out, str(CONFIG.instances_dir))
        self.assertEqual(Path(out), target.resolve())
        rows = game_dirs.entries()
        active = [r for r in rows if r["active"]][0]
        self.assertEqual(active["path"], str(target.resolve()))
        self.assertEqual(len(CONFIG.get("game_dirs")), 1)

    def test_activate_back_to_default_stores_relative(self):
        game_dirs.activate(str(self.root / "d2"))
        game_dirs.activate(game_dirs.DEFAULT_RAW)
        self.assertEqual(CONFIG.get("instances_dir"), game_dirs.DEFAULT_RAW)
        rows = game_dirs.entries()
        self.assertTrue(rows[0]["active"])


class RemoveTests(_Isolated):
    def test_remove_stored_entry(self):
        target = self.root / "d3"
        game_dirs.register(str(target))
        self.assertTrue(game_dirs.remove(str(target)))
        self.assertEqual(CONFIG.get("game_dirs"), [])
        # 只出列表，不删文件
        self.assertTrue(target.is_dir())

    def test_remove_unknown_returns_false(self):
        self.assertFalse(game_dirs.remove(str(self.root / "nope")))

    def test_remove_default_raises(self):
        with self.assertRaises(game_dirs.GameDirError):
            game_dirs.remove(game_dirs.DEFAULT_RAW)

    def test_remove_active_raises(self):
        target = self.root / "d4"
        game_dirs.activate(str(target))
        with self.assertRaises(game_dirs.GameDirError):
            game_dirs.remove(str(target))


class RenameTests(_Isolated):
    def test_rename(self):
        target = self.root / "d5"
        game_dirs.register(str(target))
        self.assertTrue(game_dirs.rename(str(target), "光影专用"))
        self.assertEqual(CONFIG.get("game_dirs")[0]["name"], "光影专用")

    def test_rename_unknown_returns_false(self):
        self.assertFalse(game_dirs.rename(str(self.root / "nope"), "x"))

    def test_rename_empty_raises(self):
        target = self.root / "d6"
        game_dirs.register(str(target))
        with self.assertRaises(game_dirs.GameDirError):
            game_dirs.rename(str(target), "  ")

    def test_rename_default_raises(self):
        with self.assertRaises(game_dirs.GameDirError):
            game_dirs.rename(game_dirs.DEFAULT_RAW, "x")


class BridgeFacadeTests(_Isolated):
    """无 Qt 门面：真实调用 add/list/remove/set_game_dir 一整圈。"""

    def setUp(self):
        super().setUp()
        from bridge.api import BackendAPI

        class _Bus:
            def emit(self, *a, **k):
                pass

        self.api = BackendAPI(_Bus())

    def test_round_trip(self):
        target = self.root / "bridge目录"
        rows = self.api.add_game_dir(str(target), "副目录")
        self.assertEqual(len(rows), 2)
        self.assertEqual([r for r in rows if r["removable"]][0]["name"], "副目录")

        out = self.api.set_game_dir(str(target))
        self.assertEqual(Path(out), target.resolve())
        rows = self.api.list_game_dirs()
        self.assertEqual([r for r in rows if r["active"]][0]["path"],
                         str(target.resolve()))

        rows = self.api.rename_game_dir(str(target), "新名字")
        self.assertEqual([r for r in rows if r["removable"]][0]["name"], "新名字")

        # 移除当前生效目录应失败（在列表里保留）
        with self.assertRaises(Exception):
            self.api.remove_game_dir(str(target))

        self.api.set_game_dir(game_dirs.DEFAULT_RAW)
        rows = self.api.remove_game_dir(str(target))
        self.assertEqual(len(rows), 1)


class QtFacadeParityTests(unittest.TestCase):
    def test_qt_backend_has_same_methods(self):
        from app.backend import BackendAPI as QtBackend
        for name in ("set_game_dir", "list_game_dirs", "add_game_dir",
                     "remove_game_dir", "rename_game_dir"):
            self.assertTrue(callable(getattr(QtBackend, name, None)), name)


if __name__ == "__main__":
    unittest.main()
