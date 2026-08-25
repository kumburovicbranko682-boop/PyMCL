# -*- coding: utf-8 -*-
"""导出整合包（.mrpack）：依赖推断、Modrinth 反查、zip 结构。"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import threading
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mclauncher import modpack_export
from mclauncher.downloader import DownloadManager

JAR_A = b"PK\x03\x04matched-mod-bytes"
JAR_B = b"PK\x03\x04unmatched-mod-bytes"
SHA1_A = hashlib.sha1(JAR_A).hexdigest()

VERSION_OBJ = {
    "id": "verA",
    "project_id": "projA",
    "files": [
        {
            "hashes": {"sha1": SHA1_A},
            "url": "https://cdn.modrinth.com/data/projA/a.jar",
            "primary": True,
        },
    ],
}


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _json(self, payload, code=200):
        data = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        if self.path == "/v2/version_files":
            hashes = body.get("hashes") or []
            self._json({h: VERSION_OBJ for h in hashes if h == SHA1_A})
        elif self.path == "/nobatch/v2/version_files":
            self._json({"error": "no"}, code=405)
        else:
            self._json({}, code=404)

    def do_GET(self):
        if self.path == f"/nobatch/v2/version_file/{SHA1_A}":
            self._json(VERSION_OBJ)
        else:
            self._json({}, code=404)


class FakeInstance:
    def __init__(self, root):
        self.path = Path(root)
        self.name = "t"

    def versions_dir(self):
        return self.path / "versions"

    def version_json(self, vid):
        p = self.versions_dir() / vid / f"{vid}.json"
        if p.is_file():
            return json.loads(p.read_text(encoding="utf-8"))
        return None


def _write_version(inst, vid, data):
    d = inst.versions_dir() / vid
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{vid}.json").write_text(json.dumps(data), encoding="utf-8")


def _fabric_instance(root):
    inst = FakeInstance(root)
    _write_version(inst, "1.20.4", {"id": "1.20.4", "libraries": []})
    _write_version(inst, "fabric-loader-0.15.11-1.20.4", {
        "id": "fabric-loader-0.15.11-1.20.4",
        "inheritsFrom": "1.20.4",
        "libraries": [{"name": "net.fabricmc:fabric-loader:0.15.11"}],
    })
    mods = inst.path / "mods"
    mods.mkdir(parents=True)
    (mods / "a.jar").write_bytes(JAR_A)
    (mods / "b.jar").write_bytes(JAR_B)
    (mods / "off.jar.disabled").write_bytes(b"disabled")
    cfg = inst.path / "config"
    cfg.mkdir()
    (cfg / "foo.toml").write_text("x=1", encoding="utf-8")
    (inst.path / "options.txt").write_text("fov:0.5", encoding="utf-8")
    return inst


class PackDependenciesTests(unittest.TestCase):
    def test_fabric_chain(self):
        with tempfile.TemporaryDirectory() as td:
            inst = _fabric_instance(td)
            deps = modpack_export.pack_dependencies(inst, "fabric-loader-0.15.11-1.20.4")
        self.assertEqual(deps, {"fabric-loader": "0.15.11", "minecraft": "1.20.4"})

    def test_forge_monolithic(self):
        with tempfile.TemporaryDirectory() as td:
            inst = FakeInstance(td)
            _write_version(inst, "1.20.4-forge-49.0.30", {
                "id": "1.20.4-forge-49.0.30",
                "libraries": [{"name": "net.minecraftforge:forge:1.20.4-49.0.30"}],
            })
            deps = modpack_export.pack_dependencies(inst, "1.20.4-forge-49.0.30")
        self.assertEqual(deps["forge"], "49.0.30")
        self.assertEqual(deps["minecraft"], "1.20.4")

    def test_neoforge(self):
        with tempfile.TemporaryDirectory() as td:
            inst = FakeInstance(td)
            _write_version(inst, "1.20.4", {"id": "1.20.4"})
            _write_version(inst, "neoforge-20.4.190", {
                "id": "neoforge-20.4.190",
                "inheritsFrom": "1.20.4",
                "libraries": [{"name": "net.neoforged:neoforge:20.4.190"}],
            })
            deps = modpack_export.pack_dependencies(inst, "neoforge-20.4.190")
        self.assertEqual(deps, {"neoforge": "20.4.190", "minecraft": "1.20.4"})

    def test_vanilla(self):
        with tempfile.TemporaryDirectory() as td:
            inst = FakeInstance(td)
            _write_version(inst, "1.21", {"id": "1.21"})
            deps = modpack_export.pack_dependencies(inst, "1.21")
        self.assertEqual(deps, {"minecraft": "1.21"})

    def test_missing_json(self):
        with tempfile.TemporaryDirectory() as td:
            inst = FakeInstance(td)
            self.assertEqual(modpack_export.pack_dependencies(inst, "nope"), {})


class ExportMrpackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        host, port = cls.server.server_address
        cls.base = f"http://{host}:{port}"
        cls.dm = DownloadManager(threads=1)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def _export(self, api_suffix="/v2", **kw):
        with tempfile.TemporaryDirectory() as td:
            inst = _fabric_instance(td)
            dest = Path(td) / "out.mrpack"
            with mock.patch.object(modpack_export, "MODRINTH_API",
                                   self.base + api_suffix):
                result = modpack_export.export_mrpack(
                    inst, "fabric-loader-0.15.11-1.20.4", dest,
                    name="MyPack", pack_version="2.0.0", dm=self.dm, **kw)
            with zipfile.ZipFile(dest) as z:
                names = sorted(z.namelist())
                index = json.loads(z.read("modrinth.index.json"))
            return result, names, index

    def test_export_structure(self):
        result, names, index = self._export()
        self.assertEqual(index["formatVersion"], 1)
        self.assertEqual(index["name"], "MyPack")
        self.assertEqual(index["versionId"], "2.0.0")
        self.assertEqual(index["dependencies"],
                         {"fabric-loader": "0.15.11", "minecraft": "1.20.4"})
        # a.jar 反查命中 → files；b.jar 未命中 → overrides
        self.assertEqual(len(index["files"]), 1)
        entry = index["files"][0]
        self.assertEqual(entry["path"], "mods/a.jar")
        self.assertEqual(entry["hashes"]["sha1"], SHA1_A)
        self.assertEqual(entry["hashes"]["sha512"],
                         hashlib.sha512(JAR_A).hexdigest())
        self.assertEqual(entry["downloads"],
                         ["https://cdn.modrinth.com/data/projA/a.jar"])
        self.assertEqual(entry["fileSize"], len(JAR_A))
        self.assertIn("overrides/mods/b.jar", names)
        self.assertNotIn("overrides/mods/a.jar", names)
        # 禁用的模组不打包
        self.assertNotIn("overrides/mods/off.jar.disabled", names)
        # 默认 overrides：config 与 options.txt
        self.assertIn("overrides/config/foo.toml", names)
        self.assertIn("overrides/options.txt", names)
        self.assertEqual(result["mods"], 2)
        self.assertEqual(result["matched"], 1)
        self.assertEqual(result["overrides"], 1)

    def test_batch_falls_back_to_single(self):
        result, names, index = self._export(api_suffix="/nobatch/v2")
        self.assertEqual(len(index["files"]), 1)
        self.assertEqual(index["files"][0]["hashes"]["sha1"], SHA1_A)
        self.assertIn("overrides/mods/b.jar", names)

    def test_missing_version_raises(self):
        with tempfile.TemporaryDirectory() as td:
            inst = FakeInstance(td)
            with self.assertRaises(modpack_export.ExportError):
                modpack_export.export_mrpack(inst, "nope", Path(td) / "x.mrpack",
                                             dm=self.dm)


if __name__ == "__main__":
    unittest.main()
