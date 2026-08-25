# -*- coding: utf-8 -*-
"""模组必需前置自动安装：CurseForge relationType=3 与 Modrinth project-only 依赖。"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mclauncher import mods


class _Inst:
    """install_* 只用到 ensure_standard_dirs（mods_dir 显式传入）。"""

    name = "测试实例"

    def ensure_standard_dirs(self):
        pass


class _FakeDM:
    """记录下载并落地空文件；fetch_json 按 URL 路由到测试数据。"""

    def __init__(self, routes=None):
        self.routes = routes or {}
        self.downloads = []

    def download(self, url, dest, **kw):
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"jar-bytes")
        self.downloads.append((url, dest.name))
        return dest

    def fetch_json(self, url, **kw):
        for key, value in self.routes.items():
            if key in url:
                if isinstance(value, Exception):
                    raise value
                return value
        raise AssertionError(f"未预期的请求: {url}")


def _cf_file(fid, name, deps=None, gvs=("1.20.1", "Fabric")):
    return {
        "id": fid,
        "fileName": name,
        "gameVersions": list(gvs),
        "downloadUrl": f"http://cdn.test/{name}",
        "dependencies": [{"modId": d, "relationType": 3} for d in (deps or [])],
    }


def _mr_version(vid, pid, fname, deps=None):
    return {
        "id": vid,
        "project_id": pid,
        "name": fname,
        "version_number": "1.0.0",
        "version_type": "release",
        "game_versions": ["1.20.1"],
        "loaders": ["fabric"],
        "files": [{"url": f"http://cdn.test/{fname}", "filename": fname,
                   "primary": True, "hashes": {}, "size": 9}],
        "dependencies": deps or [],
    }


class CfDepExtractTest(unittest.TestCase):
    def test_required_only(self):
        f = {"dependencies": [
            {"modId": 306612, "relationType": 3},
            {"modId": 999, "relationType": 2},      # optional，不装
            {"modId": 111, "relationType": 5},      # incompatible
            {"modId": "306612", "relationType": 3},  # 重复（字符串形式）
            {"modId": None, "relationType": 3},
            "garbage",
        ]}
        self.assertEqual(mods._cf_required_dep_ids(f), [306612])

    def test_empty(self):
        self.assertEqual(mods._cf_required_dep_ids({}), [])
        self.assertEqual(mods._cf_required_dep_ids(None), [])


class CfPickFileTest(unittest.TestCase):
    def test_game_version_filter(self):
        files = [_cf_file(1, "old.jar", gvs=("1.19.2", "Fabric")),
                 _cf_file(2, "new.jar", gvs=("1.20.1", "Fabric"))]
        picked = mods._cf_pick_file(files, "1.20.1", "fabric")
        self.assertEqual(picked["id"], 2)

    def test_loader_preference(self):
        files = [_cf_file(1, "forge.jar", gvs=("1.20.1", "Forge")),
                 _cf_file(2, "fabric.jar", gvs=("1.20.1", "Fabric"))]
        picked = mods._cf_pick_file(files, "1.20.1", "fabric")
        self.assertEqual(picked["id"], 2)

    def test_no_match_returns_none(self):
        files = [_cf_file(1, "old.jar", gvs=("1.12.2", "Forge"))]
        self.assertIsNone(mods._cf_pick_file(files, "1.20.1", "fabric"))
        self.assertIsNone(mods._cf_pick_file([], "1.20.1", "fabric"))


class CfInstallDepsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mods_dir = Path(self.tmp.name) / "mods"
        self.dm = _FakeDM()

    def tearDown(self):
        self.tmp.cleanup()

    def _install(self, details, files_by_id, addon_id=100):
        def fake_detail(dm, aid, api_key=None):
            hit = details.get(int(aid))
            if isinstance(hit, Exception):
                raise hit
            if hit is None:
                raise mods.ModError(f"项目 {aid} 不存在")
            return hit

        def fake_files(dm, aid, api_key=None, game_version=None,
                       mod_loader=None, page_size=50):
            out = files_by_id.get(int(aid)) or []
            if game_version:
                out = [f for f in out if game_version in (f.get("gameVersions") or [])]
            if not out:
                raise mods.ModError("没有可下载文件")
            return out

        with mock.patch.object(mods, "cf_detail", fake_detail), \
                mock.patch.object(mods, "cf_files", fake_files):
            return mods.install_curseforge_mod(
                self.dm, addon_id, _Inst(), mc_version="1.20.1", loader="fabric",
                mods_dir=self.mods_dir)

    def test_installs_required_dep(self):
        main = _cf_file(11, "sodium-extra.jar", deps=[200])
        dep = _cf_file(21, "fabric-api.jar")
        result = self._install(
            details={100: {"id": 100, "name": "Sodium Extra", "latestFiles": [main]},
                     200: {"id": 200, "name": "Fabric API", "latestFiles": [dep]}},
            files_by_id={200: [dep]})
        self.assertEqual(result["files"], ["sodium-extra.jar", "fabric-api.jar"])
        self.assertEqual(result["warnings"], [])
        self.assertTrue((self.mods_dir / "fabric-api.jar").is_file())

    def test_circular_deps_do_not_loop(self):
        main = _cf_file(11, "a.jar", deps=[200])
        dep = _cf_file(21, "b.jar", deps=[100])  # 反向依赖回主模组
        result = self._install(
            details={100: {"id": 100, "name": "A", "latestFiles": [main]},
                     200: {"id": 200, "name": "B", "latestFiles": [dep]}},
            files_by_id={200: [dep]})
        self.assertEqual(sorted(result["files"]), ["a.jar", "b.jar"])
        self.assertEqual(len(self.dm.downloads), 2)

    def test_dep_failure_keeps_main_and_warns(self):
        main = _cf_file(11, "main.jar", deps=[200])
        result = self._install(
            details={100: {"id": 100, "name": "Main", "latestFiles": [main]},
                     200: mods.ModError("接口超时")},
            files_by_id={})
        self.assertEqual(result["files"], ["main.jar"])
        self.assertEqual(len(result["warnings"]), 1)
        self.assertIn("必需前置", result["warnings"][0])

    def test_dep_already_present_skipped(self):
        self.mods_dir.mkdir(parents=True)
        (self.mods_dir / "fabric-api.jar").write_bytes(b"existing")
        main = _cf_file(11, "main.jar", deps=[200])
        dep = _cf_file(21, "fabric-api.jar")
        result = self._install(
            details={100: {"id": 100, "name": "Main", "latestFiles": [main]},
                     200: {"id": 200, "name": "Fabric API", "latestFiles": [dep]}},
            files_by_id={200: [dep]})
        self.assertEqual(result["files"], ["main.jar"])
        # 已存在的前置不重复下载，也不当失败
        self.assertEqual(result["warnings"], [])
        self.assertEqual((self.mods_dir / "fabric-api.jar").read_bytes(), b"existing")


class ModrinthDepsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mods_dir = Path(self.tmp.name) / "mods"

    def tearDown(self):
        self.tmp.cleanup()

    def test_project_only_dep_resolved(self):
        alpha = _mr_version("vA", "pAlpha", "alpha.jar",
                            deps=[{"project_id": "pBeta", "version_id": None,
                                   "dependency_type": "required"}])
        beta = _mr_version("vB", "pBeta", "beta.jar",
                           deps=[{"project_id": "pAlpha", "version_id": None,
                                  "dependency_type": "required"}])  # 环
        dm = _FakeDM(routes={
            "/project/pAlpha/version": [alpha],
            "/project/pBeta/version": [beta],
        })
        result = mods.install_modrinth_mod(
            dm, "pAlpha", _Inst(), mc_version="1.20.1", loader="fabric",
            use_mirror=False, mods_dir=self.mods_dir)
        self.assertEqual(sorted(result["files"]), ["alpha.jar", "beta.jar"])
        self.assertEqual(result["warnings"], [])
        self.assertEqual(len(dm.downloads), 2)  # 环形依赖只装一遍

    def test_optional_dep_not_installed(self):
        alpha = _mr_version("vA", "pAlpha", "alpha.jar",
                            deps=[{"project_id": "pBeta", "version_id": None,
                                   "dependency_type": "optional"}])
        dm = _FakeDM(routes={"/project/pAlpha/version": [alpha]})
        result = mods.install_modrinth_mod(
            dm, "pAlpha", _Inst(), mc_version="1.20.1", loader="fabric",
            use_mirror=False, mods_dir=self.mods_dir)
        self.assertEqual(result["files"], ["alpha.jar"])

    def test_dep_failure_warns(self):
        alpha = _mr_version("vA", "pAlpha", "alpha.jar",
                            deps=[{"project_id": "pGone", "version_id": None,
                                   "dependency_type": "required"}])
        dm = _FakeDM(routes={
            "/project/pAlpha/version": [alpha],
            "/project/pGone/version": mods.ModError("404"),
        })
        result = mods.install_modrinth_mod(
            dm, "pAlpha", _Inst(), mc_version="1.20.1", loader="fabric",
            use_mirror=False, mods_dir=self.mods_dir)
        self.assertEqual(result["files"], ["alpha.jar"])
        self.assertEqual(len(result["warnings"]), 1)
        self.assertIn("pGone", result["warnings"][0])


if __name__ == "__main__":
    unittest.main()
