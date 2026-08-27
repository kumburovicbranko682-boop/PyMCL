# -*- coding: utf-8 -*-
"""模组冲突扫描测试：重复安装、缺依赖、provides、禁用、加载器不匹配。"""
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mclauncher.ai.conflict import inspect_jar, scan_conflicts  # noqa: E402


class _FakeInstance:
    """detect_loader/detect_mc_version 只用 installed_ids 和 meta。"""

    def __init__(self, base: Path, loader_vid="fabric-loader-0.15.11-1.20.1"):
        self.name = "测试实例"
        self.path = base
        self._vid = loader_vid
        (base / "mods").mkdir(parents=True, exist_ok=True)

    def installed_ids(self):
        return [self._vid] if self._vid else []

    def meta(self):
        return {}


def _fabric_jar(path: Path, mod_id: str, version="1.0.0", depends=None,
                breaks=None, provides=None):
    data = {"schemaVersion": 1, "id": mod_id, "version": version, "name": mod_id}
    if depends:
        data["depends"] = depends
    if breaks:
        data["breaks"] = breaks
    if provides:
        data["provides"] = provides
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("fabric.mod.json", json.dumps(data))


def _forge_jar(path: Path, mod_id: str):
    text = (
        'modLoader="javafml"\nloaderVersion="[47,)"\nlicense="MIT"\n'
        f'[[mods]]\nmodId="{mod_id}"\nversion="1.0.0"\ndisplayName="{mod_id}"\n'
    )
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("META-INF/mods.toml", text)


def _issues(report, typ):
    return [i for i in report["issues"] if i["type"] == typ]


class ScanConflictsTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.base = Path(self._td.name)
        self.inst = _FakeInstance(self.base)
        self.mods = self.base / "mods"

    def tearDown(self):
        self._td.cleanup()

    def test_clean_pack_no_issues(self):
        _fabric_jar(self.mods / "a.jar", "moda", depends={"minecraft": "*", "java": ">=17"})
        _fabric_jar(self.mods / "b.jar", "modb")
        report = scan_conflicts(self.inst)
        self.assertEqual(report["issue_count"], 0)
        self.assertEqual(report["mod_count"], 2)
        self.assertEqual(report["loader"], "fabric")

    def test_duplicate_id(self):
        _fabric_jar(self.mods / "a-1.0.jar", "moda")
        _fabric_jar(self.mods / "a-1.1.jar", "moda", version="1.1.0")
        report = scan_conflicts(self.inst)
        dups = _issues(report, "duplicate_id")
        self.assertEqual(len(dups), 1)
        self.assertEqual(sorted(dups[0]["files"]), ["a-1.0.jar", "a-1.1.jar"])

    def test_duplicate_with_one_disabled_is_fine(self):
        _fabric_jar(self.mods / "a-1.0.jar", "moda")
        _fabric_jar(self.mods / "a-1.1.jar.disabled", "moda", version="1.1.0")
        report = scan_conflicts(self.inst)
        self.assertEqual(_issues(report, "duplicate_id"), [])

    def test_missing_dep(self):
        _fabric_jar(self.mods / "a.jar", "moda", depends={"modb": "*"})
        report = scan_conflicts(self.inst)
        missing = _issues(report, "missing_dep")
        self.assertEqual(len(missing), 1)
        self.assertEqual(missing[0]["need"], "modb")

    def test_dep_satisfied_by_provides(self):
        _fabric_jar(self.mods / "a.jar", "moda", depends={"modb-api": "*"})
        _fabric_jar(self.mods / "b.jar", "modb", provides=["modb-api"])
        report = scan_conflicts(self.inst)
        self.assertEqual(_issues(report, "missing_dep"), [])

    def test_disabled_mod_does_not_satisfy_dep(self):
        _fabric_jar(self.mods / "a.jar", "moda", depends={"modb": "*"})
        _fabric_jar(self.mods / "b.jar.disabled", "modb")
        report = scan_conflicts(self.inst)
        missing = _issues(report, "missing_dep")
        self.assertEqual(len(missing), 1)
        self.assertEqual(missing[0]["need"], "modb")

    def test_fabric_api_special_case(self):
        _fabric_jar(self.mods / "a.jar", "moda", depends={"fabric": "*"})
        report = scan_conflicts(self.inst)
        missing = _issues(report, "missing_dep")
        self.assertEqual(len(missing), 1)
        self.assertEqual(missing[0]["need"], "fabric-api")
        _fabric_jar(self.mods / "fabric-api.jar", "fabric-api")
        report = scan_conflicts(self.inst)
        self.assertEqual(_issues(report, "missing_dep"), [])

    def test_breaks_between_present_mods(self):
        _fabric_jar(self.mods / "a.jar", "moda", breaks={"modb": "*"})
        _fabric_jar(self.mods / "b.jar", "modb")
        report = scan_conflicts(self.inst)
        breaks = _issues(report, "breaks")
        self.assertEqual(len(breaks), 1)
        self.assertEqual(breaks[0]["other"], "modb")

    def test_loader_mismatch(self):
        _forge_jar(self.mods / "forge_mod.jar", "forgemod")
        report = scan_conflicts(self.inst)
        mismatch = _issues(report, "loader_mismatch")
        self.assertEqual(len(mismatch), 1)
        self.assertIn("forge", mismatch[0]["message"])

    def test_mods_dir_override(self):
        iso = self.base / "versions" / "1.20.1" / "mods"
        iso.mkdir(parents=True)
        _fabric_jar(iso / "a.jar", "moda", depends={"modb": "*"})
        _fabric_jar(self.mods / "unrelated.jar", "other")
        report = scan_conflicts(self.inst, mods_dir=iso)
        self.assertEqual(report["mod_count"], 1)
        self.assertEqual(len(_issues(report, "missing_dep")), 1)


class InspectJarTests(unittest.TestCase):
    def test_fabric_metadata(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "sodium.jar"
            _fabric_jar(p, "sodium", version="0.5.8", provides=["sodium-api"])
            info = inspect_jar(p)
            self.assertEqual(info["id"], "sodium")
            self.assertEqual(info["version"], "0.5.8")
            self.assertEqual(info["loader"], "fabric")
            self.assertEqual(info["provides"], ["sodium-api"])
            self.assertTrue(info["enabled"])

    def test_disabled_flag(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "x.jar.disabled"
            _fabric_jar(p, "x")
            self.assertFalse(inspect_jar(p)["enabled"])

    def test_broken_jar_reports_error(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "broken.jar"
            p.write_bytes(b"not a zip at all")
            info = inspect_jar(p)
            self.assertIn("error", info)
            self.assertEqual(info["id"], "broken")


if __name__ == "__main__":
    unittest.main()
