from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mclauncher import mods


class _FakeInstance:
    def __init__(self, root: Path):
        self.path = root
        self.name = "test"

    def ensure_standard_dirs(self):
        pass


class _FakeDM:
    """按 URL 前缀路由的假下载器。"""

    def __init__(self, json_routes: dict):
        self.json_routes = dict(json_routes)
        self.json_calls = []
        self.downloads = []

    def fetch_json(self, url, params=None, headers=None, timeout=None, expand=True):
        self.json_calls.append(url)
        for prefix, payload in self.json_routes.items():
            if url.startswith(prefix):
                if isinstance(payload, Exception):
                    raise payload
                return payload
        raise AssertionError(f"unexpected fetch_json: {url}")

    def download(self, url, dest, sha1=None, size=None, sha512=None,
                 urls=None, timeout=None, expand=True, force=False):
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"jar")
        self.downloads.append({"url": url, "dest": dest, "sha1": sha1})
        return dest


def _mr_file(filename):
    return {"url": f"https://cdn.modrinth.com/{filename}", "filename": filename,
            "primary": True, "size": 3, "hashes": {}}


class ModrinthDependencyTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.root = Path(self.td.name)
        self.inst = _FakeInstance(self.root)
        self.mods_dir = self.root / "mods"

    def test_project_id_only_dependency_installed(self):
        """必需依赖只给 project_id（Modrinth 常态）时也要装上。"""
        main_version = {
            "id": "v-main", "project_id": "p-main", "version_number": "1.0",
            "files": [_mr_file("main.jar")],
            "dependencies": [
                {"dependency_type": "required", "project_id": "p-dep", "version_id": None},
            ],
        }
        dep_versions = [{
            "id": "v-dep", "version_number": "0.9", "game_versions": ["1.20.1"],
            "loaders": ["fabric"], "files": [_mr_file("dep.jar")], "dependencies": [],
        }]
        dm = _FakeDM({
            f"{mods.MODRINTH_API}/version/v-main": main_version,
            f"{mods.MODRINTH_API}/project/p-dep/version": dep_versions,
        })
        out = mods.install_modrinth_mod(
            dm, "p-main", self.inst, mc_version="1.20.1", loader="fabric",
            use_mirror=False, version_id="v-main", mods_dir=self.mods_dir)
        self.assertEqual(sorted(out["files"]), ["dep.jar", "main.jar"])
        self.assertTrue((self.mods_dir / "dep.jar").is_file())

    def test_version_id_dependency_still_installed(self):
        main_version = {
            "id": "v-main", "project_id": "p-main", "version_number": "1.0",
            "files": [_mr_file("main.jar")],
            "dependencies": [
                {"dependency_type": "required", "project_id": "p-dep", "version_id": "v-dep"},
            ],
        }
        dep_version = {
            "id": "v-dep", "project_id": "p-dep", "version_number": "0.9",
            "files": [_mr_file("dep.jar")], "dependencies": [],
        }
        dm = _FakeDM({
            f"{mods.MODRINTH_API}/version/v-main": main_version,
            f"{mods.MODRINTH_API}/version/v-dep": dep_version,
        })
        out = mods.install_modrinth_mod(
            dm, "p-main", self.inst, mc_version="1.20.1", loader="fabric",
            use_mirror=False, version_id="v-main", mods_dir=self.mods_dir)
        self.assertEqual(sorted(out["files"]), ["dep.jar", "main.jar"])

    def test_optional_dependency_skipped(self):
        main_version = {
            "id": "v-main", "project_id": "p-main", "version_number": "1.0",
            "files": [_mr_file("main.jar")],
            "dependencies": [
                {"dependency_type": "optional", "project_id": "p-opt", "version_id": None},
            ],
        }
        dm = _FakeDM({f"{mods.MODRINTH_API}/version/v-main": main_version})
        out = mods.install_modrinth_mod(
            dm, "p-main", self.inst, mc_version="1.20.1", loader="fabric",
            use_mirror=False, version_id="v-main", mods_dir=self.mods_dir)
        self.assertEqual(out["files"], ["main.jar"])
        self.assertFalse(any("p-opt" in u for u in dm.json_calls))

    def test_dependency_failure_does_not_break_main(self):
        main_version = {
            "id": "v-main", "project_id": "p-main", "version_number": "1.0",
            "files": [_mr_file("main.jar")],
            "dependencies": [
                {"dependency_type": "required", "project_id": "p-dead", "version_id": None},
            ],
        }
        dm = _FakeDM({
            f"{mods.MODRINTH_API}/version/v-main": main_version,
            f"{mods.MODRINTH_API}/project/p-dead/version": RuntimeError("404"),
        })
        out = mods.install_modrinth_mod(
            dm, "p-main", self.inst, mc_version="1.20.1", loader="fabric",
            use_mirror=False, version_id="v-main", mods_dir=self.mods_dir)
        self.assertEqual(out["files"], ["main.jar"])

    def test_duplicate_project_resolved_once(self):
        """两个模组共享同一依赖 project 时只解析一次。"""
        main_version = {
            "id": "v-main", "project_id": "p-main", "version_number": "1.0",
            "files": [_mr_file("main.jar")],
            "dependencies": [
                {"dependency_type": "required", "project_id": "p-dep", "version_id": None},
                {"dependency_type": "required", "project_id": "p-dep", "version_id": None},
            ],
        }
        dep_versions = [{
            "id": "v-dep", "version_number": "0.9", "game_versions": ["1.20.1"],
            "loaders": ["fabric"], "files": [_mr_file("dep.jar")], "dependencies": [],
        }]
        dm = _FakeDM({
            f"{mods.MODRINTH_API}/version/v-main": main_version,
            f"{mods.MODRINTH_API}/project/p-dep/version": dep_versions,
        })
        out = mods.install_modrinth_mod(
            dm, "p-main", self.inst, mc_version="1.20.1", loader="fabric",
            use_mirror=False, version_id="v-main", mods_dir=self.mods_dir)
        self.assertEqual(sorted(out["files"]), ["dep.jar", "main.jar"])
        calls = [u for u in dm.json_calls if "p-dep" in u]
        self.assertEqual(len(calls), 1, calls)


def _cf_mod(addon_id, name, file_obj):
    return {"data": {"id": addon_id, "name": name, "latestFiles": [file_obj]}}


def _cf_file(file_id, filename, deps=(), sha1=None):
    out = {
        "id": file_id, "fileName": filename,
        "downloadUrl": f"https://edge.forgecdn.net/files/{file_id}/{filename}",
        "gameVersions": ["1.20.1", "Fabric"],
        "dependencies": list(deps),
    }
    if sha1:
        out["hashes"] = [{"value": sha1, "algo": 1}]
    return out


class CurseForgeDependencyTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.root = Path(self.td.name)
        self.inst = _FakeInstance(self.root)
        self.mods_dir = self.root / "mods"

    def _routes(self, *mods_payloads):
        routes = {}
        for payload in mods_payloads:
            addon_id = payload["data"]["id"]
            for base in mods.cf_api_bases():
                routes[f"{base}/mods/{addon_id}"] = payload
        return routes

    def test_required_dependency_installed(self):
        dep_file = _cf_file(20, "fabric-api.jar")
        main_file = _cf_file(
            10, "main.jar",
            deps=[{"modId": 222, "relationType": mods.CF_RELATION_REQUIRED}],
            sha1="a" * 40)
        dm = _FakeDM(self._routes(_cf_mod(111, "Main", main_file),
                                  _cf_mod(222, "Fabric API", dep_file)))
        out = mods.install_curseforge_mod(
            dm, 111, self.inst, mc_version="1.20.1", loader="fabric",
            mods_dir=self.mods_dir)
        self.assertEqual(sorted(out["files"]), ["fabric-api.jar", "main.jar"])
        self.assertTrue((self.mods_dir / "fabric-api.jar").is_file())
        # 主文件的 sha1 传给了下载器
        main_dl = next(d for d in dm.downloads if d["dest"].name == "main.jar")
        self.assertEqual(main_dl["sha1"], "a" * 40)

    def test_optional_dependency_skipped(self):
        main_file = _cf_file(
            10, "main.jar",
            deps=[{"modId": 333, "relationType": 2}])  # 2 = optional
        dm = _FakeDM(self._routes(_cf_mod(111, "Main", main_file)))
        out = mods.install_curseforge_mod(
            dm, 111, self.inst, mc_version="1.20.1", loader="fabric",
            mods_dir=self.mods_dir)
        self.assertEqual(out["files"], ["main.jar"])

    def test_dependency_cycle_terminates(self):
        f_a = _cf_file(10, "a.jar", deps=[{"modId": 222, "relationType": 3}])
        f_b = _cf_file(20, "b.jar", deps=[{"modId": 111, "relationType": 3}])
        dm = _FakeDM(self._routes(_cf_mod(111, "A", f_a), _cf_mod(222, "B", f_b)))
        out = mods.install_curseforge_mod(
            dm, 111, self.inst, mc_version="1.20.1", loader="fabric",
            mods_dir=self.mods_dir)
        self.assertEqual(sorted(out["files"]), ["a.jar", "b.jar"])

    def test_dependency_failure_does_not_break_main(self):
        main_file = _cf_file(10, "main.jar",
                             deps=[{"modId": 999, "relationType": 3}])
        dm = _FakeDM(self._routes(_cf_mod(111, "Main", main_file)))
        out = mods.install_curseforge_mod(
            dm, 111, self.inst, mc_version="1.20.1", loader="fabric",
            mods_dir=self.mods_dir)
        self.assertEqual(out["files"], ["main.jar"])


if __name__ == "__main__":
    unittest.main()
