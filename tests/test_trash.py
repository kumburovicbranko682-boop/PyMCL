# -*- coding: utf-8 -*-
"""删除走系统回收站：XDG Trash 真实路径 + 各删除入口的接线。"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import PropertyMock, patch
from urllib.parse import unquote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mclauncher import instances, trash  # noqa: E402
from mclauncher.config import CONFIG  # noqa: E402


class _XdgBase(unittest.TestCase):
    """把 XDG_DATA_HOME 指到临时目录，测试绝不碰真实用户回收站。"""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.base = Path(self._td.name)
        self.data_home = self.base / "xdg-data"
        env = patch.dict(os.environ, {"XDG_DATA_HOME": str(self.data_home)})
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(self._td.cleanup)

    @property
    def trash_files(self) -> Path:
        return self.data_home / "Trash" / "files"

    @property
    def trash_info(self) -> Path:
        return self.data_home / "Trash" / "info"


@unittest.skipIf(sys.platform in ("win32", "darwin"), "XDG 回收站仅 Linux")
class XdgTrashTests(_XdgBase):
    def test_trash_file(self):
        f = self.base / "hello.txt"
        f.write_text("hi", "utf-8")
        self.assertTrue(trash.move_to_trash(f))
        self.assertFalse(f.exists())
        moved = self.trash_files / "hello.txt"
        self.assertEqual(moved.read_text("utf-8"), "hi")
        info = (self.trash_info / "hello.txt.trashinfo").read_text("utf-8")
        self.assertIn("[Trash Info]", info)
        self.assertIn("DeletionDate=", info)
        path_line = next(l for l in info.splitlines() if l.startswith("Path="))
        self.assertEqual(unquote(path_line[len("Path="):]), str(f.resolve()))

    def test_trash_directory_tree(self):
        d = self.base / "world"
        (d / "region").mkdir(parents=True)
        (d / "level.dat").write_bytes(b"lvl")
        (d / "region" / "r.0.0.mca").write_bytes(b"chunk")
        self.assertTrue(trash.move_to_trash(d))
        self.assertFalse(d.exists())
        self.assertEqual((self.trash_files / "world" / "level.dat").read_bytes(), b"lvl")
        self.assertEqual(
            (self.trash_files / "world" / "region" / "r.0.0.mca").read_bytes(), b"chunk")

    def test_name_collision_gets_suffix(self):
        for expected in ("a.txt", "a.txt.2", "a.txt.3"):
            f = self.base / "a.txt"
            f.write_text(expected, "utf-8")
            self.assertTrue(trash.move_to_trash(f))
            self.assertEqual((self.trash_files / expected).read_text("utf-8"), expected)
            self.assertTrue((self.trash_info / f"{expected}.trashinfo").is_file())

    def test_unicode_and_space_paths_roundtrip(self):
        f = self.base / "我的 存档.zip"
        f.write_bytes(b"x")
        self.assertTrue(trash.move_to_trash(f))
        info = (self.trash_info / "我的 存档.zip.trashinfo").read_text("utf-8")
        path_line = next(l for l in info.splitlines() if l.startswith("Path="))
        self.assertNotIn(" ", path_line)  # 空格必须被百分号编码
        self.assertEqual(unquote(path_line[len("Path="):]), str(f.resolve()))

    def test_missing_path_returns_false(self):
        self.assertFalse(trash.move_to_trash(self.base / "nope.txt"))

    def test_trash_or_delete_prefers_trash(self):
        f = self.base / "b.txt"
        f.write_text("x", "utf-8")
        self.assertEqual(trash.trash_or_delete(f), "trash")
        self.assertTrue((self.trash_files / "b.txt").is_file())

    def test_trash_or_delete_falls_back_to_permanent(self):
        f = self.base / "c.txt"
        f.write_text("x", "utf-8")
        with patch.object(trash, "move_to_trash", return_value=False):
            self.assertEqual(trash.trash_or_delete(f), "deleted")
        self.assertFalse(f.exists())
        self.assertFalse((self.trash_files / "c.txt").exists())

    def test_move_failure_cleans_info_and_reports_false(self):
        f = self.base / "d.txt"
        f.write_text("x", "utf-8")
        with patch.object(trash.shutil, "move", side_effect=OSError("boom")):
            self.assertFalse(trash.move_to_trash(f))
        self.assertTrue(f.exists())
        self.assertFalse((self.trash_info / "d.txt.trashinfo").exists())


@unittest.skipIf(sys.platform in ("win32", "darwin"), "XDG 回收站仅 Linux")
class DeletionWiringTests(_XdgBase):
    """各删除入口都应把东西送进回收站而不是直接消失。"""

    def setUp(self):
        super().setUp()
        self.instances_root = self.base / "instances"
        self.instances_root.mkdir()
        patcher = patch.object(type(CONFIG), "instances_dir",
                               new_callable=PropertyMock,
                               return_value=self.instances_root)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _make_instance(self, name="测试"):
        inst = instances.Instance(name)
        inst.create()
        return inst

    def test_instance_delete(self):
        inst = self._make_instance()
        (inst.path / "saves" / "w").mkdir(parents=True, exist_ok=True)
        (inst.path / "saves" / "w" / "level.dat").write_bytes(b"lvl")
        inst.delete()
        self.assertFalse(inst.path.exists())
        self.assertEqual(
            (self.trash_files / "测试" / "saves" / "w" / "level.dat").read_bytes(), b"lvl")

    def test_delete_save(self):
        from mclauncher import saves
        inst = self._make_instance()
        world = inst.path / "saves" / "MyWorld"
        world.mkdir(parents=True, exist_ok=True)
        (world / "level.dat").write_bytes(b"lvl")
        saves.delete_save(inst, "MyWorld")
        self.assertFalse(world.exists())
        self.assertTrue((self.trash_files / "MyWorld" / "level.dat").is_file())

    def test_delete_backup(self):
        from mclauncher import saves
        inst = self._make_instance()
        bdir = saves.backups_dir(inst)
        bdir.mkdir(parents=True, exist_ok=True)
        (bdir / "w-2024.zip").write_bytes(b"zip")
        saves.delete_backup(inst, "w-2024.zip")
        self.assertTrue((self.trash_files / "w-2024.zip").is_file())

    def test_delete_mod(self):
        from mclauncher import mods
        inst = self._make_instance()
        (inst.path / "mods" / "jei.jar").write_bytes(b"jar")
        mods.delete_mod(inst, "jei.jar")
        self.assertFalse((inst.path / "mods" / "jei.jar").exists())
        self.assertTrue((self.trash_files / "jei.jar").is_file())

    def test_delete_content_file(self):
        from mclauncher import mods
        inst = self._make_instance()
        (inst.path / "resourcepacks" / "pack.zip").write_bytes(b"zip")
        mods.delete_content_file(inst, "resourcepacks", "pack.zip")
        self.assertTrue((self.trash_files / "pack.zip").is_file())

    def test_uninstall_version(self):
        from mclauncher.installer import Installer
        inst = self._make_instance()
        vdir = inst.path / "versions" / "1.20.1"
        vdir.mkdir(parents=True, exist_ok=True)
        (vdir / "1.20.1.json").write_text("{}", "utf-8")
        Installer(inst).uninstall_version("1.20.1")
        self.assertFalse(vdir.exists())
        self.assertTrue((self.trash_files / "1.20.1" / "1.20.1.json").is_file())


if __name__ == "__main__":
    unittest.main()
