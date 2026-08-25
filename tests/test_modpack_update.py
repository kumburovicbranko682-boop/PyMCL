# -*- coding: utf-8 -*-
"""整合包更新：安装清单记录、状态判定、检查更新、原地更新清理/备份。"""
from __future__ import annotations

import hashlib
import http.server
import json
import os
import tempfile
import threading
import unittest
import zipfile
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from mclauncher import modpack, modpack_update, utils
from mclauncher.downloader import DownloadManager
from mclauncher.instances import Instance
from mclauncher.modpack import ModpackError


@contextmanager
def file_server(directory: Path):
    class QuietHandler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(directory), **kw)

        def log_message(self, *_args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()


def sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def make_jar(marker: str) -> bytes:
    """合法的迷你 zip（下载器要求 jar 以 PK 开头）。"""
    import io
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("marker.txt", marker)
    return buf.getvalue()


JAR_A = make_jar("AAA")
JAR_B = make_jar("BBB")


class InstanceSandbox(unittest.TestCase):
    """把实例根指到临时目录，避免污染仓库。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        patcher = mock.patch(
            "mclauncher.instances.get_instance_path",
            side_effect=lambda name: self.root / "instances" / name)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def make_instance(self, name="pack-test") -> Instance:
        inst = Instance(name)
        inst.create()
        return inst


class TestInstallRecordsManifest(InstanceSandbox):
    def _build_mrpack(self, base_url: str, dest: Path):
        index = {
            "formatVersion": 1,
            "game": "minecraft",
            "name": "TestPack",
            "versionId": "1.0.0",
            "dependencies": {"minecraft": "1.20.1"},
            "files": [
                {
                    "path": "mods/a.jar",
                    "downloads": [f"{base_url}/a.jar"],
                    "hashes": {"sha1": sha1(JAR_A)},
                    "fileSize": len(JAR_A),
                },
                {
                    "path": "mods/b.jar",
                    "downloads": [f"{base_url}/b.jar"],
                    "hashes": {"sha1": sha1(JAR_B)},
                    "fileSize": len(JAR_B),
                },
                {
                    "path": "mods/server-only.jar",
                    "downloads": [f"{base_url}/a.jar"],
                    "env": {"client": "unsupported", "server": "required"},
                },
            ],
        }
        with zipfile.ZipFile(dest, "w") as z:
            z.writestr("modrinth.index.json", json.dumps(index))
            z.writestr("overrides/config/foo.cfg", "key=1\n")

    def test_meta_has_manifest_and_source(self):
        serve = self.root / "serve"
        serve.mkdir()
        (serve / "a.jar").write_bytes(JAR_A)
        (serve / "b.jar").write_bytes(JAR_B)
        inst = self.make_instance()
        with file_server(serve) as base:
            pack_file = self.root / "test.mrpack"
            self._build_mrpack(base, pack_file)
            dm = DownloadManager(threads=2)
            with mock.patch.object(modpack, "Installer") as fake_installer, \
                    mock.patch.object(modpack, "_resolve_pack_minecraft",
                                      side_effect=lambda dm_, d, p=None: d):
                meta = modpack.install_mrpack(
                    dm, str(pack_file), inst,
                    slug="testpack", source_version_id="ver-1")
            fake_installer.assert_called_once()

        self.assertEqual(meta["name"], "TestPack")
        self.assertEqual(meta["slug"], "testpack")
        self.assertEqual(meta["source_version_id"], "ver-1")
        self.assertEqual(sorted(meta["managed_files"]), ["mods/a.jar", "mods/b.jar"])
        self.assertEqual([o["path"] for o in meta["override_files"]], ["config/foo.cfg"])
        self.assertEqual(meta["override_files"][0]["sha1"],
                         sha1(b"key=1\n"))
        self.assertTrue((inst.path / "mods" / "a.jar").is_file())
        self.assertTrue((inst.path / "config" / "foo.cfg").is_file())
        # meta 落盘
        stored = inst.meta().get("modpack")
        self.assertEqual(stored["managed_files"], meta["managed_files"])

        state = modpack_update.pack_state(inst)
        self.assertTrue(state["installed"])
        self.assertTrue(state["can_update"])


class TestPackState(InstanceSandbox):
    def test_not_a_pack(self):
        inst = self.make_instance("plain")
        self.assertFalse(modpack_update.pack_state(inst)["installed"])

    def test_legacy_install_without_manifest(self):
        inst = self.make_instance("legacy")
        inst.set_meta("modpack", {"name": "Old", "version": "1", "source": "modrinth"})
        state = modpack_update.pack_state(inst)
        self.assertTrue(state["installed"])
        self.assertFalse(state["can_update"])
        self.assertIn("旧版", state["reason"])

    def test_local_install_without_source(self):
        inst = self.make_instance("local")
        inst.set_meta("modpack", {
            "name": "L", "version": "1", "source": "modrinth",
            "managed_files": [], "override_files": [], "slug": ""})
        state = modpack_update.pack_state(inst)
        self.assertFalse(state["can_update"])
        self.assertIn("本地", state["reason"])


class TestCheckUpdate(InstanceSandbox):
    def _pack_meta(self, version_id="ver-1"):
        return {
            "name": "TestPack", "version": "1.0.0", "source": "modrinth",
            "slug": "testpack", "source_version_id": version_id,
            "managed_files": ["mods/a.jar"], "override_files": [],
        }

    def _versions(self):
        return [
            {
                "id": "ver-2", "version_number": "1.1.0", "version_type": "release",
                "date_published": "2026-08-01T00:00:00Z", "changelog": "new stuff",
                "files": [{"filename": "TestPack-1.1.0.mrpack",
                           "url": "https://cdn.example/t.mrpack", "primary": True}],
            },
            {
                "id": "ver-1", "version_number": "1.0.0", "version_type": "release",
                "date_published": "2026-07-01T00:00:00Z",
                "files": [{"filename": "TestPack-1.0.0.mrpack",
                           "url": "https://cdn.example/o.mrpack", "primary": True}],
            },
        ]

    def test_update_available(self):
        inst = self.make_instance("chk")
        inst.set_meta("modpack", self._pack_meta("ver-1"))
        with mock.patch.object(modpack_update, "modrinth_versions",
                               return_value=self._versions()):
            res = modpack_update.check_update(DownloadManager(threads=1), inst)
        self.assertTrue(res["has_update"])
        self.assertEqual(res["latest"], "1.1.0")
        self.assertEqual(res["latest_id"], "ver-2")
        self.assertEqual(res["current"], "1.0.0")
        self.assertIn("new stuff", res["changelog"])

    def test_already_latest(self):
        inst = self.make_instance("chk2")
        inst.set_meta("modpack", self._pack_meta("ver-2"))
        with mock.patch.object(modpack_update, "modrinth_versions",
                               return_value=self._versions()):
            res = modpack_update.check_update(DownloadManager(threads=1), inst)
        self.assertFalse(res["has_update"])

    def test_legacy_raises(self):
        inst = self.make_instance("chk3")
        inst.set_meta("modpack", {"name": "Old", "source": "modrinth"})
        with self.assertRaises(ModpackError):
            modpack_update.check_update(DownloadManager(threads=1), inst)

    def test_curseforge_compare(self):
        inst = self.make_instance("chk4")
        inst.set_meta("modpack", {
            "name": "CFPack", "version": "2.0", "source": "curseforge",
            "addon_id": "123", "file_id": "1000", "slug": "cfpack",
            "managed_files": [], "override_files": [],
        })
        with mock.patch.object(modpack_update, "resolve_cf_modpack_file",
                               return_value={"file_id": 2000,
                                             "fileName": "CFPack-3.0.zip"}):
            res = modpack_update.check_update(DownloadManager(threads=1), inst)
        self.assertTrue(res["has_update"])
        self.assertEqual(res["latest_id"], "2000")


class TestUpdateFlow(InstanceSandbox):
    def test_cleanup_backup_reinstall(self):
        inst = self.make_instance("upd")
        (inst.path / "mods").mkdir(exist_ok=True)
        (inst.path / "config").mkdir(exist_ok=True)
        (inst.path / "mods" / "a.jar").write_bytes(b"OLD")
        (inst.path / "config" / "pack.cfg").write_text("original\n")
        (inst.path / "config" / "user.cfg").write_text("user changed this\n")
        (inst.path / "config" / "mine.cfg").write_text("purely mine\n")
        inst.set_meta("modpack", {
            "name": "TestPack", "version": "1.0.0", "source": "modrinth",
            "slug": "testpack", "source_version_id": "ver-1",
            "managed_files": ["mods/a.jar", "mods/gone.jar"],
            "override_files": [
                {"path": "config/pack.cfg", "sha1": sha1(b"original\n")},
                {"path": "config/user.cfg", "sha1": sha1(b"something else\n")},
            ],
        })

        def fake_install(dm, slug, instance, on_progress=None, cancel=None,
                         version_id=None, **kw):
            (instance.path / "mods" / "c.jar").write_bytes(b"NEW")
            meta = {
                "name": "TestPack", "version": "1.1.0", "source": "modrinth",
                "slug": slug, "source_version_id": version_id or "ver-2",
                "managed_files": ["mods/c.jar"], "override_files": [],
                "instance": instance.name,
            }
            instance.set_meta("modpack", meta)
            return meta

        with mock.patch.object(modpack_update, "install_mrpack_by_slug",
                               side_effect=fake_install):
            meta = modpack_update.update(
                DownloadManager(threads=1), inst, target_version_id="ver-2")

        # 旧管理文件删除
        self.assertFalse((inst.path / "mods" / "a.jar").exists())
        # 未修改 override 删除（新包会重新提供）
        self.assertFalse((inst.path / "config" / "pack.cfg").exists())
        # 用户改过的 override 留在原位并备份
        self.assertTrue((inst.path / "config" / "user.cfg").is_file())
        backups = list((inst.path / "backups").glob("modpack-update-*/config/user.cfg"))
        self.assertEqual(len(backups), 1)
        # 用户自己的文件不动
        self.assertTrue((inst.path / "config" / "mine.cfg").is_file())
        # 新版本安装 + meta 更新
        self.assertTrue((inst.path / "mods" / "c.jar").is_file())
        self.assertEqual(meta["version"], "1.1.0")
        self.assertEqual(meta["source_version_id"], "ver-2")
        stored = inst.meta()["modpack"]
        self.assertEqual(stored["version"], "1.1.0")
        self.assertTrue(stored.get("last_update_backup"))

    def test_update_refuses_legacy(self):
        inst = self.make_instance("upd2")
        inst.set_meta("modpack", {"name": "Old", "source": "modrinth"})
        with self.assertRaises(ModpackError):
            modpack_update.update(DownloadManager(threads=1), inst)


if __name__ == "__main__":
    unittest.main()
