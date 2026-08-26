# -*- coding: utf-8 -*-
"""版本组件识别 + 加载器原地更换（version_components）。"""
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mclauncher import version_components as vc  # noqa: E402


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

    def has_version(self, vid):
        return (self.versions_dir() / vid / f"{vid}.json").is_file()


def _write_version(inst, vid, data):
    d = inst.versions_dir() / vid
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{vid}.json").write_text(json.dumps(data), encoding="utf-8")


def _vanilla(inst, mc="1.20.4"):
    _write_version(inst, mc, {
        "id": mc, "libraries": [],
        "downloads": {"client": {"url": "https://x/client.jar", "sha1": "0" * 40}},
    })


def _fabric(inst, vid, mc="1.20.4", loader="0.15.11", parent=True):
    if parent:
        _vanilla(inst, mc)
    _write_version(inst, vid, {
        "id": vid, "inheritsFrom": mc,
        "libraries": [{"name": f"net.fabricmc:fabric-loader:{loader}"}],
    })


class ComponentsTest(unittest.TestCase):
    def test_vanilla_canonical(self):
        with tempfile.TemporaryDirectory() as td:
            inst = FakeInstance(td)
            _vanilla(inst)
            c = vc.components_of(inst, "1.20.4")
            self.assertEqual(c["mc"], "1.20.4")
            self.assertEqual(c["loader"], "")

    def test_fabric_chain(self):
        with tempfile.TemporaryDirectory() as td:
            inst = FakeInstance(td)
            _fabric(inst, "fabric-loader-0.15.11-1.20.4")
            c = vc.components_of(inst, "fabric-loader-0.15.11-1.20.4")
            self.assertEqual((c["mc"], c["loader"], c["loader_version"]),
                             ("1.20.4", "fabric", "0.15.11"))

    def test_missing_parent_uses_inherits_from(self):
        with tempfile.TemporaryDirectory() as td:
            inst = FakeInstance(td)
            _fabric(inst, "myver", parent=False)
            c = vc.components_of(inst, "myver")
            self.assertEqual(c["mc"], "1.20.4")
            self.assertEqual(c["loader"], "fabric")

    def test_forge_modern(self):
        with tempfile.TemporaryDirectory() as td:
            inst = FakeInstance(td)
            _vanilla(inst)
            _write_version(inst, "1.20.4-forge-49.0.30", {
                "id": "1.20.4-forge-49.0.30", "inheritsFrom": "1.20.4",
                "libraries": [{"name": "net.minecraftforge:forge:1.20.4-49.0.30"}],
            })
            c = vc.components_of(inst, "1.20.4-forge-49.0.30")
            self.assertEqual((c["mc"], c["loader"], c["loader_version"]),
                             ("1.20.4", "forge", "49.0.30"))

    def test_forge_legacy_monolithic(self):
        with tempfile.TemporaryDirectory() as td:
            inst = FakeInstance(td)
            _write_version(inst, "1.7.10-Forge10.13.4.1614-1.7.10", {
                "id": "1.7.10-Forge10.13.4.1614-1.7.10",
                "libraries": [
                    {"name": "net.minecraftforge:forge:1.7.10-10.13.4.1614-1.7.10"},
                ],
            })
            c = vc.components_of(inst, "1.7.10-Forge10.13.4.1614-1.7.10")
            self.assertEqual(c["loader"], "forge")
            self.assertEqual(c["loader_version"], "10.13.4.1614")
            self.assertEqual(c["mc"], "1.7.10")

    def test_neoforge_both_coordinates(self):
        with tempfile.TemporaryDirectory() as td:
            inst = FakeInstance(td)
            _vanilla(inst, "1.20.1")
            _write_version(inst, "neo-old", {
                "id": "neo-old", "inheritsFrom": "1.20.1",
                "libraries": [{"name": "net.neoforged:forge:1.20.1-47.1.84"}],
            })
            _vanilla(inst, "1.20.4")
            _write_version(inst, "neo-new", {
                "id": "neo-new", "inheritsFrom": "1.20.4",
                "libraries": [{"name": "net.neoforged:neoforge:20.4.237"}],
            })
            old = vc.components_of(inst, "neo-old")
            new = vc.components_of(inst, "neo-new")
            self.assertEqual((old["loader"], old["loader_version"]), ("neoforge", "47.1.84"))
            self.assertEqual((new["loader"], new["loader_version"]), ("neoforge", "20.4.237"))

    def test_optifine_version(self):
        with tempfile.TemporaryDirectory() as td:
            inst = FakeInstance(td)
            _vanilla(inst)
            _write_version(inst, "1.20.4-OptiFine_HD_U_I6", {
                "id": "1.20.4-OptiFine_HD_U_I6", "inheritsFrom": "1.20.4",
                "libraries": [{"name": "optifine:OptiFine:1.20.4_HD_U_I6"}],
            })
            c = vc.components_of(inst, "1.20.4-OptiFine_HD_U_I6")
            self.assertEqual((c["loader"], c["loader_version"]), ("optifine", "HD_U_I6"))

    def test_copied_vanilla_mc_from_jar(self):
        with tempfile.TemporaryDirectory() as td:
            inst = FakeInstance(td)
            _write_version(inst, "我的复制", {"id": "我的复制", "libraries": [],
                                             "downloads": {"client": {"url": "u"}}})
            jar = inst.versions_dir() / "我的复制" / "我的复制.jar"
            with zipfile.ZipFile(jar, "w") as zf:
                zf.writestr("version.json", json.dumps({"id": "1.20.4"}))
            c = vc.components_of(inst, "我的复制")
            self.assertEqual(c["mc"], "1.20.4")

    def test_unknown_mc_is_empty(self):
        with tempfile.TemporaryDirectory() as td:
            inst = FakeInstance(td)
            _write_version(inst, "神秘版本", {"id": "神秘版本", "libraries": []})
            c = vc.components_of(inst, "神秘版本")
            self.assertEqual(c["mc"], "")

    def test_snapshot_id_is_vanilla(self):
        with tempfile.TemporaryDirectory() as td:
            inst = FakeInstance(td)
            _write_version(inst, "24w14a", {"id": "24w14a", "libraries": []})
            c = vc.components_of(inst, "24w14a")
            self.assertEqual(c["mc"], "24w14a")

    def test_not_installed_raises(self):
        with tempfile.TemporaryDirectory() as td:
            inst = FakeInstance(td)
            with self.assertRaises(vc.ComponentError):
                vc.components_of(inst, "nope")


def _fake_install_game(canonical_id, canonical_json, jar_bytes=None):
    """伪 install_game：把规范版本写盘并返回 id，记录收到的参数。"""
    calls = []

    def fake(installer, version, loader, loader_version="", extra=None):
        calls.append({"mc": version, "loader": loader,
                      "loader_version": loader_version, "extra": dict(extra or {})})
        inst = installer.instance
        d = inst.versions_dir() / canonical_id
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{canonical_id}.json").write_text(
            json.dumps(canonical_json), encoding="utf-8")
        if jar_bytes is not None:
            (d / f"{canonical_id}.jar").write_bytes(jar_bytes)
        return canonical_id

    fake.calls = calls
    return fake


class SwitchLoaderTest(unittest.TestCase):
    def test_in_place_switch_and_cleanup(self):
        with tempfile.TemporaryDirectory() as td:
            inst = FakeInstance(td)
            _fabric(inst, "myver", loader="0.15.11")
            (inst.versions_dir() / "myver" / "pymcl.json").write_text(
                '{"memory_mb": 4096}', encoding="utf-8")
            new_json = {"id": "fabric-loader-0.16.9-1.20.4", "inheritsFrom": "1.20.4",
                        "libraries": [{"name": "net.fabricmc:fabric-loader:0.16.9"}]}
            fake = _fake_install_game("fabric-loader-0.16.9-1.20.4", new_json)
            with mock.patch("mclauncher.game_install.install_game", fake):
                res = vc.switch_loader(inst, "myver", "fabric", "0.16.9")
            self.assertTrue(res["in_place"])
            self.assertEqual(res["version"], "myver")
            self.assertEqual(res["loader_version"], "0.16.9")
            written = inst.version_json("myver")
            self.assertEqual(written["id"], "myver")
            self.assertEqual(written["inheritsFrom"], "1.20.4")
            # 规范目录是本次新建的 → 清掉；设置文件保留
            self.assertFalse((inst.versions_dir() / "fabric-loader-0.16.9-1.20.4").exists())
            self.assertTrue((inst.versions_dir() / "myver" / "pymcl.json").is_file())
            self.assertEqual(fake.calls[0]["mc"], "1.20.4")

    def test_preexisting_canonical_kept(self):
        with tempfile.TemporaryDirectory() as td:
            inst = FakeInstance(td)
            _fabric(inst, "myver", loader="0.15.11")
            canon = {"id": "fabric-loader-0.16.9-1.20.4", "inheritsFrom": "1.20.4",
                     "libraries": [{"name": "net.fabricmc:fabric-loader:0.16.9"}]}
            _write_version(inst, "fabric-loader-0.16.9-1.20.4", canon)
            fake = _fake_install_game("fabric-loader-0.16.9-1.20.4", canon)
            with mock.patch("mclauncher.game_install.install_game", fake):
                vc.switch_loader(inst, "myver", "fabric", "0.16.9")
            self.assertTrue(inst.has_version("fabric-loader-0.16.9-1.20.4"))

    def test_remove_loader_restores_vanilla_and_copies_jar(self):
        with tempfile.TemporaryDirectory() as td:
            inst = FakeInstance(td)
            _fabric(inst, "myver")
            (inst.versions_dir() / "1.20.4" / "1.20.4.jar").write_bytes(b"JARDATA")
            vanilla_json = json.loads(
                (inst.versions_dir() / "1.20.4" / "1.20.4.json").read_text())
            fake = _fake_install_game("1.20.4", vanilla_json)
            with mock.patch("mclauncher.game_install.install_game", fake):
                res = vc.switch_loader(inst, "myver", "")
            self.assertTrue(res["in_place"])
            self.assertEqual(res["loader"], "")
            written = inst.version_json("myver")
            self.assertEqual(written["id"], "myver")
            self.assertNotIn("inheritsFrom", written)
            self.assertEqual(
                (inst.versions_dir() / "myver" / "myver.jar").read_bytes(), b"JARDATA")
            # 原版规范目录不能被清理
            self.assertTrue(inst.has_version("1.20.4"))
            self.assertEqual(fake.calls[0]["loader"], "无")

    def test_stale_jar_removed_when_new_json_reuses_parent(self):
        with tempfile.TemporaryDirectory() as td:
            inst = FakeInstance(td)
            _fabric(inst, "myver", loader="0.15.11")
            (inst.versions_dir() / "myver" / "myver.jar").write_bytes(b"OLD")
            new_json = {"id": "fabric-loader-0.16.9-1.20.4", "inheritsFrom": "1.20.4",
                        "libraries": [{"name": "net.fabricmc:fabric-loader:0.16.9"}]}
            fake = _fake_install_game("fabric-loader-0.16.9-1.20.4", new_json)
            with mock.patch("mclauncher.game_install.install_game", fake):
                vc.switch_loader(inst, "myver", "fabric", "0.16.9")
            self.assertFalse((inst.versions_dir() / "myver" / "myver.jar").exists())

    def test_canonical_vanilla_gets_new_version(self):
        with tempfile.TemporaryDirectory() as td:
            inst = FakeInstance(td)
            _vanilla(inst)
            new_json = {"id": "fabric-loader-0.16.9-1.20.4", "inheritsFrom": "1.20.4",
                        "libraries": [{"name": "net.fabricmc:fabric-loader:0.16.9"}]}
            fake = _fake_install_game("fabric-loader-0.16.9-1.20.4", new_json)
            with mock.patch("mclauncher.game_install.install_game", fake):
                res = vc.switch_loader(inst, "1.20.4", "fabric", "0.16.9")
            self.assertFalse(res["in_place"])
            self.assertEqual(res["version"], "fabric-loader-0.16.9-1.20.4")
            # 原版目录原封不动
            self.assertEqual(inst.version_json("1.20.4")["id"], "1.20.4")

    def test_already_vanilla_raises(self):
        with tempfile.TemporaryDirectory() as td:
            inst = FakeInstance(td)
            _vanilla(inst)
            with self.assertRaises(vc.ComponentError):
                vc.switch_loader(inst, "1.20.4", "")

    def test_unknown_mc_raises(self):
        with tempfile.TemporaryDirectory() as td:
            inst = FakeInstance(td)
            _write_version(inst, "神秘版本", {"id": "神秘版本", "libraries": []})
            with self.assertRaises(vc.ComponentError):
                vc.switch_loader(inst, "神秘版本", "fabric")

    def test_optifine_version_token_split(self):
        with tempfile.TemporaryDirectory() as td:
            inst = FakeInstance(td)
            _fabric(inst, "myver")
            of_json = {"id": "1.20.4-OptiFine_HD_U_I6", "inheritsFrom": "1.20.4",
                       "libraries": [{"name": "optifine:OptiFine:1.20.4_HD_U_I6"}]}
            fake = _fake_install_game("1.20.4-OptiFine_HD_U_I6", of_json)
            with mock.patch("mclauncher.game_install.install_game", fake):
                res = vc.switch_loader(inst, "myver", "optifine", "HD_U_I6")
            call = fake.calls[0]
            self.assertEqual(call["extra"].get("optifine_type"), "HD_U")
            self.assertEqual(call["extra"].get("optifine_patch"), "I6")
            self.assertEqual(call["loader_version"], "")
            self.assertEqual(res["loader"], "optifine")


class BackendFacadeTest(unittest.TestCase):
    """两个门面都暴露 get_version_components / switch_loader 且签名一致。"""

    def test_methods_exist_and_aligned(self):
        import inspect
        mods = []
        try:
            from bridge.api import BackendAPI as Bridge
            mods.append(Bridge)
        except Exception:
            pass
        try:
            from app.backend import BackendAPI as Qt
            mods.append(Qt)
        except Exception:
            pass
        self.assertTrue(mods, "至少要能导入一个门面")
        sigs = set()
        for cls in mods:
            for name in ("get_version_components", "switch_loader"):
                fn = getattr(cls, name, None)
                self.assertIsNotNone(fn, f"{cls.__module__} 缺少 {name}")
                sigs.add((name, str(inspect.signature(fn))))
        # 每个方法在两个门面中的签名应一致
        names = [s[0] for s in sigs]
        for name in ("get_version_components", "switch_loader"):
            self.assertEqual(names.count(name), 1, f"{name} 两个门面签名不一致: {sigs}")


if __name__ == "__main__":
    unittest.main()
