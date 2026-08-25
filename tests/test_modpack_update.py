# -*- coding: utf-8 -*-
"""整合包更新检查：Modrinth / CurseForge 身份对比与更新触发。"""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mclauncher import modpack, modpack_update
from mclauncher.downloader import DownloadManager

MR_VERSIONS = [
    {
        "id": "vNew", "name": "2.0", "version_number": "2.0.0",
        "version_type": "release", "game_versions": ["1.20.4"],
        "loaders": ["fabric"],
        "files": [{"url": "http://cdn/new.mrpack", "filename": "pack-2.0.mrpack",
                   "primary": True, "hashes": {}}],
    },
    {
        "id": "vOld", "name": "1.0", "version_number": "1.0.0",
        "version_type": "release", "game_versions": ["1.20.4"],
        "loaders": ["fabric"],
        "files": [{"url": "http://cdn/old.mrpack", "filename": "pack-1.0.mrpack",
                   "primary": True, "hashes": {}}],
    },
]


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path == "/project/coolpack/version":
            data = json.dumps(MR_VERSIONS).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        else:
            self.send_response(404)
            self.end_headers()


class FakeInstance:
    def __init__(self, root, pack=None):
        self.path = Path(root)
        self.name = "t"
        self._meta = {"modpack": pack} if pack else {}

    def meta(self):
        return dict(self._meta)

    def set_meta(self, key, value):
        self._meta[key] = value


class CheckUpdateTests(unittest.TestCase):
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

    def _mr_instance(self, **pack_extra):
        pack = {"name": "CoolPack", "version": "1.0.0", "source": "modrinth",
                "slug": "coolpack", **pack_extra}
        return FakeInstance(tempfile.gettempdir(), pack)

    def test_modrinth_update_available(self):
        inst = self._mr_instance(version_id="vOld", version_number="1.0.0")
        with mock.patch.object(modpack, "MODRINTH_API", self.base):
            info = modpack_update.check_pack_update(inst, dm=self.dm)
        self.assertTrue(info["has_update"])
        self.assertEqual(info["latest"], "2.0.0")
        self.assertEqual(info["latest_id"], "vNew")
        self.assertEqual(info["current"], "1.0.0")

    def test_modrinth_up_to_date(self):
        inst = self._mr_instance(version_id="vNew", version_number="2.0.0")
        with mock.patch.object(modpack, "MODRINTH_API", self.base):
            info = modpack_update.check_pack_update(inst, dm=self.dm)
        self.assertFalse(info["has_update"])

    def test_modrinth_legacy_meta_falls_back_to_version_number(self):
        # 老数据没有 version_id：按版本号字符串比较
        inst = self._mr_instance()
        with mock.patch.object(modpack, "MODRINTH_API", self.base):
            info = modpack_update.check_pack_update(inst, dm=self.dm)
        self.assertTrue(info["has_update"])

    def test_curseforge_update_available(self):
        pack = {"name": "CFPack", "version": "1.2", "source": "curseforge",
                "addon_id": "123", "file_id": "111", "cf_slug": "cfpack"}
        inst = FakeInstance(tempfile.gettempdir(), pack)
        with mock.patch.object(
                modpack_update, "resolve_cf_modpack_file",
                return_value={"addon_id": "123", "file_id": 222,
                              "fileName": "cfpack-1.3.zip", "name": "CFPack"}):
            info = modpack_update.check_pack_update(inst, dm=self.dm)
        self.assertTrue(info["has_update"])
        self.assertEqual(info["latest_id"], "222")

    def test_curseforge_same_file_no_update(self):
        pack = {"name": "CFPack", "version": "1.3", "source": "curseforge",
                "addon_id": "123", "file_id": "222"}
        inst = FakeInstance(tempfile.gettempdir(), pack)
        with mock.patch.object(
                modpack_update, "resolve_cf_modpack_file",
                return_value={"addon_id": "123", "file_id": 222,
                              "fileName": "cfpack-1.3.zip", "name": "CFPack"}):
            info = modpack_update.check_pack_update(inst, dm=self.dm)
        self.assertFalse(info["has_update"])

    def test_not_a_pack_instance(self):
        inst = FakeInstance(tempfile.gettempdir())
        with self.assertRaises(modpack.ModpackError) as ctx:
            modpack_update.check_pack_update(inst, dm=self.dm)
        self.assertIn("不是从整合包", str(ctx.exception))

    def test_missing_identity_hint(self):
        pack = {"name": "OldPack", "version": "1.0", "source": "modrinth"}
        inst = FakeInstance(tempfile.gettempdir(), pack)
        with self.assertRaises(modpack.ModpackError) as ctx:
            modpack_update.check_pack_update(inst, dm=self.dm)
        self.assertIn("重新安装", str(ctx.exception))


class ApplyUpdateTests(unittest.TestCase):
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

    def test_apply_installs_latest_modrinth(self):
        pack = {"name": "CoolPack", "version": "1.0.0", "source": "modrinth",
                "slug": "coolpack", "version_id": "vOld"}
        inst = FakeInstance(tempfile.gettempdir(), pack)
        with mock.patch.object(modpack, "MODRINTH_API", self.base), \
             mock.patch.object(modpack_update, "install_mrpack_by_slug") as im:
            info = modpack_update.apply_pack_update(inst, dm=self.dm)
        self.assertTrue(info.get("updated"))
        im.assert_called_once()
        _args, kwargs = im.call_args
        self.assertEqual(kwargs.get("version_id"), "vNew")

    def test_apply_skips_when_current(self):
        pack = {"name": "CoolPack", "version": "2.0.0", "source": "modrinth",
                "slug": "coolpack", "version_id": "vNew"}
        inst = FakeInstance(tempfile.gettempdir(), pack)
        with mock.patch.object(modpack, "MODRINTH_API", self.base), \
             mock.patch.object(modpack_update, "install_mrpack_by_slug") as im:
            info = modpack_update.apply_pack_update(inst, dm=self.dm)
        self.assertNotIn("updated", info)
        im.assert_not_called()


class SourceMetaTests(unittest.TestCase):
    def test_mrpack_meta_merge_shape(self):
        """source_meta 只并入非空项。"""
        pack_meta = {"name": "X", "version": "1", "source": "modrinth"}
        source_meta = {"slug": "s", "version_id": "", "version_number": "1.0"}
        pack_meta.update({k: v for k, v in source_meta.items() if v})
        self.assertEqual(pack_meta["slug"], "s")
        self.assertEqual(pack_meta["version_number"], "1.0")
        self.assertNotIn("version_id", pack_meta)


if __name__ == "__main__":
    unittest.main()
