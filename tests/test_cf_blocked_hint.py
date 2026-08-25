# -*- coding: utf-8 -*-
"""CurseForge 禁止分发的 Mod：整合包安装失败时给手动下载指引。"""
from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from mclauncher import modpack
from mclauncher.downloader import DownloadError, DownloadManager
from mclauncher.instances import Instance
from mclauncher.modpack import ModpackError, cf_manual_download_hint


class TestHintText(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mods = Path(self.tmp.name) / "mods"
        self.mods.mkdir()
        self.addCleanup(self.tmp.cleanup)

    def _entry(self, name, pid=100, fid=2000, blocked=False, present=False):
        dest = self.mods / name
        if present:
            dest.write_bytes(b"PK ok")
        return {"dest": dest, "project_id": pid, "file_id": fid,
                "name": name, "blocked": blocked}

    def test_lists_missing_with_links(self):
        entries = [
            self._entry("jei.jar", pid=238222, fid=5000, blocked=True),
            self._entry("ok.jar", present=True),
            self._entry("fail.jar", pid=310806, fid=6000),
        ]
        msg = cf_manual_download_hint(self.mods, entries, DownloadError("x"))
        self.assertIn("2 个 Mod 下载失败", msg)
        self.assertIn("1 个作者禁止第三方分发", msg)
        self.assertIn("https://www.curseforge.com/projects/238222", msg)
        self.assertIn("[需手动] jei.jar", msg)
        self.assertNotIn("ok.jar", msg)
        self.assertIn(str(self.mods), msg)
        self.assertIn("自动跳过", msg)

    def test_all_present_falls_back_to_error(self):
        entries = [self._entry("a.jar", present=True)]
        msg = cf_manual_download_hint(self.mods, entries, DownloadError("boom"))
        self.assertEqual(msg, "boom")

    def test_truncates_long_list(self):
        entries = [self._entry(f"m{i}.jar", pid=i, fid=i) for i in range(20)]
        msg = cf_manual_download_hint(self.mods, entries, DownloadError("x"))
        self.assertIn("以及另外 8 个", msg)


class TestInstallSurfacesHint(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        patcher = mock.patch(
            "mclauncher.instances.get_instance_path",
            side_effect=lambda name: self.root / "instances" / name)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def _cf_zip(self) -> Path:
        manifest = {
            "manifestType": "minecraftModpack",
            "name": "BlockedPack",
            "version": "1.0",
            "minecraft": {"version": "1.20.1",
                          "modLoaders": [{"id": "forge-47.2.0", "primary": True}]},
            "files": [
                {"projectID": 238222, "fileID": 5000, "required": True},
            ],
            "overrides": "overrides",
        }
        p = self.root / "pack.zip"
        with zipfile.ZipFile(p, "w") as z:
            z.writestr("manifest.json", json.dumps(manifest))
        return p

    def test_blocked_mod_error_contains_guidance(self):
        inst = Instance("cfb")
        inst.create()
        dm = DownloadManager(threads=1)
        file_meta = {5000: {"fileName": "jei.jar", "downloadUrl": None,
                            "hashes": [], "fileLength": 10}}
        with mock.patch.object(modpack, "Installer"), \
                mock.patch.object(modpack, "install_loader", return_value="forge-vid"), \
                mock.patch.object(modpack, "_resolve_pack_minecraft",
                                  side_effect=lambda dm_, d, p=None: d), \
                mock.patch("mclauncher.mods.cf_files_by_ids", return_value=file_meta), \
                mock.patch.object(dm, "download_all",
                                  side_effect=DownloadError("jei.jar: HTTP 403")):
            with self.assertRaises(ModpackError) as ctx:
                modpack.install_cf_zip(dm, str(self._cf_zip()), inst)
        msg = str(ctx.exception)
        self.assertIn("禁止第三方分发", msg)
        self.assertIn("https://www.curseforge.com/projects/238222", msg)
        self.assertIn("jei.jar", msg)
        self.assertIn("HTTP 403", msg)


if __name__ == "__main__":
    unittest.main()
