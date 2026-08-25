# -*- coding: utf-8 -*-
"""冲突扫描：重复安装、缺依赖、加载器不匹配、互斥模组、隔离目录。"""
from __future__ import annotations

import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from mclauncher.ai import conflict
from mclauncher.instances import Instance


def fabric_jar(mod_id: str, version: str = "1.0", depends: dict | None = None,
               breaks: dict | None = None) -> bytes:
    meta = {"schemaVersion": 1, "id": mod_id, "name": mod_id.title(),
            "version": version}
    if depends:
        meta["depends"] = depends
    if breaks:
        meta["breaks"] = breaks
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("fabric.mod.json", json.dumps(meta))
    return buf.getvalue()


def forge_jar(mod_id: str, version: str = "1.0") -> bytes:
    toml = (
        'modLoader="javafml"\nloaderVersion="[47,)"\nlicense="MIT"\n'
        "[[mods]]\n"
        f'modId="{mod_id}"\ndisplayName="{mod_id}"\nversion="{version}"\n'
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("META-INF/mods.toml", toml)
    return buf.getvalue()


class Sandbox(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        patcher = mock.patch(
            "mclauncher.instances.get_instance_path",
            side_effect=lambda name: self.root / "instances" / name)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def make_instance(self, name="conflict-test") -> Instance:
        inst = Instance(name)
        inst.create()
        return inst

    def add_version(self, inst: Instance, vid: str):
        vdir = inst.versions_dir() / vid
        vdir.mkdir(parents=True, exist_ok=True)
        (vdir / f"{vid}.json").write_text(json.dumps({"id": vid}), "utf-8")

    def write_mod(self, folder: Path, filename: str, data: bytes):
        folder.mkdir(parents=True, exist_ok=True)
        (folder / filename).write_bytes(data)


class TestScanConflicts(Sandbox):
    def test_clean_instance_no_issues(self):
        inst = self.make_instance()
        self.add_version(inst, "1.20.1-fabric")
        mods = inst.path / "mods"
        self.write_mod(mods, "sodium.jar", fabric_jar("sodium"))
        report = conflict.scan_conflicts(inst)
        self.assertEqual(report["issue_count"], 0)
        self.assertEqual(report["mod_count"], 1)
        self.assertEqual(report["loader"], "fabric")

    def test_duplicate_id(self):
        inst = self.make_instance()
        mods = inst.path / "mods"
        self.write_mod(mods, "sodium-1.jar", fabric_jar("sodium", "1.0"))
        self.write_mod(mods, "sodium-2.jar", fabric_jar("sodium", "2.0"))
        report = conflict.scan_conflicts(inst)
        dup = [i for i in report["issues"] if i["type"] == "duplicate_id"]
        self.assertEqual(len(dup), 1)
        self.assertEqual(dup[0]["id"], "sodium")
        self.assertEqual(sorted(dup[0]["files"]), ["sodium-1.jar", "sodium-2.jar"])

    def test_disabled_copy_not_duplicate(self):
        inst = self.make_instance()
        mods = inst.path / "mods"
        self.write_mod(mods, "sodium-1.jar", fabric_jar("sodium", "1.0"))
        self.write_mod(mods, "sodium-2.jar.disabled", fabric_jar("sodium", "2.0"))
        report = conflict.scan_conflicts(inst)
        self.assertFalse([i for i in report["issues"] if i["type"] == "duplicate_id"])

    def test_missing_dep(self):
        inst = self.make_instance()
        mods = inst.path / "mods"
        self.write_mod(mods, "reeses.jar",
                       fabric_jar("reeses-sodium-options", depends={"sodium": "*"}))
        report = conflict.scan_conflicts(inst)
        missing = [i for i in report["issues"] if i["type"] == "missing_dep"]
        self.assertEqual(len(missing), 1)
        self.assertEqual(missing[0]["need"], "sodium")
        # 装上依赖后不再报
        self.write_mod(mods, "sodium.jar", fabric_jar("sodium"))
        report = conflict.scan_conflicts(inst)
        self.assertFalse([i for i in report["issues"] if i["type"] == "missing_dep"])

    def test_loader_and_java_deps_skipped(self):
        inst = self.make_instance()
        mods = inst.path / "mods"
        self.write_mod(mods, "a.jar", fabric_jar(
            "a", depends={"minecraft": "*", "java": ">=17", "fabricloader": "*"}))
        report = conflict.scan_conflicts(inst)
        self.assertFalse([i for i in report["issues"] if i["type"] == "missing_dep"])

    def test_loader_mismatch(self):
        inst = self.make_instance()
        self.add_version(inst, "1.20.1-fabric")
        mods = inst.path / "mods"
        self.write_mod(mods, "jei-forge.jar", forge_jar("jei"))
        report = conflict.scan_conflicts(inst)
        bad = [i for i in report["issues"] if i["type"] == "loader_mismatch"]
        self.assertEqual(len(bad), 1)
        self.assertEqual(bad[0]["file"], "jei-forge.jar")
        self.assertEqual(bad[0]["mod_loader"], "forge")
        self.assertEqual(bad[0]["need"], "fabric")

    def test_breaks(self):
        inst = self.make_instance()
        mods = inst.path / "mods"
        self.write_mod(mods, "optifabric.jar",
                       fabric_jar("optifabric", breaks={"sodium": "*"}))
        self.write_mod(mods, "sodium.jar", fabric_jar("sodium"))
        report = conflict.scan_conflicts(inst)
        broken = [i for i in report["issues"] if i["type"] == "breaks"]
        self.assertEqual(len(broken), 1)
        self.assertEqual(broken[0]["other"], "sodium")


class TestIsolatedDirAndVersion(Sandbox):
    def test_custom_mods_dir(self):
        inst = self.make_instance()
        shared = inst.path / "mods"
        self.write_mod(shared, "shared.jar", fabric_jar("shared-mod"))
        iso = inst.path / "versions" / "1.20.1-fabric" / "mods"
        self.write_mod(iso, "iso.jar",
                       fabric_jar("iso-mod", depends={"ghost": "*"}))
        report = conflict.scan_conflicts(inst, mods_dir=iso,
                                         version_id="1.20.1-fabric")
        self.assertEqual(report["mod_count"], 1)
        self.assertEqual(report["mods"][0]["id"], "iso-mod")
        self.assertEqual(report["version"], "1.20.1-fabric")
        missing = [i for i in report["issues"] if i["type"] == "missing_dep"]
        self.assertEqual(len(missing), 1)

    def test_version_id_wins_loader_detection(self):
        inst = self.make_instance()
        # 实例里装了 forge 版本，但按 fabric 版本的隔离目录扫
        self.add_version(inst, "1.20.1-forge")
        mods = inst.path / "mods"
        self.write_mod(mods, "sodium.jar", fabric_jar("sodium"))
        report = conflict.scan_conflicts(inst, version_id="1.19.2-fabric")
        self.assertEqual(report["loader"], "fabric")
        self.assertEqual(report["mc_version"], "1.19.2")
        self.assertFalse([i for i in report["issues"] if i["type"] == "loader_mismatch"])

    def test_empty_isolated_dir_does_not_fall_back(self):
        inst = self.make_instance()
        shared = inst.path / "mods"
        self.write_mod(shared, "shared.jar", fabric_jar("shared-mod"))
        iso = inst.path / "versions" / "v" / "mods"
        iso.mkdir(parents=True)
        report = conflict.scan_conflicts(inst, mods_dir=iso)
        self.assertEqual(report["mod_count"], 0)


class TestBridgeFacade(Sandbox):
    def test_bridge_scan_mod_conflicts(self):
        from bridge.api import BackendAPI
        inst = self.make_instance("bridge-scan")
        mods = inst.path / "mods"
        self.write_mod(mods, "a.jar", fabric_jar("a", depends={"b": "*"}))
        api = BackendAPI.__new__(BackendAPI)  # 不跑 __init__，只测门面路由
        with mock.patch.object(BackendAPI, "_instance", return_value=inst):
            report = api.scan_mod_conflicts("bridge-scan")
        self.assertEqual(report["mod_count"], 1)
        self.assertEqual(report["issue_count"], 1)
        self.assertEqual(report["issues"][0]["type"], "missing_dep")


if __name__ == "__main__":
    unittest.main()
