# -*- coding: utf-8 -*-
"""模组冲突扫描测试（普通 UI 入口 backend.scan_mod_conflicts 的核心）。

覆盖 重复安装 / 缺前置 / breaks 不兼容 / 加载器不匹配，
以及版本隔离时的 mods_dir 覆盖。
"""
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mclauncher.ai import conflict  # noqa: E402


class _Inst:
    def __init__(self, root):
        self.path = Path(root)
        self.name = "test"


def _fabric_jar(path: Path, mod_id, version="1.0", depends=None, breaks=None):
    data = {"schemaVersion": 1, "id": mod_id, "name": mod_id, "version": version}
    if depends:
        data["depends"] = depends
    if breaks:
        data["breaks"] = breaks
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("fabric.mod.json", json.dumps(data))


def _scan(inst, mods_dir=None, loader="fabric", mc="1.20.1"):
    with mock.patch.object(conflict, "detect_loader", return_value=loader), \
            mock.patch.object(conflict, "detect_mc_version", return_value=mc):
        return conflict.scan_conflicts(inst, mods_dir=mods_dir)


class ConflictScanTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.inst = _Inst(self.tmp.name)
        self.mods = self.inst.path / "mods"

    def tearDown(self):
        self.tmp.cleanup()

    def test_clean_instance_reports_no_issues(self):
        _fabric_jar(self.mods / "sodium.jar", "sodium")
        result = _scan(self.inst)
        self.assertEqual(result["issues"], [])
        self.assertEqual(result["mod_count"], 1)
        self.assertEqual(result["enabled"], 1)

    def test_duplicate_id_detected(self):
        _fabric_jar(self.mods / "sodium-1.jar", "sodium", "0.5.0")
        _fabric_jar(self.mods / "sodium-2.jar", "sodium", "0.5.1")
        result = _scan(self.inst)
        kinds = [i["type"] for i in result["issues"]]
        self.assertIn("duplicate_id", kinds)

    def test_disabled_duplicate_not_reported(self):
        _fabric_jar(self.mods / "sodium-1.jar", "sodium", "0.5.0")
        _fabric_jar(self.mods / "sodium-2.jar.disabled", "sodium", "0.5.1")
        result = _scan(self.inst)
        self.assertEqual([i for i in result["issues"] if i["type"] == "duplicate_id"], [])

    def test_missing_dependency_detected(self):
        _fabric_jar(self.mods / "extra.jar", "extra",
                    depends={"some-lib": ">=1.0", "minecraft": "*"})
        result = _scan(self.inst)
        missing = [i for i in result["issues"] if i["type"] == "missing_dep"]
        self.assertEqual(len(missing), 1)
        self.assertEqual(missing[0]["need"], "some-lib")

    def test_satisfied_dependency_not_reported(self):
        _fabric_jar(self.mods / "lib.jar", "some-lib")
        _fabric_jar(self.mods / "extra.jar", "extra", depends={"some-lib": "*"})
        result = _scan(self.inst)
        self.assertEqual([i for i in result["issues"] if i["type"] == "missing_dep"], [])

    def test_breaks_detected(self):
        _fabric_jar(self.mods / "a.jar", "moda", breaks={"modb": "*"})
        _fabric_jar(self.mods / "b.jar", "modb")
        result = _scan(self.inst)
        kinds = [i["type"] for i in result["issues"]]
        self.assertIn("breaks", kinds)

    def test_loader_mismatch_detected(self):
        _fabric_jar(self.mods / "fab.jar", "fabmod")
        result = _scan(self.inst, loader="forge")
        kinds = [i["type"] for i in result["issues"]]
        self.assertIn("loader_mismatch", kinds)

    def test_mods_dir_override_used(self):
        # 实例共享 mods 干净，隔离目录里有重复——扫隔离目录必须报
        _fabric_jar(self.mods / "clean.jar", "clean")
        iso = self.inst.path / "versions" / "1.20.1" / "mods"
        _fabric_jar(iso / "dup-1.jar", "dup")
        _fabric_jar(iso / "dup-2.jar", "dup")
        shared = _scan(self.inst)
        self.assertEqual(shared["issues"], [])
        isolated = _scan(self.inst, mods_dir=iso)
        kinds = [i["type"] for i in isolated["issues"]]
        self.assertIn("duplicate_id", kinds)
        self.assertEqual(isolated["mod_count"], 2)


if __name__ == "__main__":
    unittest.main()
