# -*- coding: utf-8 -*-
"""MCBBS 整合包 fileApi 在线更新测试（不联网）。

对标 HMCL：检查 {fileApi}/mcbbs.packmeta 的 version，更新时增量同步
addFile（hash 未变自动跳过）、重新下载 curse 条目、清理旧版本残留。
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mclauncher import modpack  # noqa: E402
from mclauncher.modpack import ModpackError  # noqa: E402


class _FakeInstance:
    def __init__(self, base: Path, name="测试实例", pack_meta=None):
        self.name = name
        self.path = base / name
        self.meta_store = {}
        if pack_meta is not None:
            self.meta_store["modpack"] = pack_meta
        self.ensure_standard_dirs()

    def create(self):
        self.ensure_standard_dirs()

    def ensure_standard_dirs(self):
        for d in ("versions", "libraries", "assets", "mods"):
            (self.path / d).mkdir(parents=True, exist_ok=True)

    def versions_dir(self):
        return self.path / "versions"

    def meta(self):
        return dict(self.meta_store)

    def set_meta(self, key, value):
        self.meta_store[key] = value


def _pack_meta(version="1.0", file_api="https://packs.example.com/my-pack"):
    meta = {
        "name": "测试 MCBBS 包", "version": version, "mc_version": "1.12.2",
        "loader": "forge-14.23.5.2854", "source": "mcbbs", "instance": "测试实例",
    }
    if file_api:
        meta["file_api"] = file_api
    return meta


def _remote_manifest(version="1.1", files=None):
    return {
        "manifestType": "minecraftModpack",
        "manifestVersion": 2,
        "name": "测试 MCBBS 包",
        "version": version,
        "fileApi": "https://packs.example.com/my-pack",
        "addons": [{"id": "game", "version": "1.12.2"},
                   {"id": "forge", "version": "14.23.5.2854"}],
        "files": files if files is not None else [
            {"type": "addFile", "path": "config/pack.cfg", "hash": "ab" * 20},
            {"type": "curse", "projectID": 238222, "fileID": 2831681},
        ],
        "launchInfo": {"minMemory": 3072},
    }


class _FakeDM(mock.MagicMock):
    """fetch_json 返回预置清单；download_all 把 dest 写出来并记录 URL。"""

    def __init__(self, *args, manifest=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._manifest = manifest or _remote_manifest()
        self.fetched_urls = []
        self.downloaded = []

    def fetch_json(self, url, timeout=60, **kwargs):
        self.fetched_urls.append(url)
        return self._manifest

    def download_all(self, tasks, message=""):
        for task in tasks:
            dest = Path(task[1])
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"data")
            urls = task[0] if isinstance(task[0], (list, tuple)) else [task[0]]
            self.downloaded.append(urls[0])
        return True


class CheckUpdateTests(unittest.TestCase):
    def test_newer_version_reports_update(self):
        with tempfile.TemporaryDirectory() as td:
            inst = _FakeInstance(Path(td), pack_meta=_pack_meta(version="1.0"))
            dm = _FakeDM(manifest=_remote_manifest(version="1.1"))
            info = modpack.check_modpack_update(dm, inst)
            self.assertEqual(info["source"], "mcbbs")
            self.assertTrue(info["update"])
            self.assertEqual(info["current"], "1.0")
            self.assertEqual(info["latest"], "1.1")
            self.assertEqual(info["mc_versions"], ["1.12.2"])
            self.assertEqual(dm.fetched_urls,
                             ["https://packs.example.com/my-pack/mcbbs.packmeta"])

    def test_same_version_no_update(self):
        with tempfile.TemporaryDirectory() as td:
            inst = _FakeInstance(Path(td), pack_meta=_pack_meta(version="1.1"))
            dm = _FakeDM(manifest=_remote_manifest(version="1.1"))
            info = modpack.check_modpack_update(dm, inst)
            self.assertFalse(info["update"])

    def test_missing_file_api_raises(self):
        with tempfile.TemporaryDirectory() as td:
            inst = _FakeInstance(Path(td), pack_meta=_pack_meta(file_api=None))
            with self.assertRaises(ModpackError) as ctx:
                modpack.check_modpack_update(_FakeDM(), inst)
            self.assertIn("fileApi", str(ctx.exception))


class UpdateTests(unittest.TestCase):
    def _run(self, inst, dm):
        with mock.patch.object(modpack, "Installer"), \
                mock.patch.object(modpack, "install_loader",
                                  return_value="1.12.2-forge-14.23.5.2854"), \
                mock.patch.object(modpack, "_resolve_pack_minecraft",
                                  side_effect=lambda _dm, v, _p=None: v), \
                mock.patch("mclauncher.mods.cf_files_by_ids",
                           return_value={2831681: {"fileName": "jei.jar"}}), \
                mock.patch("mclauncher.mods.cf_mod_download_urls",
                           side_effect=lambda pid, fid, **kw: [f"https://cdn/{pid}/{fid}"]):
            return modpack.update_modpack(dm, inst)

    def test_full_update_syncs_files_and_cleans_stale(self):
        with tempfile.TemporaryDirectory() as td:
            inst = _FakeInstance(Path(td), pack_meta=_pack_meta(version="1.0"))
            # 旧版本清单里有个新版不再包含的 mod，应被清理
            modpack.write_pack_files(inst, ["mods/old.jar", "config/pack.cfg"])
            (inst.path / "mods" / "old.jar").write_bytes(b"old")
            dm = _FakeDM(manifest=_remote_manifest(version="1.1"))
            out = self._run(inst, dm)
            self.assertTrue(out["updated"])
            self.assertEqual(out["from"], "1.0")
            self.assertEqual(out["to"], "1.1")
            # addFile 从 fileApi/overrides/ 同步、curse 文件已下载
            self.assertTrue((inst.path / "config" / "pack.cfg").is_file())
            self.assertTrue((inst.path / "mods" / "jei.jar").is_file())
            self.assertIn("https://packs.example.com/my-pack/overrides/config/pack.cfg",
                          dm.downloaded)
            # 旧版本残留清理
            self.assertFalse((inst.path / "mods" / "old.jar").exists())
            self.assertIn("mods/old.jar", out["removed"])
            # pack_meta 与文件清单更新
            self.assertEqual(inst.meta_store["modpack"]["version"], "1.1")
            self.assertEqual(inst.meta_store["modpack"]["file_api"],
                             "https://packs.example.com/my-pack")
            files = set(modpack.read_pack_files(inst))
            self.assertEqual(files, {"mods/jei.jar", "config/pack.cfg"})
            # launchInfo 重新落进版本设置
            vs_file = inst.versions_dir() / "1.12.2-forge-14.23.5.2854" / "pymcl.json"
            vs = json.loads(vs_file.read_text(encoding="utf-8"))
            self.assertEqual(vs["memory_mb"], 3072)

    def test_already_latest_skips(self):
        with tempfile.TemporaryDirectory() as td:
            inst = _FakeInstance(Path(td), pack_meta=_pack_meta(version="1.1"))
            dm = _FakeDM(manifest=_remote_manifest(version="1.1"))
            out = self._run(inst, dm)
            self.assertFalse(out["updated"])

    def test_path_traversal_entry_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            inst = _FakeInstance(Path(td), pack_meta=_pack_meta(version="1.0"))
            manifest = _remote_manifest(version="1.1", files=[
                {"type": "addFile", "path": "../evil.txt", "hash": "cd" * 20},
                {"type": "addFile", "path": "config/ok.cfg"},
            ])
            dm = _FakeDM(manifest=manifest)
            self._run(inst, dm)
            self.assertFalse((inst.path.parent / "evil.txt").exists())
            self.assertTrue((inst.path / "config" / "ok.cfg").is_file())


if __name__ == "__main__":
    unittest.main()
