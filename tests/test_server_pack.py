# -*- coding: utf-8 -*-
"""HMCL 服务器整合包（server-manifest.json）测试（不联网）。

对标 HMCL ServerModpack*Task：
- 本地 zip（server-manifest.json + overrides/）安装；
- 远程按 fileApi 安装（文件从 {fileApi}/overrides/{path} 或清单直链下载）；
- 在线检查更新 + 增量同步：hash 变了且本地未被用户改过才重下、用户改过
  的保留、被禁用（.disabled）的 mod 不动、远端删掉的文件本地删除。
"""
import hashlib
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mclauncher import modpack  # noqa: E402
from mclauncher.import_files import classify_file  # noqa: E402
from mclauncher.modpack import (  # noqa: E402
    ModpackError,
    _derive_file_api,
    _server_root,
    parse_server_files,
)

FILE_API = "https://packs.example.com/my-server"


def _sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def _addon(aid, version):
    return {"id": aid, "version": version}


def _manifest(version="1.0", files=None, addons=None, **extra):
    mf = {
        "name": "测试服务器包",
        "author": "服主",
        "version": version,
        "description": "",
        "fileApi": FILE_API,
        "addons": addons if addons is not None else [
            _addon("game", "1.20.1"), _addon("forge", "47.2.0"),
        ],
        "files": files if files is not None else [
            {"path": "mods/a.jar", "hash": _sha1(b"a-v1")},
            {"path": "config/pack.cfg", "hash": _sha1(b"cfg-v1")},
        ],
    }
    mf.update(extra)
    return mf


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


class _FakeDM(mock.MagicMock):
    """fetch_json 返回预置清单；download_all 把 dest 写出来并记录首选 URL。"""

    def __init__(self, *args, manifest=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._manifest = manifest
        self.fetched_urls = []
        self.downloaded = []

    def fetch_json(self, url, timeout=60, **kwargs):
        self.fetched_urls.append(url)
        if self._manifest is None:
            raise RuntimeError("测试没有预置清单")
        return self._manifest

    def download_all(self, tasks, message=""):
        for task in tasks:
            dest = Path(task[1])
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"downloaded")
            urls = task[0] if isinstance(task[0], (list, tuple)) else [task[0]]
            self.downloaded.append(urls[0])
        return True


def _install_mocks():
    return (
        mock.patch.object(modpack, "Installer"),
        mock.patch.object(modpack, "install_loader",
                          return_value="1.20.1-forge-47.2.0"),
        mock.patch.object(modpack, "_resolve_pack_minecraft",
                          side_effect=lambda _dm, v, _p=None: v),
    )


class ParseFilesTests(unittest.TestCase):
    def test_parse_and_traversal_guard(self):
        mf = {"files": [
            {"path": "mods/a.jar", "hash": "AB" * 20},
            {"path": "../evil.txt", "hash": "cd" * 20},
            {"path": "config\\win.cfg"},
            {"path": "mods/b.jar", "hash": "ef" * 20,
             "downloadURL": "https://cdn.modrinth.com/b.jar"},
            "junk",
            {"hash": "no-path"},
        ]}
        out = parse_server_files(mf)
        self.assertEqual([f["path"] for f in out],
                         ["mods/a.jar", "config/win.cfg", "mods/b.jar"])
        self.assertEqual(out[0]["hash"], "ab" * 20)   # hash 统一小写
        self.assertEqual(out[2]["url"], "https://cdn.modrinth.com/b.jar")

    def test_derive_file_api(self):
        self.assertEqual(_derive_file_api({"fileApi": FILE_API + "/"}), FILE_API)
        self.assertEqual(
            _derive_file_api({}, f"{FILE_API}/server-manifest.json"), FILE_API)
        self.assertEqual(_derive_file_api({}, FILE_API + "/"), FILE_API)
        self.assertEqual(_derive_file_api({}), "")


class ServerRootTests(unittest.TestCase):
    def test_root_level(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "server-manifest.json").write_text("{}", encoding="utf-8")
            self.assertEqual(_server_root(Path(td)), Path(td))

    def test_nested_one_level(self):
        with tempfile.TemporaryDirectory() as td:
            sub = Path(td) / "MyPack"
            sub.mkdir()
            (sub / "server-manifest.json").write_text("{}", encoding="utf-8")
            self.assertEqual(_server_root(Path(td)), sub)

    def test_absent(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "manifest.json").write_text("{}", encoding="utf-8")
            self.assertIsNone(_server_root(Path(td)))


def _build_zip(td: Path, mf: dict, folder="", overrides=None) -> Path:
    zpath = td / "server-pack.zip"
    prefix = f"{folder}/" if folder else ""
    with zipfile.ZipFile(zpath, "w") as z:
        z.writestr(f"{prefix}server-manifest.json", json.dumps(mf, ensure_ascii=False))
        for rel, data in (overrides or {}).items():
            z.writestr(f"{prefix}overrides/{rel}", data)
    return zpath


class LocalInstallTests(unittest.TestCase):
    def _run(self, zpath: Path, base: Path):
        inst = _FakeInstance(base)
        dm = _FakeDM()
        p1, p2, p3 = _install_mocks()
        with p1 as installer_cls, p2 as fake_loader, p3:
            meta = modpack.install_cf_zip(dm, str(zpath), inst)
        return inst, dm, meta, installer_cls, fake_loader

    def test_full_pack_with_overrides(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            mf = _manifest(files=[
                {"path": "mods/a.jar", "hash": _sha1(b"a-v1")},
                {"path": "config/pack.cfg", "hash": _sha1(b"cfg-v1")},
                # 包里没带、清单声明了直链的文件（新 HMCL 导出方式）
                {"path": "mods/b.jar", "hash": _sha1(b"b-v1"),
                 "downloadURL": "https://cdn.modrinth.com/b.jar"},
            ])
            zpath = _build_zip(td, mf, overrides={
                "mods/a.jar": "a-v1", "config/pack.cfg": "cfg-v1"})
            inst, dm, meta, installer_cls, fake_loader = self._run(zpath, td)

            self.assertEqual(meta["source"], "server")
            self.assertEqual(meta["name"], "测试服务器包")
            self.assertEqual(meta["version"], "1.0")
            self.assertEqual(meta["mc_version"], "1.20.1")
            self.assertEqual(meta["loader"], "forge-47.2.0")
            self.assertEqual(meta["author"], "服主")
            self.assertEqual(meta["file_api"], FILE_API)
            installer_cls.return_value.install_version.assert_called_once()
            self.assertEqual(fake_loader.call_args[0][1:],
                             ("forge", "47.2.0", "1.20.1"))
            # overrides 落地、缺的文件按清单补下（直链优先）
            self.assertTrue((inst.path / "mods" / "a.jar").is_file())
            self.assertTrue((inst.path / "config" / "pack.cfg").is_file())
            self.assertTrue((inst.path / "mods" / "b.jar").is_file())
            self.assertEqual(dm.downloaded, ["https://cdn.modrinth.com/b.jar"])
            self.assertEqual(inst.meta_store["mc_version"], "1.20.1-forge-47.2.0")
            files = set(modpack.read_pack_files(inst))
            self.assertEqual(files, {"mods/a.jar", "config/pack.cfg", "mods/b.jar"})
            # hash 清单写盘，供增量更新比对
            hashes = modpack.read_server_hashes(inst)
            self.assertEqual(hashes["mods/a.jar"], _sha1(b"a-v1"))
            self.assertEqual(hashes["mods/b.jar"], _sha1(b"b-v1"))

    def test_nested_folder(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            zpath = _build_zip(td, _manifest(files=[]), folder="我的包",
                               overrides={"config/pack.cfg": "k=v"})
            inst, _dm, meta, _cls, _loader = self._run(zpath, td)
            self.assertEqual(meta["source"], "server")
            self.assertTrue((inst.path / "config" / "pack.cfg").is_file())

    def test_vanilla_pack_skips_loader(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            zpath = _build_zip(td, _manifest(files=[], addons=[_addon("game", "1.18.2")]))
            inst, _dm, meta, _cls, fake_loader = self._run(zpath, td)
            self.assertEqual(meta["loader"], "vanilla")
            fake_loader.assert_not_called()
            self.assertEqual(inst.meta_store["mc_version"], "1.18.2")

    def test_missing_game_addon_raises(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            zpath = _build_zip(td, _manifest(addons=[_addon("forge", "47.2.0")]))
            with self.assertRaises(ModpackError) as ctx:
                self._run(zpath, td)
            self.assertIn("game", str(ctx.exception))

    def test_local_manifest_json_routes_to_remote_install(self):
        """链接下载回来的不是 zip 而是 server-manifest.json 时按远程装。"""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            mf = _manifest(files=[{"path": "mods/a.jar", "hash": _sha1(b"a-v1")}])
            jpath = td / "server-manifest.json"
            jpath.write_text(json.dumps(mf, ensure_ascii=False), encoding="utf-8")
            inst = _FakeInstance(td)
            dm = _FakeDM()
            p1, p2, p3 = _install_mocks()
            with p1, p2, p3:
                meta = modpack.install_cf_zip(dm, str(jpath), inst)
            self.assertEqual(meta["source"], "server")
            self.assertTrue((inst.path / "mods" / "a.jar").is_file())
            self.assertEqual(dm.downloaded,
                             [f"{FILE_API}/overrides/mods/a.jar"])


class RemoteInstallTests(unittest.TestCase):
    def _run(self, base: Path, manifest, url=f"{FILE_API}/server-manifest.json"):
        inst = _FakeInstance(base)
        dm = _FakeDM(manifest=manifest)
        p1, p2, p3 = _install_mocks()
        with p1, p2, p3:
            meta = modpack.install_server_pack_url(dm, url, inst)
        return inst, dm, meta

    def test_remote_install_downloads_everything(self):
        with tempfile.TemporaryDirectory() as td:
            inst, dm, meta = self._run(Path(td), _manifest())
            self.assertEqual(dm.fetched_urls, [f"{FILE_API}/server-manifest.json"])
            self.assertEqual(meta["source"], "server")
            self.assertEqual(meta["file_api"], FILE_API)
            self.assertTrue((inst.path / "mods" / "a.jar").is_file())
            self.assertTrue((inst.path / "config" / "pack.cfg").is_file())
            self.assertIn(f"{FILE_API}/overrides/mods/a.jar", dm.downloaded)
            self.assertIn(f"{FILE_API}/overrides/config/pack.cfg", dm.downloaded)

    def test_base_url_without_manifest_suffix(self):
        """允许只给基址：自动拼 /server-manifest.json（HMCL 用户习惯直接贴清单直链，两者都认）。"""
        with tempfile.TemporaryDirectory() as td:
            mf = _manifest()
            mf.pop("fileApi")
            inst, dm, meta = self._run(Path(td), mf, url=FILE_API)
            self.assertEqual(dm.fetched_urls, [f"{FILE_API}/server-manifest.json"])
            # 清单没写 fileApi 时按清单地址推导
            self.assertEqual(meta["file_api"], FILE_API)
            self.assertTrue((inst.path / "mods" / "a.jar").is_file())

    def test_no_file_api_and_no_urls_raises(self):
        with tempfile.TemporaryDirectory() as td:
            mf = _manifest()
            mf.pop("fileApi")
            inst = _FakeInstance(Path(td))
            dm = _FakeDM(manifest=mf)
            p1, p2, p3 = _install_mocks()
            with p1, p2, p3, self.assertRaises(ModpackError) as ctx:
                modpack.install_server_pack_manifest(dm, mf, inst)
            self.assertIn("fileApi", str(ctx.exception))


def _pack_meta(version="1.0", file_api=FILE_API):
    meta = {
        "name": "测试服务器包", "version": version, "mc_version": "1.20.1",
        "loader": "forge-47.2.0", "source": "server", "instance": "测试实例",
    }
    if file_api:
        meta["file_api"] = file_api
    return meta


class CheckUpdateTests(unittest.TestCase):
    def test_newer_version_reports_update(self):
        with tempfile.TemporaryDirectory() as td:
            inst = _FakeInstance(Path(td), pack_meta=_pack_meta(version="1.0"))
            dm = _FakeDM(manifest=_manifest(version="1.1"))
            info = modpack.check_modpack_update(dm, inst)
            self.assertEqual(info["source"], "server")
            self.assertTrue(info["update"])
            self.assertEqual(info["current"], "1.0")
            self.assertEqual(info["latest"], "1.1")
            self.assertEqual(info["mc_versions"], ["1.20.1"])
            self.assertEqual(dm.fetched_urls, [f"{FILE_API}/server-manifest.json"])

    def test_same_version_no_update(self):
        with tempfile.TemporaryDirectory() as td:
            inst = _FakeInstance(Path(td), pack_meta=_pack_meta(version="1.1"))
            dm = _FakeDM(manifest=_manifest(version="1.1"))
            self.assertFalse(modpack.check_modpack_update(dm, inst)["update"])

    def test_missing_file_api_raises(self):
        with tempfile.TemporaryDirectory() as td:
            inst = _FakeInstance(Path(td), pack_meta=_pack_meta(file_api=None))
            with self.assertRaises(ModpackError) as ctx:
                modpack.check_modpack_update(_FakeDM(), inst)
            self.assertIn("fileApi", str(ctx.exception))


class UpdateTests(unittest.TestCase):
    """增量更新语义（HMCL ServerModpackCompletionTask 对齐）。"""

    def _setup(self, td: Path):
        """装好 1.0 版：mods/a.jar + config/pack.cfg，hash 清单齐全。"""
        inst = _FakeInstance(td, pack_meta=_pack_meta(version="1.0"))
        (inst.path / "mods" / "a.jar").write_bytes(b"a-v1")
        (inst.path / "config").mkdir(parents=True, exist_ok=True)
        (inst.path / "config" / "pack.cfg").write_bytes(b"cfg-v1")
        modpack.write_pack_files(inst, ["mods/a.jar", "config/pack.cfg"])
        modpack.write_server_hashes(inst, [
            {"path": "mods/a.jar", "hash": _sha1(b"a-v1")},
            {"path": "config/pack.cfg", "hash": _sha1(b"cfg-v1")},
        ])
        return inst

    def _run(self, inst, manifest):
        dm = _FakeDM(manifest=manifest)
        p1, p2, p3 = _install_mocks()
        with p1, p2, p3:
            out = modpack.update_modpack(dm, inst)
        return dm, out

    def test_changed_file_redownloaded_new_file_added_stale_removed(self):
        with tempfile.TemporaryDirectory() as td:
            inst = self._setup(Path(td))
            remote = _manifest(version="1.1", files=[
                {"path": "mods/a.jar", "hash": _sha1(b"a-v2")},      # 远端变了
                {"path": "mods/new.jar", "hash": _sha1(b"new-v1")},  # 新增
                # config/pack.cfg 被远端删除
            ])
            dm, out = self._run(inst, remote)
            self.assertTrue(out["updated"])
            self.assertEqual(out["from"], "1.0")
            self.assertEqual(out["to"], "1.1")
            # a.jar 本地未被改过 -> 重下；new.jar 新增 -> 下载
            self.assertIn(f"{FILE_API}/overrides/mods/a.jar", dm.downloaded)
            self.assertIn(f"{FILE_API}/overrides/mods/new.jar", dm.downloaded)
            # 远端删掉的 config/pack.cfg 本地删除
            self.assertFalse((inst.path / "config" / "pack.cfg").exists())
            # 元数据与 hash 清单滚动到 1.1
            self.assertEqual(inst.meta_store["modpack"]["version"], "1.1")
            hashes = modpack.read_server_hashes(inst)
            self.assertEqual(set(hashes), {"mods/a.jar", "mods/new.jar"})

    def test_unchanged_remote_keeps_user_modified_file(self):
        with tempfile.TemporaryDirectory() as td:
            inst = self._setup(Path(td))
            (inst.path / "config" / "pack.cfg").write_bytes(b"user-edited")
            remote = _manifest(version="1.1", files=[
                {"path": "mods/a.jar", "hash": _sha1(b"a-v1")},
                {"path": "config/pack.cfg", "hash": _sha1(b"cfg-v1")},
            ])
            dm, _out = self._run(inst, remote)
            self.assertEqual(dm.downloaded, [])
            self.assertEqual((inst.path / "config" / "pack.cfg").read_bytes(),
                             b"user-edited")

    def test_changed_remote_keeps_user_modified_file(self):
        """远端变了但用户也改过：保留用户版本（HMCL 同款）。"""
        with tempfile.TemporaryDirectory() as td:
            inst = self._setup(Path(td))
            (inst.path / "config" / "pack.cfg").write_bytes(b"user-edited")
            remote = _manifest(version="1.1", files=[
                {"path": "mods/a.jar", "hash": _sha1(b"a-v1")},
                {"path": "config/pack.cfg", "hash": _sha1(b"cfg-v2")},
            ])
            dm, _out = self._run(inst, remote)
            self.assertEqual(dm.downloaded, [])
            self.assertEqual((inst.path / "config" / "pack.cfg").read_bytes(),
                             b"user-edited")

    def test_disabled_mod_not_redownloaded(self):
        with tempfile.TemporaryDirectory() as td:
            inst = self._setup(Path(td))
            a = inst.path / "mods" / "a.jar"
            a.rename(a.with_name("a.jar.disabled"))
            remote = _manifest(version="1.1", files=[
                {"path": "mods/a.jar", "hash": _sha1(b"a-v2")},
                {"path": "config/pack.cfg", "hash": _sha1(b"cfg-v1")},
            ])
            dm, _out = self._run(inst, remote)
            self.assertEqual(dm.downloaded, [])
            self.assertFalse((inst.path / "mods" / "a.jar").exists())

    def test_missing_file_redownloaded(self):
        with tempfile.TemporaryDirectory() as td:
            inst = self._setup(Path(td))
            (inst.path / "mods" / "a.jar").unlink()
            remote = _manifest(version="1.1")
            dm, _out = self._run(inst, remote)
            self.assertIn(f"{FILE_API}/overrides/mods/a.jar", dm.downloaded)
            self.assertTrue((inst.path / "mods" / "a.jar").is_file())


class ClassifyTests(unittest.TestCase):
    def test_server_zip_is_modpack(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            zpath = _build_zip(td, _manifest(), overrides={"config/pack.cfg": "k=v"})
            self.assertEqual(classify_file(zpath)["kind"], "modpack")

    def test_nested_server_zip_is_modpack(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            zpath = _build_zip(td, _manifest(), folder="我的包")
            self.assertEqual(classify_file(zpath)["kind"], "modpack")


if __name__ == "__main__":
    unittest.main()
