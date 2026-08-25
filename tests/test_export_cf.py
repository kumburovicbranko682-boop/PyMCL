# -*- coding: utf-8 -*-
"""CurseForge 格式整合包导出：指纹、批量匹配、manifest 结构。"""
import json
import sys
import unittest
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mclauncher import export_pack
from mclauncher import mods


class Murmur2Tests(unittest.TestCase):
    def test_stable_and_distinct(self):
        a = mods.murmur2_hash(b"hello world", 1)
        self.assertEqual(a, mods.murmur2_hash(b"hello world", 1))
        self.assertNotEqual(a, mods.murmur2_hash(b"hello worle", 1))
        self.assertLessEqual(a, 0xFFFFFFFF)

    def test_fingerprint_strips_whitespace(self):
        with tempfile.TemporaryDirectory() as td:
            p1 = Path(td) / "a.jar"
            p2 = Path(td) / "b.jar"
            p1.write_bytes(b"abc def\r\n\tghi")
            p2.write_bytes(b"abcdefghi")
            self.assertEqual(mods.cf_fingerprint(p1), mods.cf_fingerprint(p2))

    def test_empty_input(self):
        self.assertEqual(mods.murmur2_hash(b"", 1), mods.murmur2_hash(b"", 1))


class _FakeDM:
    pass


class MatchFingerprintsTests(unittest.TestCase):
    def test_maps_exact_matches(self):
        payload = {"data": {"exactMatches": [
            {"id": 111, "file": {"id": 222, "modId": 111,
                                 "fileFingerprint": 333, "fileName": "a.jar"}},
        ]}}
        with patch.object(mods, "_cf_post", return_value=payload) as post:
            out = mods.cf_match_fingerprints(_FakeDM(), [333, 999])
        self.assertEqual(out, {333: {"projectID": 111, "fileID": 222, "fileName": "a.jar"}})
        post.assert_called_once()

    def test_request_failure_returns_empty(self):
        with patch.object(mods, "_cf_post", side_effect=RuntimeError("api down")):
            out = mods.cf_match_fingerprints(_FakeDM(), [1, 2, 3])
        self.assertEqual(out, {})

    def test_chunks_of_100(self):
        with patch.object(mods, "_cf_post", return_value={"data": {"exactMatches": []}}) as post:
            mods.cf_match_fingerprints(_FakeDM(), list(range(1, 251)))
        self.assertEqual(post.call_count, 3)

    def test_ignores_bad_values(self):
        with patch.object(mods, "_cf_post", return_value={"data": {"exactMatches": []}}) as post:
            out = mods.cf_match_fingerprints(_FakeDM(), ["x", None])
        self.assertEqual(out, {})
        post.assert_not_called()


class _FakeInstance:
    def __init__(self, root: Path, meta=None):
        self.path = root
        self.name = "packtest"
        self._meta = meta or {}

    def meta(self):
        return self._meta


class ExportCfZipTests(unittest.TestCase):
    def _make_instance(self, root: Path):
        (root / "mods").mkdir(parents=True)
        (root / "mods" / "matched.jar").write_bytes(b"PK\x03\x04matched")
        (root / "mods" / "unmatched.jar").write_bytes(b"PK\x03\x04unmatched")
        (root / "config").mkdir()
        (root / "config" / "some.toml").write_text("x = 1", encoding="utf-8")
        return _FakeInstance(root, meta={"modpack": {
            "name": "MyPack", "version": "2.1", "mc_version": "1.20.1",
            "loader": "Forge", "loader_version": "47.2.0",
        }})

    def test_export_manifest_and_overrides(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            inst = self._make_instance(root)
            matched_fp = mods.cf_fingerprint(root / "mods" / "matched.jar")

            def fake_match(_dm, fps, api_key=None):
                self.assertIn(matched_fp, list(fps))
                return {matched_fp: {"projectID": 238222, "fileID": 4711,
                                     "fileName": "matched.jar"}}

            dest = root / "out.zip"
            with patch.object(mods, "cf_match_fingerprints", side_effect=fake_match):
                path = export_pack.export_cf_zip(inst, dest, dm=_FakeDM())
            with zipfile.ZipFile(path) as zf:
                names = set(zf.namelist())
                manifest = json.loads(zf.read("manifest.json"))
            self.assertEqual(manifest["manifestType"], "minecraftModpack")
            self.assertEqual(manifest["manifestVersion"], 1)
            self.assertEqual(manifest["name"], "MyPack")
            self.assertEqual(manifest["version"], "2.1")
            self.assertEqual(manifest["minecraft"]["version"], "1.20.1")
            self.assertEqual(manifest["minecraft"]["modLoaders"],
                             [{"id": "forge-47.2.0", "primary": True}])
            self.assertEqual(manifest["files"],
                             [{"projectID": 238222, "fileID": 4711, "required": True}])
            self.assertEqual(manifest["overrides"], "overrides")
            self.assertIn("overrides/mods/unmatched.jar", names)
            self.assertNotIn("overrides/mods/matched.jar", names)
            self.assertIn("overrides/config/some.toml", names)

    def test_export_all_unmatched_when_api_down(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            inst = self._make_instance(root)
            dest = root / "out.zip"
            with patch.object(mods, "cf_match_fingerprints", return_value={}):
                path = export_pack.export_cf_zip(inst, dest, dm=_FakeDM())
            with zipfile.ZipFile(path) as zf:
                manifest = json.loads(zf.read("manifest.json"))
                names = set(zf.namelist())
            self.assertEqual(manifest["files"], [])
            self.assertIn("overrides/mods/matched.jar", names)
            self.assertIn("overrides/mods/unmatched.jar", names)

    def test_export_without_mods_dir(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            inst = _FakeInstance(root, meta={"mc_version": "1.19.4"})
            dest = root / "out.zip"
            path = export_pack.export_cf_zip(inst, dest, dm=_FakeDM())
            with zipfile.ZipFile(path) as zf:
                manifest = json.loads(zf.read("manifest.json"))
            self.assertEqual(manifest["files"], [])
            self.assertEqual(manifest["minecraft"]["version"], "1.19.4")
            self.assertEqual(manifest["minecraft"]["modLoaders"], [])

    def test_loader_without_version(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            inst = _FakeInstance(root, meta={"modpack": {
                "mc_version": "1.20.1", "loader": "fabric"}})
            dest = root / "out.zip"
            path = export_pack.export_cf_zip(inst, dest, dm=_FakeDM())
            with zipfile.ZipFile(path) as zf:
                manifest = json.loads(zf.read("manifest.json"))
            self.assertEqual(manifest["minecraft"]["modLoaders"],
                             [{"id": "fabric", "primary": True}])


class MrpackHelpersTests(unittest.TestCase):
    def test_pack_meta_defaults(self):
        with tempfile.TemporaryDirectory() as td:
            inst = _FakeInstance(Path(td))
            meta = export_pack._pack_meta(inst)
            self.assertEqual(meta["name"], "packtest")
            self.assertEqual(meta["version"], "1.0.0")
            self.assertEqual(meta["loader"], "")

    def test_collect_overrides(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "resourcepacks").mkdir()
            (root / "resourcepacks" / "pack.zip").write_bytes(b"PK")
            inst = _FakeInstance(root)
            rows = export_pack._collect_overrides(inst)
            self.assertEqual(rows[0][0], "resourcepacks/pack.zip")


if __name__ == "__main__":
    unittest.main()
