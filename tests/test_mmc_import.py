# -*- coding: utf-8 -*-
"""MultiMC / Prism Launcher 实例包导入测试（不联网）。"""
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mclauncher import modpack  # noqa: E402
from mclauncher.modpack import (  # noqa: E402
    ModpackError,
    _mmc_instance_name,
    _mmc_root,
    parse_mmc_components,
)


def _comp(uid, version, name="", dep=False):
    d = {"uid": uid, "version": version}
    if name:
        d["cachedName"] = name
    if dep:
        d["dependencyOnly"] = True
    return d


class ParseComponentsTests(unittest.TestCase):
    def test_fabric_pack(self):
        pack = {"components": [
            _comp("org.lwjgl3", "3.3.1", dep=True),
            _comp("net.minecraft", "1.19.2"),
            _comp("net.fabricmc.intermediary", "1.19.2", dep=True),
            _comp("net.fabricmc.fabric-loader", "0.14.9"),
        ]}
        out = parse_mmc_components(pack)
        self.assertEqual(out["mc"], "1.19.2")
        self.assertEqual(out["loader"], "fabric-loader")
        self.assertEqual(out["loader_version"], "0.14.9")
        self.assertEqual(out["skipped"], [])

    def test_forge_neoforge_quilt_liteloader(self):
        for uid, expect in (
            ("net.minecraftforge", "forge"),
            ("net.neoforged", "neoforge"),
            ("org.quiltmc.quilt-loader", "quilt-loader"),
            ("com.mumfrey.liteloader", "liteloader"),
        ):
            pack = {"components": [_comp("net.minecraft", "1.20.1"), _comp(uid, "47.2.0")]}
            out = parse_mmc_components(pack)
            self.assertEqual(out["loader"], expect, uid)
            self.assertEqual(out["loader_version"], "47.2.0")

    def test_vanilla_only(self):
        out = parse_mmc_components({"components": [_comp("net.minecraft", "1.18.2")]})
        self.assertEqual(out["mc"], "1.18.2")
        self.assertIsNone(out["loader"])

    def test_unknown_component_reported(self):
        pack = {"components": [
            _comp("net.minecraft", "1.12.2"),
            _comp("org.multimc.jarmod.abc", "1.0", name="某个 Jar Mod"),
        ]}
        out = parse_mmc_components(pack)
        self.assertEqual(out["skipped"], ["某个 Jar Mod 1.0"])

    def test_cached_version_fallback(self):
        pack = {"components": [{"uid": "net.minecraft", "cachedVersion": "1.16.5"}]}
        self.assertEqual(parse_mmc_components(pack)["mc"], "1.16.5")

    def test_empty_pack(self):
        out = parse_mmc_components({})
        self.assertEqual(out["mc"], "")
        self.assertIsNone(out["loader"])


class InstanceNameTests(unittest.TestCase):
    def test_plain_cfg(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "instance.cfg").write_text(
                "InstanceType=OneSix\nname=我的整合包\niconKey=default\n", encoding="utf-8")
            self.assertEqual(_mmc_instance_name(Path(td)), "我的整合包")

    def test_prism_general_section(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "instance.cfg").write_text(
                "[General]\nConfigVersion=1.2\nname=Prism Pack\n", encoding="utf-8")
            self.assertEqual(_mmc_instance_name(Path(td)), "Prism Pack")

    def test_missing_cfg(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(_mmc_instance_name(Path(td)), "")


class MmcRootTests(unittest.TestCase):
    def test_root_level(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "mmc-pack.json").write_text("{}", encoding="utf-8")
            self.assertEqual(_mmc_root(Path(td)), Path(td))

    def test_nested_one_level(self):
        with tempfile.TemporaryDirectory() as td:
            sub = Path(td) / "MyPack"
            sub.mkdir()
            (sub / "mmc-pack.json").write_text("{}", encoding="utf-8")
            self.assertEqual(_mmc_root(Path(td)), sub)

    def test_absent(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "readme.txt").write_text("x", encoding="utf-8")
            self.assertIsNone(_mmc_root(Path(td)))


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


def _build_mmc_zip(td: Path, folder="MyPack", components=None, cfg=True,
                   game_dir="minecraft") -> Path:
    zpath = td / "pack.zip"
    comps = components if components is not None else [
        _comp("net.minecraft", "1.19.2"),
        _comp("net.fabricmc.fabric-loader", "0.14.9"),
    ]
    with zipfile.ZipFile(zpath, "w") as z:
        z.writestr(f"{folder}/mmc-pack.json",
                   json.dumps({"formatVersion": 1, "components": comps}))
        if cfg:
            z.writestr(f"{folder}/instance.cfg", "[General]\nname=我的 MMC 包\n")
        if game_dir:
            z.writestr(f"{folder}/{game_dir}/mods/a.jar", "jar-bytes")
            z.writestr(f"{folder}/{game_dir}/options.txt", "lang:zh_cn")
            z.writestr(f"{folder}/{game_dir}/logs/latest.log", "runtime junk")
    return zpath


class InstallDispatchTests(unittest.TestCase):
    def _run(self, zpath: Path, base: Path):
        inst = _FakeInstance(base)
        dm = mock.MagicMock()
        with mock.patch.object(modpack, "Installer") as fake_installer_cls, \
                mock.patch.object(modpack, "install_loader",
                                  return_value="fabric-loader-0.14.9-1.19.2") as fake_loader, \
                mock.patch.object(modpack, "_resolve_pack_minecraft",
                                  side_effect=lambda _dm, v, _p=None: v):
            meta = modpack.install_cf_zip(dm, str(zpath), inst)
        return inst, meta, fake_installer_cls, fake_loader

    def test_full_mmc_install(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            zpath = _build_mmc_zip(td)
            inst, meta, installer_cls, fake_loader = self._run(zpath, td)
            self.assertEqual(meta["source"], "multimc")
            self.assertEqual(meta["name"], "我的 MMC 包")
            self.assertEqual(meta["mc_version"], "1.19.2")
            self.assertEqual(meta["loader"], "fabric-loader-0.14.9")
            installer = installer_cls.return_value
            installer.install_version.assert_called_once()
            self.assertEqual(installer.install_version.call_args[0][0], "1.19.2")
            args = fake_loader.call_args[0]
            self.assertEqual(args[1:], ("fabric-loader", "0.14.9", "1.19.2"))
            # 游戏目录拷贝：mods / options.txt 进实例，logs 运行垃圾不拷
            self.assertTrue((inst.path / "mods" / "a.jar").is_file())
            self.assertTrue((inst.path / "options.txt").is_file())
            self.assertFalse((inst.path / "logs").exists())
            self.assertEqual(inst.meta_store["mc_version"], "fabric-loader-0.14.9-1.19.2")

    def test_name_falls_back_to_zip_stem(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            zpath = _build_mmc_zip(td, cfg=False)
            _inst, meta, _cls, _loader = self._run(zpath, td)
            self.assertEqual(meta["name"], "pack")

    def test_dot_minecraft_dir(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            zpath = _build_mmc_zip(td, game_dir=".minecraft")
            inst, _meta, _cls, _loader = self._run(zpath, td)
            self.assertTrue((inst.path / "mods" / "a.jar").is_file())

    def test_missing_minecraft_component_raises(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            zpath = _build_mmc_zip(td, components=[_comp("net.fabricmc.fabric-loader", "0.14.9")])
            with self.assertRaises(ModpackError) as ctx:
                self._run(zpath, td)
            self.assertIn("net.minecraft", str(ctx.exception))

    def test_plain_zip_error_mentions_mmc(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            zpath = td / "junk.zip"
            with zipfile.ZipFile(zpath, "w") as z:
                z.writestr("readme.txt", "not a pack")
            inst = _FakeInstance(td)
            with self.assertRaises(ModpackError) as ctx:
                modpack.install_cf_zip(mock.MagicMock(), str(zpath), inst)
            self.assertIn("mmc-pack.json", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
