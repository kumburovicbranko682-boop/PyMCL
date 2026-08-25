# -*- coding: utf-8 -*-
"""资源包元数据列表（PCL2 资源包管理 parity）：mcmeta 描述、pack.png 图标、格式→版本段。"""

import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import PropertyMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mclauncher import resourcepacks  # noqa: E402
from mclauncher.config import CONFIG  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24


def _zip_pack(path: Path, description="A nice pack", pack_format=15, with_png=True):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("pack.mcmeta", json.dumps(
            {"pack": {"description": description, "pack_format": pack_format}}))
        if with_png:
            zf.writestr("pack.png", PNG)
        zf.writestr("assets/minecraft/textures/x.png", b"x")


class FormatRangeTests(unittest.TestCase):
    def test_known_formats(self):
        self.assertEqual(resourcepacks.format_mc_range(1), "1.6.1–1.8.9")
        self.assertEqual(resourcepacks.format_mc_range(15), "1.20.2")
        self.assertEqual(resourcepacks.format_mc_range(34), "1.21–1.21.1")

    def test_unknown_format(self):
        self.assertEqual(resourcepacks.format_mc_range(0), "")
        self.assertEqual(resourcepacks.format_mc_range(999), "")


class ListEntriesTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.base = Path(self._td.name)
        self.addCleanup(self._td.cleanup)
        self.packs = self.base / "resourcepacks"
        self.packs.mkdir()
        self.icons = self.base / "icons"

    def _list(self):
        return resourcepacks.list_pack_entries_at(self.packs, icons_dir=self.icons)

    def test_zip_pack_full_metadata(self):
        _zip_pack(self.packs / "cool.zip", description="Very cool", pack_format=15)
        rows = self._list()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["filename"], "cool.zip")
        self.assertEqual(row["description"], "Very cool")
        self.assertEqual(row["pack_format"], 15)
        self.assertEqual(row["mc_range"], "1.20.2")
        self.assertTrue(row["icon"])
        self.assertEqual(Path(row["icon"]).read_bytes(), PNG)

    def test_folder_pack(self):
        folder = self.packs / "myfolderpack"
        folder.mkdir()
        (folder / "pack.mcmeta").write_text(json.dumps(
            {"pack": {"description": {"text": "Folder!"}, "pack_format": 34}}), "utf-8")
        (folder / "pack.png").write_bytes(PNG)
        rows = self._list()
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["is_dir"])
        self.assertEqual(rows[0]["description"], "Folder!")
        self.assertEqual(rows[0]["mc_range"], "1.21–1.21.1")
        self.assertTrue(Path(rows[0]["icon"]).is_file())

    def test_zip_without_png_has_empty_icon(self):
        _zip_pack(self.packs / "noicon.zip", with_png=False)
        row = self._list()[0]
        self.assertEqual(row["icon"], "")

    def test_broken_zip_still_listed(self):
        (self.packs / "broken.zip").write_bytes(b"not a zip at all")
        rows = self._list()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["description"], "")
        self.assertEqual(rows[0]["pack_format"], 0)
        self.assertEqual(rows[0]["icon"], "")

    def test_skips_non_pack_entries(self):
        (self.packs / "readme.txt").write_text("hi", "utf-8")
        (self.packs / "randomdir").mkdir()  # 没有 pack.mcmeta 的文件夹
        _zip_pack(self.packs / "real.zip")
        rows = self._list()
        self.assertEqual([r["filename"] for r in rows], ["real.zip"])

    def test_icon_cache_reused(self):
        _zip_pack(self.packs / "a.zip")
        icon1 = self._list()[0]["icon"]
        mtime = Path(icon1).stat().st_mtime_ns
        icon2 = self._list()[0]["icon"]
        self.assertEqual(icon1, icon2)
        self.assertEqual(Path(icon2).stat().st_mtime_ns, mtime)

    def test_missing_dir_returns_empty(self):
        self.assertEqual(
            resourcepacks.list_pack_entries_at(self.base / "nope", icons_dir=self.icons), [])


class FacadeTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.base = Path(self._td.name)
        self.addCleanup(self._td.cleanup)
        root = self.base / "instances"
        root.mkdir()
        patcher = patch.object(type(CONFIG), "instances_dir",
                               new_callable=PropertyMock, return_value=root)
        patcher.start()
        self.addCleanup(patcher.stop)
        env = patch.dict(os.environ, {"XDG_DATA_HOME": str(self.base / "xdg")})
        env.start()
        self.addCleanup(env.stop)

    def _api(self):
        from bridge.api import BackendAPI

        class _Bus:
            def emit(self, *a, **k):
                pass

        return BackendAPI(_Bus())

    def test_bridge_entries_and_folder_delete(self):
        from mclauncher.instances import Instance
        inst = Instance("测试")
        inst.create()
        _zip_pack(inst.path / "resourcepacks" / "p.zip", with_png=False)
        folder = inst.path / "resourcepacks" / "fold"
        folder.mkdir()
        (folder / "pack.mcmeta").write_text(json.dumps(
            {"pack": {"description": "d", "pack_format": 15}}), "utf-8")

        api = self._api()
        rows = api.get_resourcepack_entries("测试")
        self.assertEqual([r["filename"] for r in rows], ["fold", "p.zip"])

        if sys.platform not in ("win32", "darwin"):
            # 文件夹包也要能删（走回收站）
            api.delete_resourcepack("测试", "fold")
            rows = api.get_resourcepack_entries("测试")
            self.assertEqual([r["filename"] for r in rows], ["p.zip"])

    def test_qt_backend_has_same_method(self):
        import ast
        src = (Path(__file__).resolve().parents[1] / "app" / "backend.py").read_text("utf-8")
        tree = ast.parse(src)
        names = {n.name for n in ast.walk(tree)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        self.assertIn("get_resourcepack_entries", names)


if __name__ == "__main__":
    unittest.main()
