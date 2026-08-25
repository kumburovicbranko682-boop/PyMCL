# -*- coding: utf-8 -*-
"""任意游戏目录导入测试：目录解析、版本扫描、迁移拷贝。"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mclauncher import official_migrate as om


def _make_game_dir(root: Path, versions=("1.20.1",)) -> Path:
    for vid in versions:
        vdir = root / "versions" / vid
        vdir.mkdir(parents=True, exist_ok=True)
        (vdir / f"{vid}.json").write_text(json.dumps({"id": vid}), encoding="utf-8")
        (vdir / f"{vid}.jar").write_bytes(b"jar")
    return root


class ResolveGameDirTests(unittest.TestCase):
    def test_direct_game_dir(self):
        with tempfile.TemporaryDirectory() as td:
            p = _make_game_dir(Path(td))
            self.assertEqual(om.resolve_game_dir(p), p)

    def test_parent_with_dot_minecraft(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_game_dir(root / ".minecraft")
            self.assertEqual(om.resolve_game_dir(root), root / ".minecraft")

    def test_parent_with_minecraft(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_game_dir(root / "minecraft")
            self.assertEqual(om.resolve_game_dir(root), root / "minecraft")

    def test_not_a_game_dir(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "docs").mkdir()
            self.assertIsNone(om.resolve_game_dir(td))

    def test_missing_path(self):
        self.assertIsNone(om.resolve_game_dir("/no/such/dir"))
        self.assertIsNone(om.resolve_game_dir(""))


class ScanVersionsTests(unittest.TestCase):
    def test_scan_lists_only_valid_versions(self):
        with tempfile.TemporaryDirectory() as td:
            root = _make_game_dir(Path(td), versions=("1.20.1", "1.19.4-forge"))
            # 没有 json 的目录不算版本
            (root / "versions" / "broken").mkdir()
            self.assertEqual(om.scan_versions(root), ["1.19.4-forge", "1.20.1"])

    def test_scan_without_versions_dir(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(om.scan_versions(Path(td)), [])


class _FakeInstance:
    _base: Path | None = None

    def __init__(self, name=None):
        self.name = name or "default"
        self.path = type(self)._base / self.name
        self._meta = {}

    def create(self):
        self.path.mkdir(parents=True, exist_ok=True)

    def versions_dir(self):
        return self.path / "versions"

    def libraries_dir(self):
        return self.path / "libraries"

    def assets_dir(self):
        return self.path / "assets"

    def set_meta(self, key, value):
        self._meta[key] = value


class MigrateTests(unittest.TestCase):
    def test_migrate_copies_versions(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = _make_game_dir(root / "old-pc" / ".minecraft",
                                 versions=("1.20.1", "1.18.2"))
            _FakeInstance._base = root / "dest"
            with mock.patch.object(om, "Instance", _FakeInstance):
                result = om.migrate(str(src), "default")
            self.assertEqual(sorted(result["versions"]), ["1.18.2", "1.20.1"])
            for vid in ("1.20.1", "1.18.2"):
                vdir = _FakeInstance._base / "default" / "versions" / vid
                self.assertTrue((vdir / f"{vid}.json").is_file())
                self.assertTrue((vdir / f"{vid}.jar").is_file())

    def test_migrate_missing_dir_raises(self):
        with self.assertRaises(FileNotFoundError):
            om.migrate("/no/such/game/dir")


if __name__ == "__main__":
    unittest.main()
