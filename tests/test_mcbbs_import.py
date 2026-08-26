# -*- coding: utf-8 -*-
"""MCBBS 规范整合包（mcbbs.packmeta）导入测试（不联网）。

对标 HMCL / PCL2：addons 装原版 + 加载器，files 的 curse 条目走
CurseForge 下载、addFile 随 overrides 落地，launchInfo 折算成版本设置。
"""
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
    _mcbbs_root,
    parse_mcbbs_addons,
    split_mcbbs_files,
)


def _addon(aid, version):
    return {"id": aid, "version": version}


class ParseAddonsTests(unittest.TestCase):
    def test_forge_pack(self):
        mf = {"addons": [_addon("game", "1.12.2"), _addon("forge", "14.23.5.2854")]}
        out = parse_mcbbs_addons(mf)
        self.assertEqual(out["mc"], "1.12.2")
        self.assertEqual(out["loader"], "forge")
        self.assertEqual(out["loader_version"], "14.23.5.2854")
        self.assertEqual(out["extras"], [])

    def test_loader_aliases(self):
        for aid, expect in (
            ("forge", "forge"),
            ("neoforge", "neoforge"),
            ("neoforged", "neoforge"),
            ("fabric", "fabric-loader"),
            ("quilt", "quilt-loader"),
        ):
            mf = {"addons": [_addon("game", "1.20.1"), _addon(aid, "1.0")]}
            self.assertEqual(parse_mcbbs_addons(mf)["loader"], expect, aid)

    def test_vanilla_with_extras(self):
        mf = {"addons": [
            _addon("game", "1.8.9"),
            _addon("optifine", "HD_U_L5"),
            _addon("something-else", "9"),
        ]}
        out = parse_mcbbs_addons(mf)
        self.assertEqual(out["mc"], "1.8.9")
        self.assertIsNone(out["loader"])
        self.assertEqual(out["extras"],
                         [("optifine", "HD_U_L5"), ("something-else", "9")])

    def test_empty(self):
        out = parse_mcbbs_addons({})
        self.assertEqual(out["mc"], "")
        self.assertIsNone(out["loader"])


class SplitFilesTests(unittest.TestCase):
    def test_split(self):
        mf = {"files": [
            {"type": "curse", "projectID": 238222, "fileID": 2831681},
            {"type": "curseFile", "projectID": 1, "fileID": 2},
            {"projectID": 3, "fileID": 4},                        # 老包不写 type
            {"type": "addFile", "path": "config/a.cfg", "hash": "x"},
            {"type": "addFile", "path": "mods/local.jar"},
            "junk",
            {"type": "curse"},                                    # 缺 id，丢弃
        ]}
        curse, add = split_mcbbs_files(mf)
        self.assertEqual([f["projectID"] for f in curse], [238222, 1, 3])
        self.assertEqual([f["path"] for f in add], ["config/a.cfg", "mods/local.jar"])

    def test_empty(self):
        self.assertEqual(split_mcbbs_files({}), ([], []))


class McbbsRootTests(unittest.TestCase):
    def test_root_level(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "mcbbs.packmeta").write_text("{}", encoding="utf-8")
            self.assertEqual(_mcbbs_root(Path(td)), Path(td))

    def test_nested_one_level(self):
        with tempfile.TemporaryDirectory() as td:
            sub = Path(td) / "MyPack"
            sub.mkdir()
            (sub / "mcbbs.packmeta").write_text("{}", encoding="utf-8")
            self.assertEqual(_mcbbs_root(Path(td)), sub)

    def test_absent(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "manifest.json").write_text("{}", encoding="utf-8")
            self.assertIsNone(_mcbbs_root(Path(td)))


class _FakeInstance:
    def __init__(self, base: Path, name="测试实例"):
        self.name = name
        self.path = base / name
        self.meta_store = {}

    def create(self):
        self.ensure_standard_dirs()

    def ensure_standard_dirs(self):
        for d in ("versions", "libraries", "assets", "mods"):
            (self.path / d).mkdir(parents=True, exist_ok=True)

    def versions_dir(self):
        return self.path / "versions"

    def set_meta(self, key, value):
        self.meta_store[key] = value


def _packmeta(addons=None, files=None, launch_info=None, **extra):
    mf = {
        "manifestType": "minecraftModpack",
        "manifestVersion": 2,
        "name": "测试 MCBBS 包",
        "version": "1.1",
        "author": "作者",
        "addons": addons if addons is not None else [
            _addon("game", "1.12.2"), _addon("forge", "14.23.5.2854"),
        ],
    }
    if files is not None:
        mf["files"] = files
    if launch_info is not None:
        mf["launchInfo"] = launch_info
    mf.update(extra)
    return mf


def _build_mcbbs_zip(td: Path, mf: dict, folder="", overrides=True,
                     also_curse_manifest=False) -> Path:
    zpath = td / "mcbbs-pack.zip"
    prefix = f"{folder}/" if folder else ""
    with zipfile.ZipFile(zpath, "w") as z:
        z.writestr(f"{prefix}mcbbs.packmeta", json.dumps(mf, ensure_ascii=False))
        if overrides:
            z.writestr(f"{prefix}overrides/config/pack.cfg", "k=v")
            z.writestr(f"{prefix}overrides/mods/local.jar", "jar-bytes")
        if also_curse_manifest:
            z.writestr(f"{prefix}manifest.json", json.dumps({
                "manifestType": "minecraftModpack",
                "minecraft": {"version": "1.12.2"},
            }))
    return zpath


class _FakeDM(mock.MagicMock):
    """download_all 假实现：把每个 dest 文件写出来（模拟下载成功）。"""

    def download_all(self, tasks, message=""):
        for task in tasks:
            dest = Path(task[1])
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"jar")
        return True


class InstallDispatchTests(unittest.TestCase):
    def _run(self, zpath: Path, base: Path, loader_vid="1.12.2-forge-14.23.5.2854",
             cf_meta=None):
        inst = _FakeInstance(base)
        dm = _FakeDM()
        with mock.patch.object(modpack, "Installer") as fake_installer_cls, \
                mock.patch.object(modpack, "install_loader",
                                  return_value=loader_vid) as fake_loader, \
                mock.patch.object(modpack, "_resolve_pack_minecraft",
                                  side_effect=lambda _dm, v, _p=None: v), \
                mock.patch("mclauncher.mods.cf_files_by_ids",
                           return_value=cf_meta or {}), \
                mock.patch("mclauncher.mods.cf_mod_download_urls",
                           side_effect=lambda pid, fid, **kw: [f"https://cdn/{pid}/{fid}"]):
            meta = modpack.install_cf_zip(dm, str(zpath), inst)
        return inst, meta, fake_installer_cls, fake_loader

    def test_full_forge_pack(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            mf = _packmeta(
                files=[
                    {"type": "curse", "projectID": 238222, "fileID": 2831681},
                    {"type": "addFile", "path": "config/pack.cfg", "hash": "x"},
                ],
                launch_info={"minMemory": 4096,
                             "javaArgument": ["-XX:+UseG1GC"],
                             "launchArgument": ["--demo"]},
                fileApi="https://packs.example.com/my-pack",
            )
            zpath = _build_mcbbs_zip(td, mf)
            inst, meta, installer_cls, fake_loader = self._run(
                zpath, td,
                cf_meta={2831681: {"fileName": "jei.jar", "downloadUrl": "https://cdn/jei.jar",
                                   "hashes": [{"algo": 1, "value": "ab" * 20}],
                                   "fileLength": 3}})
            self.assertEqual(meta["source"], "mcbbs")
            self.assertEqual(meta["name"], "测试 MCBBS 包")
            self.assertEqual(meta["version"], "1.1")
            self.assertEqual(meta["mc_version"], "1.12.2")
            self.assertEqual(meta["loader"], "forge-14.23.5.2854")
            self.assertEqual(meta["author"], "作者")
            self.assertEqual(meta["file_api"], "https://packs.example.com/my-pack")
            installer = installer_cls.return_value
            installer.install_version.assert_called_once()
            self.assertEqual(installer.install_version.call_args[0][0], "1.12.2")
            self.assertEqual(fake_loader.call_args[0][1:],
                             ("forge", "14.23.5.2854", "1.12.2"))
            # curse 文件已下载、overrides 已拷贝
            self.assertTrue((inst.path / "mods" / "jei.jar").is_file())
            self.assertTrue((inst.path / "config" / "pack.cfg").is_file())
            self.assertTrue((inst.path / "mods" / "local.jar").is_file())
            self.assertEqual(inst.meta_store["mc_version"], "1.12.2-forge-14.23.5.2854")
            # 文件清单记录了下载文件 + overrides
            files = set(modpack.read_pack_files(inst))
            self.assertIn("mods/jei.jar", files)
            self.assertIn("config/pack.cfg", files)
            self.assertIn("mods/local.jar", files)
            # launchInfo 落进版本设置
            vs_file = inst.versions_dir() / "1.12.2-forge-14.23.5.2854" / "pymcl.json"
            self.assertTrue(vs_file.is_file())
            vs = json.loads(vs_file.read_text(encoding="utf-8"))
            self.assertEqual(vs["memory_mb"], 4096)
            self.assertEqual(vs["jvm_args"], "-XX:+UseG1GC")
            self.assertEqual(vs["game_args"], "--demo")

    def test_nested_folder(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            zpath = _build_mcbbs_zip(td, _packmeta(), folder="我的包")
            inst, meta, _cls, _loader = self._run(zpath, td)
            self.assertEqual(meta["source"], "mcbbs")
            self.assertTrue((inst.path / "config" / "pack.cfg").is_file())

    def test_mcbbs_wins_over_curse_manifest(self):
        """同包并存 mcbbs.packmeta 与 manifest.json 时按 MCBBS 装（HMCL 同款优先级）。"""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            zpath = _build_mcbbs_zip(td, _packmeta(), also_curse_manifest=True)
            _inst, meta, _cls, _loader = self._run(zpath, td)
            self.assertEqual(meta["source"], "mcbbs")

    def test_vanilla_pack_skips_loader(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            zpath = _build_mcbbs_zip(td, _packmeta(addons=[_addon("game", "1.18.2")]))
            inst, meta, _cls, fake_loader = self._run(zpath, td)
            self.assertEqual(meta["loader"], "vanilla")
            fake_loader.assert_not_called()
            self.assertEqual(inst.meta_store["mc_version"], "1.18.2")

    def test_missing_game_addon_raises(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            zpath = _build_mcbbs_zip(td, _packmeta(addons=[_addon("forge", "1.0")]))
            with self.assertRaises(ModpackError) as ctx:
                self._run(zpath, td)
            self.assertIn("game", str(ctx.exception))

    def test_wrong_manifest_type_raises(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            mf = _packmeta()
            mf["manifestType"] = "somethingElse"
            zpath = _build_mcbbs_zip(td, mf)
            with self.assertRaises(ModpackError) as ctx:
                self._run(zpath, td)
            self.assertIn("manifestType", str(ctx.exception))


class ClassifyTests(unittest.TestCase):
    def test_mcbbs_zip_is_modpack(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            zpath = _build_mcbbs_zip(td, _packmeta())
            self.assertEqual(classify_file(zpath)["kind"], "modpack")

    def test_nested_mcbbs_zip_is_modpack(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            zpath = _build_mcbbs_zip(td, _packmeta(), folder="我的包")
            self.assertEqual(classify_file(zpath)["kind"], "modpack")


if __name__ == "__main__":
    unittest.main()
