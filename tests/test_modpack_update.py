# -*- coding: utf-8 -*-
"""整合包更新：文件清单、来源记录、版本检查、升级清理。"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mclauncher import modpack


class _Inst:
    def __init__(self, root, meta=None):
        self.path = Path(root)
        self.name = "packtest"
        self._meta = dict(meta or {})

    def meta(self):
        return self._meta

    def set_meta(self, key, value):
        self._meta[key] = value


class _DM:
    pass


class PackFilesTests(unittest.TestCase):
    def test_roundtrip_and_normalize(self):
        with tempfile.TemporaryDirectory() as td:
            inst = _Inst(td)
            modpack.write_pack_files(inst, [
                "mods/a.jar", "mods\\b.jar", "/config/x.toml", "mods/a.jar", "",
            ])
            self.assertEqual(modpack.read_pack_files(inst),
                             ["config/x.toml", "mods/a.jar", "mods/b.jar"])

    def test_missing_file_returns_empty(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(modpack.read_pack_files(_Inst(td)), [])

    def test_corrupt_file_returns_empty(self):
        with tempfile.TemporaryDirectory() as td:
            inst = _Inst(td)
            modpack.pack_files_path(inst).write_text("not json", "utf-8")
            self.assertEqual(modpack.read_pack_files(inst), [])
            modpack.pack_files_path(inst).write_text('{"files": "oops"}', "utf-8")
            self.assertEqual(modpack.read_pack_files(inst), [])


class MergeOriginTests(unittest.TestCase):
    def test_merges_present_keys_only(self):
        meta = {"name": "P"}
        modpack._merge_origin(meta, {"slug": "s", "version_id": "", "addon_id": None},
                              ("slug", "version_id", "addon_id"))
        self.assertEqual(meta, {"name": "P", "slug": "s"})

    def test_none_origin(self):
        meta = {"name": "P"}
        modpack._merge_origin(meta, None, ("slug",))
        self.assertEqual(meta, {"name": "P"})


class CleanupStaleTests(unittest.TestCase):
    def _setup(self, td):
        inst = _Inst(td)
        (inst.path / "mods").mkdir()
        (inst.path / "config").mkdir()
        (inst.path / "saves" / "world").mkdir(parents=True)
        (inst.path / "mods" / "old.jar").write_bytes(b"old")
        (inst.path / "mods" / "kept.jar").write_bytes(b"kept")
        (inst.path / "mods" / "user.jar").write_bytes(b"user")
        (inst.path / "config" / "old.toml").write_text("x", "utf-8")
        (inst.path / "saves" / "world" / "level.dat").write_bytes(b"lvl")
        return inst

    def test_removes_stale_managed_only(self):
        with tempfile.TemporaryDirectory() as td:
            inst = self._setup(td)
            modpack.write_pack_files(inst, ["mods/kept.jar"])  # 新版本清单
            removed = modpack.cleanup_stale_pack_files(
                inst, ["mods/old.jar", "mods/kept.jar", "config/old.toml"])
            self.assertEqual(removed, ["mods/old.jar"])
            self.assertFalse((inst.path / "mods" / "old.jar").exists())
            self.assertTrue((inst.path / "mods" / "kept.jar").exists())
            # 用户手动放的 jar 不在旧清单里，不动
            self.assertTrue((inst.path / "mods" / "user.jar").exists())
            # config 不自动清理（可能被用户改过）
            self.assertTrue((inst.path / "config" / "old.toml").exists())
            self.assertTrue((inst.path / "saves" / "world" / "level.dat").exists())

    def test_removes_disabled_variant(self):
        with tempfile.TemporaryDirectory() as td:
            inst = self._setup(td)
            (inst.path / "mods" / "old.jar.disabled").write_bytes(b"olddis")
            modpack.write_pack_files(inst, [])
            removed = modpack.cleanup_stale_pack_files(inst, ["mods/old.jar"])
            self.assertIn("mods/old.jar", removed)
            self.assertIn("mods/old.jar.disabled", removed)
            self.assertFalse((inst.path / "mods" / "old.jar.disabled").exists())

    def test_ignores_traversal_and_missing(self):
        with tempfile.TemporaryDirectory() as td:
            inst = self._setup(td)
            outside = Path(td).parent / "outside-victim.jar"
            modpack.write_pack_files(inst, [])
            removed = modpack.cleanup_stale_pack_files(
                inst, ["mods/../../outside-victim.jar", "mods/ghost.jar", ""])
            self.assertEqual(removed, [])

    def test_empty_old_list(self):
        with tempfile.TemporaryDirectory() as td:
            inst = self._setup(td)
            self.assertEqual(modpack.cleanup_stale_pack_files(inst, []), [])
            self.assertEqual(modpack.cleanup_stale_pack_files(inst, None), [])


_MR_VERSIONS = [{
    "id": "ver2", "name": "2.0", "version_number": "2.0",
    "version_type": "release", "game_versions": ["1.20.1"], "loaders": ["fabric"],
    "files": [{"url": "https://cdn.example/x-2.0.mrpack",
               "filename": "x-2.0.mrpack", "primary": True}],
}]


class CheckUpdateTests(unittest.TestCase):
    def test_no_pack_meta_raises(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(modpack.ModpackError):
                modpack.check_modpack_update(_DM(), _Inst(td))

    def test_modrinth_update_available(self):
        with tempfile.TemporaryDirectory() as td:
            inst = _Inst(td, {"modpack": {"name": "X", "version": "1.0",
                                          "source": "modrinth", "slug": "x",
                                          "version_id": "ver1"}})
            with patch.object(modpack, "modrinth_versions", return_value=_MR_VERSIONS):
                info = modpack.check_modpack_update(_DM(), inst)
        self.assertTrue(info["update"])
        self.assertEqual(info["latest"], "2.0")
        self.assertEqual(info["latest_id"], "ver2")
        self.assertEqual(info["url"], "https://cdn.example/x-2.0.mrpack")

    def test_modrinth_up_to_date_by_id(self):
        with tempfile.TemporaryDirectory() as td:
            inst = _Inst(td, {"modpack": {"name": "X", "version": "2.0",
                                          "source": "modrinth", "slug": "x",
                                          "version_id": "ver2"}})
            with patch.object(modpack, "modrinth_versions", return_value=_MR_VERSIONS):
                info = modpack.check_modpack_update(_DM(), inst)
        self.assertFalse(info["update"])

    def test_modrinth_fallback_compare_by_version_string(self):
        with tempfile.TemporaryDirectory() as td:
            inst = _Inst(td, {"modpack": {"name": "X", "version": "2.0",
                                          "source": "modrinth", "slug": "x"}})
            with patch.object(modpack, "modrinth_versions", return_value=_MR_VERSIONS):
                info = modpack.check_modpack_update(_DM(), inst)
        self.assertFalse(info["update"])

    def test_modrinth_without_slug_raises_hint(self):
        with tempfile.TemporaryDirectory() as td:
            inst = _Inst(td, {"modpack": {"name": "X", "version": "1.0",
                                          "source": "modrinth"}})
            with self.assertRaises(modpack.ModpackError) as ctx:
                modpack.check_modpack_update(_DM(), inst)
        self.assertIn("重新安装", str(ctx.exception))

    def test_curseforge_update_available(self):
        with tempfile.TemporaryDirectory() as td:
            inst = _Inst(td, {"modpack": {"name": "Y", "version": "1.0",
                                          "source": "curseforge",
                                          "addon_id": 42, "file_id": 100}})
            resolved = {"addon_id": 42, "file_id": 200, "fileName": "y-2.zip",
                        "downloadUrl": None, "name": "Y Pack", "slug": "y"}
            with patch.object(modpack, "resolve_cf_modpack_file",
                              return_value=resolved) as res:
                info = modpack.check_modpack_update(_DM(), inst, api_key="k")
        self.assertTrue(info["update"])
        self.assertEqual(info["latest_id"], 200)
        self.assertEqual(info["latest"], "y-2.zip")
        self.assertEqual(info["name"], "Y Pack")
        res.assert_called_once()

    def test_curseforge_up_to_date(self):
        with tempfile.TemporaryDirectory() as td:
            inst = _Inst(td, {"modpack": {"name": "Y", "version": "2.0",
                                          "source": "curseforge",
                                          "addon_id": 42, "file_id": 200}})
            resolved = {"addon_id": 42, "file_id": 200, "fileName": "y-2.zip",
                        "downloadUrl": None, "name": "Y", "slug": "y"}
            with patch.object(modpack, "resolve_cf_modpack_file", return_value=resolved):
                info = modpack.check_modpack_update(_DM(), inst)
        self.assertFalse(info["update"])

    def test_curseforge_without_ids_raises(self):
        with tempfile.TemporaryDirectory() as td:
            inst = _Inst(td, {"modpack": {"name": "Y", "version": "1.0",
                                          "source": "curseforge"}})
            with self.assertRaises(modpack.ModpackError):
                modpack.check_modpack_update(_DM(), inst)

    def test_unsupported_source_raises(self):
        with tempfile.TemporaryDirectory() as td:
            inst = _Inst(td, {"modpack": {"name": "Z", "version": "?",
                                          "source": "plain-zip"}})
            with self.assertRaises(modpack.ModpackError) as ctx:
                modpack.check_modpack_update(_DM(), inst)
        self.assertIn("不支持", str(ctx.exception))


class UpdateModpackTests(unittest.TestCase):
    def test_no_update_returns_early(self):
        with tempfile.TemporaryDirectory() as td:
            inst = _Inst(td)
            info = {"update": False, "name": "X", "current": "2.0", "source": "modrinth"}
            with patch.object(modpack, "install_mrpack") as im:
                result = modpack.update_modpack(_DM(), inst, info=info)
            im.assert_not_called()
        self.assertFalse(result["updated"])
        self.assertEqual(result["current"], "2.0")

    def test_modrinth_update_installs_and_cleans(self):
        with tempfile.TemporaryDirectory() as td:
            inst = _Inst(td, {"modpack": {"name": "X", "version": "1.0",
                                          "source": "modrinth", "slug": "x",
                                          "version_id": "ver1"}})
            (inst.path / "mods").mkdir()
            (inst.path / "mods" / "old-1.0.jar").write_bytes(b"old")
            (inst.path / "mods" / "user.jar").write_bytes(b"user")
            modpack.write_pack_files(inst, ["mods/old-1.0.jar"])

            def fake_install(dm, url, instance, on_progress=None, cancel=None,
                             force=False, java=None, origin=None):
                (instance.path / "mods" / "new-2.0.jar").write_bytes(b"new")
                modpack.write_pack_files(instance, ["mods/new-2.0.jar"])
                pack_meta = {"name": "X", "version": "2.0", "source": "modrinth"}
                modpack._merge_origin(pack_meta, origin, ("slug", "version_id"))
                instance.set_meta("modpack", pack_meta)
                return pack_meta

            info = {"update": True, "source": "modrinth", "name": "X",
                    "slug": "x", "current": "1.0", "latest": "2.0",
                    "latest_id": "ver2", "url": "https://cdn.example/x-2.0.mrpack"}
            with patch.object(modpack, "install_mrpack", side_effect=fake_install) as im:
                result = modpack.update_modpack(_DM(), inst, info=info)
            im.assert_called_once()
            self.assertEqual(im.call_args.kwargs["origin"],
                             {"slug": "x", "version_id": "ver2"})

            self.assertTrue(result["updated"])
            self.assertEqual(result["from"], "1.0")
            self.assertEqual(result["to"], "2.0")
            self.assertEqual(result["removed"], ["mods/old-1.0.jar"])
            self.assertFalse((inst.path / "mods" / "old-1.0.jar").exists())
            self.assertTrue((inst.path / "mods" / "new-2.0.jar").exists())
            self.assertTrue((inst.path / "mods" / "user.jar").exists())
            self.assertEqual(inst.meta()["modpack"]["version_id"], "ver2")

    def test_modrinth_update_without_url_raises(self):
        with tempfile.TemporaryDirectory() as td:
            inst = _Inst(td)
            info = {"update": True, "source": "modrinth", "name": "X",
                    "current": "1.0", "latest": "2.0", "latest_id": "v2", "url": None}
            with self.assertRaises(modpack.ModpackError):
                modpack.update_modpack(_DM(), inst, info=info)

    def test_curseforge_update_passes_file_id(self):
        with tempfile.TemporaryDirectory() as td:
            inst = _Inst(td, {"modpack": {"name": "Y", "version": "1.0",
                                          "source": "curseforge",
                                          "addon_id": 42, "file_id": 100}})
            (inst.path / "mods").mkdir()
            modpack.write_pack_files(inst, [])

            def fake_cf(dm, addon_id, instance, api_key=None, on_progress=None,
                        cancel=None, cf_slug=None, file_id=None):
                pack_meta = {"name": "Y", "version": "2.0", "source": "curseforge",
                             "addon_id": addon_id, "file_id": file_id, "slug": cf_slug}
                instance.set_meta("modpack", pack_meta)
                modpack.write_pack_files(instance, ["mods/y-2.jar"])
                return pack_meta

            info = {"update": True, "source": "curseforge", "name": "Y",
                    "slug": "y", "addon_id": 42, "current": "1.0",
                    "latest": "y-2.zip", "latest_id": 200, "url": None}
            with patch.object(modpack, "install_cf_modpack", side_effect=fake_cf) as im:
                result = modpack.update_modpack(_DM(), inst, info=info, api_key="k")
            im.assert_called_once()
            self.assertEqual(im.call_args.kwargs["file_id"], 200)
            self.assertEqual(im.call_args.kwargs["cf_slug"], "y")
            self.assertEqual(im.call_args.kwargs["api_key"], "k")
        self.assertTrue(result["updated"])
        self.assertEqual(result["to"], "2.0")


if __name__ == "__main__":
    unittest.main()
