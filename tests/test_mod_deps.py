# -*- coding: utf-8 -*-
"""模组必需前置自动安装：CurseForge relationType=3 递归、Modrinth project_id 解析。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mclauncher import mods as mods_mod
from mclauncher.instances import Instance


class FakeDM:
    """把下载写成占位文件，记录 URL；fetch_json 用 canned 数据。"""

    def __init__(self, versions=None):
        self.downloads = []
        self.versions = versions or {}

    def download(self, url, dest, **kw):
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"jar")
        self.downloads.append((url, dest.name))

    def fetch_json(self, url, **kw):
        for vid, data in self.versions.items():
            if url.endswith(f"/version/{vid}"):
                return data
        raise AssertionError(f"unexpected fetch_json: {url}")


class Sandbox(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        patcher = mock.patch(
            "mclauncher.instances.get_instance_path",
            side_effect=lambda name: self.root / "instances" / name)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def make_instance(self, name="deps-test") -> Instance:
        inst = Instance(name)
        inst.create()
        return inst


def cf_file(fid, name, deps=None):
    return {
        "id": fid,
        "fileName": name,
        "gameVersions": ["1.20.1", "Fabric"],
        "downloadUrl": f"https://cdn.example/{name}",
        "dependencies": deps or [],
    }


class TestCurseforgeDeps(Sandbox):
    def run_install(self, cf_mods, addon_id, **kw):
        dm = FakeDM()
        fetched = []

        def detail(dm_, aid, api_key=None):
            fetched.append(str(aid))
            hit = cf_mods.get(str(aid))
            if hit is None:
                raise mods_mod.ModError(f"no mod {aid}")
            return hit

        inst = self.make_instance()
        with mock.patch.object(mods_mod, "cf_detail", side_effect=detail):
            out = mods_mod.install_curseforge_mod(
                dm, addon_id, inst, mc_version="1.20.1", loader="fabric", **kw)
        return out, dm, fetched, inst

    def test_required_dep_installed(self):
        cf_mods = {
            "100": {"id": 100, "name": "Main", "latestFiles": [cf_file(
                1000, "main.jar",
                deps=[{"modId": 200, "relationType": 3},
                      {"modId": 300, "relationType": 2}])]},
            "200": {"id": 200, "name": "Lib", "latestFiles": [cf_file(2000, "lib.jar")]},
            "300": {"id": 300, "name": "Optional", "latestFiles": [cf_file(3000, "opt.jar")]},
        }
        out, dm, fetched, inst = self.run_install(cf_mods, 100)
        self.assertEqual(out["title"], "Main")
        self.assertEqual(sorted(out["files"]), ["lib.jar", "main.jar"])
        self.assertNotIn("300", fetched)  # 可选依赖不装
        self.assertTrue((inst.path / "mods" / "lib.jar").is_file())

    def test_circular_deps_no_loop(self):
        cf_mods = {
            "100": {"id": 100, "name": "A", "latestFiles": [cf_file(
                1000, "a.jar", deps=[{"modId": 200, "relationType": 3}])]},
            "200": {"id": 200, "name": "B", "latestFiles": [cf_file(
                2000, "b.jar", deps=[{"modId": 100, "relationType": 3}])]},
        }
        out, dm, fetched, _ = self.run_install(cf_mods, 100)
        self.assertEqual(sorted(out["files"]), ["a.jar", "b.jar"])
        self.assertEqual(fetched.count("100"), 1)

    def test_dep_failure_does_not_break_main(self):
        cf_mods = {
            "100": {"id": 100, "name": "A", "latestFiles": [cf_file(
                1000, "a.jar", deps=[{"modId": 999, "relationType": 3}])]},
        }
        out, dm, fetched, _ = self.run_install(cf_mods, 100)
        self.assertEqual(out["files"], ["a.jar"])
        self.assertIn("999", fetched)

    def test_depth_limit(self):
        chain = {}
        for i in range(6):
            aid = 100 + i
            deps = [{"modId": aid + 1, "relationType": 3}] if i < 5 else []
            chain[str(aid)] = {"id": aid, "name": f"M{i}", "latestFiles": [
                cf_file(1000 + i, f"m{i}.jar", deps=deps)]}
        out, dm, fetched, _ = self.run_install(chain, 100)
        # 深度 0..3 共 4 层
        self.assertEqual(sorted(out["files"]), ["m0.jar", "m1.jar", "m2.jar", "m3.jar"])

    def test_explicit_file_id_still_works(self):
        cf_mods = {
            "100": {"id": 100, "name": "A", "latestFiles": [
                cf_file(1000, "a-new.jar"), cf_file(999, "a-old.jar")]},
        }
        out, dm, fetched, _ = self.run_install(cf_mods, 100, file_id=999)
        self.assertEqual(out["files"], ["a-old.jar"])

    def test_missing_file_id_raises(self):
        cf_mods = {
            "100": {"id": 100, "name": "A", "latestFiles": [cf_file(1000, "a.jar")]},
        }
        with mock.patch.object(mods_mod, "cf_files", return_value=[]):
            with self.assertRaises(mods_mod.ModError):
                self.run_install(cf_mods, 100, file_id=12345)


def mr_version(vid, pid, filename, deps=None):
    return {
        "id": vid,
        "project_id": pid,
        "version_number": "1.0",
        "loaders": ["fabric"],
        "files": [{"url": f"https://cdn.example/{filename}",
                   "filename": filename, "primary": True}],
        "dependencies": deps or [],
    }


class TestModrinthDeps(Sandbox):
    def install(self, versions_by_slug, slug, dm=None):
        dm = dm or FakeDM()

        def fake_list_versions(dm_, s, game_version=None, loaders=None):
            return versions_by_slug.get(str(s)) or []

        inst = self.make_instance()
        with mock.patch.object(mods_mod, "list_versions", side_effect=fake_list_versions):
            out = mods_mod.install_modrinth_mod(
                dm, slug, inst, mc_version="1.20.1", loader="fabric",
                use_mirror=False)
        return out, dm

    def test_project_id_dependency_resolved(self):
        """依赖只带 project_id（version_id=null）也要装上——Modrinth 的常态。"""
        versions = {
            "main-mod": [mr_version("v1", "p-main", "main.jar", deps=[
                {"dependency_type": "required", "project_id": "p-lib",
                 "version_id": None}])],
            "p-lib": [mr_version("v2", "p-lib", "lib.jar")],
        }
        out, dm = self.install(versions, "main-mod")
        self.assertEqual(sorted(out["files"]), ["lib.jar", "main.jar"])

    def test_optional_project_dep_skipped(self):
        versions = {
            "main-mod": [mr_version("v1", "p-main", "main.jar", deps=[
                {"dependency_type": "optional", "project_id": "p-opt",
                 "version_id": None}])],
            "p-opt": [mr_version("v9", "p-opt", "opt.jar")],
        }
        out, dm = self.install(versions, "main-mod")
        self.assertEqual(out["files"], ["main.jar"])

    def test_version_id_dependency_still_works(self):
        dep_ver = mr_version("v-dep", "p-dep", "dep.jar")
        dm = FakeDM(versions={"v-dep": dep_ver})
        versions = {
            "main-mod": [mr_version("v1", "p-main", "main.jar", deps=[
                {"dependency_type": "required", "project_id": "p-dep",
                 "version_id": "v-dep"}])],
        }
        out, dm = self.install(versions, "main-mod", dm=dm)
        self.assertEqual(sorted(out["files"]), ["dep.jar", "main.jar"])

    def test_circular_project_deps_no_loop(self):
        versions = {
            "main-mod": [mr_version("v1", "p-a", "a.jar", deps=[
                {"dependency_type": "required", "project_id": "p-b",
                 "version_id": None}])],
            "p-b": [mr_version("v2", "p-b", "b.jar", deps=[
                {"dependency_type": "required", "project_id": "p-a",
                 "version_id": None}])],
        }
        out, dm = self.install(versions, "main-mod")
        self.assertEqual(sorted(out["files"]), ["a.jar", "b.jar"])

    def test_dep_resolve_failure_only_warns(self):
        versions = {
            "main-mod": [mr_version("v1", "p-main", "main.jar", deps=[
                {"dependency_type": "required", "project_id": "p-ghost",
                 "version_id": None}])],
        }
        out, dm = self.install(versions, "main-mod")
        self.assertEqual(out["files"], ["main.jar"])


if __name__ == "__main__":
    unittest.main()
