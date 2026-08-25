# -*- coding: utf-8 -*-
"""模组展示元数据（mod_info）与解析器扩展字段的测试。"""

import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mclauncher import mod_info  # noqa: E402
from mclauncher.ai.conflict import inspect_jar  # noqa: E402

# 1x1 透明 PNG
import base64
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")


def _jar(path: Path, members: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members.items():
            if isinstance(data, (dict, list)):
                data = json.dumps(data)
            if isinstance(data, str):
                data = data.encode("utf-8")
            zf.writestr(name, data)
    return path


def _fabric_jar(path: Path, icon="assets/demo/icon.png", write_icon=True, **overrides):
    meta = {
        "id": "demo",
        "name": "Demo Mod",
        "version": "1.2.3",
        "description": "A demo mod.\nSecond line.",
        "authors": ["Alice", {"name": "Bob"}],
        "depends": {"minecraft": "*"},
    }
    if icon:
        meta["icon"] = icon
    meta.update(overrides)
    members = {"fabric.mod.json": meta}
    if icon and write_icon and isinstance(icon, str):
        members[icon] = _PNG
    return _jar(path, members)


class ParserFieldsTests(unittest.TestCase):
    """inspect_jar 新增的 description / authors / icon 字段。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_fabric_fields(self):
        p = _fabric_jar(self.dir / "demo.jar")
        info = inspect_jar(p)
        self.assertEqual(info["name"], "Demo Mod")
        self.assertEqual(info["version"], "1.2.3")
        self.assertIn("A demo mod.", info["description"])
        self.assertEqual(info["authors"], ["Alice", "Bob"])
        self.assertEqual(info["icon"], "assets/demo/icon.png")

    def test_fabric_icon_size_map_picks_largest(self):
        p = _fabric_jar(self.dir / "demo.jar",
                        icon={"16": "a16.png", "128": "a128.png", "64": "a64.png"})
        info = inspect_jar(p)
        self.assertEqual(info["icon"], "a128.png")

    def test_quilt_fields(self):
        p = _jar(self.dir / "q.jar", {"quilt.mod.json": {
            "quilt_loader": {
                "id": "qmod", "version": "2.0",
                "metadata": {
                    "name": "Quilt Mod",
                    "description": "quilty",
                    "contributors": {"Carol": "Owner"},
                    "icon": "icon.png",
                },
            },
        }})
        info = inspect_jar(p)
        self.assertEqual(info["name"], "Quilt Mod")
        self.assertEqual(info["description"], "quilty")
        self.assertEqual(info["authors"], ["Carol"])
        self.assertEqual(info["icon"], "icon.png")

    def test_forge_toml_fields(self):
        toml = (
            'modLoader="javafml"\n'
            'logoFile="top_logo.png"\n'
            "[[mods]]\n"
            'modId="fmod"\n'
            'displayName="Forge Mod"\n'
            'version="3.1"\n'
            'description="forge desc"\n'
            'authors="Dan, Erin"\n'
        )
        p = _jar(self.dir / "f.jar", {"META-INF/mods.toml": toml})
        info = inspect_jar(p)
        self.assertEqual(info["name"], "Forge Mod")
        self.assertEqual(info["description"], "forge desc")
        self.assertEqual(info["authors"], ["Dan", "Erin"])
        self.assertEqual(info["icon"], "top_logo.png")

    def test_forge_toml_mod_logo_overrides_top(self):
        toml = (
            'logoFile="top.png"\n'
            "[[mods]]\n"
            'modId="m"\n'
            'logoFile="mod.png"\n'
        )
        p = _jar(self.dir / "f2.jar", {"META-INF/mods.toml": toml})
        self.assertEqual(inspect_jar(p)["icon"], "mod.png")

    def test_mcmod_info_fields(self):
        p = _jar(self.dir / "old.jar", {"mcmod.info": [{
            "modid": "legacy", "name": "Legacy Mod", "version": "0.9",
            "description": "old desc", "authorList": ["Fay"],
            "logoFile": "logo.png",
        }]})
        info = inspect_jar(p)
        self.assertEqual(info["name"], "Legacy Mod")
        self.assertEqual(info["description"], "old desc")
        self.assertEqual(info["authors"], ["Fay"])
        self.assertEqual(info["icon"], "logo.png")

    def test_plain_jar_defaults(self):
        p = _jar(self.dir / "plain.jar", {"whatever.txt": "x"})
        info = inspect_jar(p)
        self.assertEqual(info["description"], "")
        self.assertEqual(info["authors"], [])
        self.assertEqual(info["icon"], "")


class DescribeModsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.mods = base / "mods"
        self.mods.mkdir()
        self.cache = base / "cache"
        self.addCleanup(self.tmp.cleanup)

    def _describe(self):
        return mod_info.describe_mods_at(self.mods, cache_dir=self.cache)

    def test_metadata_and_icon_extraction(self):
        _fabric_jar(self.mods / "demo-1.2.3.jar")
        rows = self._describe()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["filename"], "demo-1.2.3.jar")
        self.assertEqual(row["name"], "Demo Mod")
        self.assertEqual(row["version"], "1.2.3")
        self.assertEqual(row["loader"], "fabric")
        self.assertEqual(row["authors"], ["Alice", "Bob"])
        # 描述里的换行被压平成单行
        self.assertEqual(row["description"], "A demo mod. Second line.")
        self.assertTrue(row["icon"], "icon 应抽取到缓存目录")
        self.assertTrue(Path(row["icon"]).is_file())
        self.assertEqual(Path(row["icon"]).read_bytes(), _PNG)

    def test_no_metadata_falls_back_to_filename(self):
        _jar(self.mods / "unknown-mod.jar", {"a.txt": "x"})
        row = self._describe()[0]
        self.assertEqual(row["name"], "unknown-mod")
        self.assertEqual(row["icon"], "")
        self.assertEqual(row["description"], "")

    def test_disabled_jar_display_name(self):
        _jar(self.mods / "off-mod.jar.disabled", {"a.txt": "x"})
        row = self._describe()[0]
        self.assertFalse(row["enabled"])
        self.assertEqual(row["name"], "off-mod")

    def test_disabled_jar_with_metadata_uses_mod_name(self):
        _fabric_jar(self.mods / "off2.jar.disabled")
        row = self._describe()[0]
        self.assertEqual(row["name"], "Demo Mod")
        self.assertFalse(row["enabled"])

    def test_corrupt_jar_does_not_crash(self):
        (self.mods / "broken.jar").write_bytes(b"not a zip at all")
        row = self._describe()[0]
        self.assertEqual(row["name"], "broken")
        self.assertEqual(row["loader"], "unknown")

    def test_second_call_hits_cache(self):
        _fabric_jar(self.mods / "demo.jar")
        self._describe()
        with mock.patch.object(mod_info, "_inspect",
                               side_effect=AssertionError("cache miss")) as m:
            rows = self._describe()
        self.assertEqual(m.call_count, 0)
        self.assertEqual(rows[0]["name"], "Demo Mod")
        self.assertTrue(Path(rows[0]["icon"]).is_file())

    def test_cache_invalidated_on_change(self):
        p = _fabric_jar(self.mods / "demo.jar")
        self._describe()
        _fabric_jar(p, name="Renamed Mod")
        os.utime(p, ns=(p.stat().st_atime_ns + 10 ** 9, p.stat().st_mtime_ns + 10 ** 9))
        rows = self._describe()
        self.assertEqual(rows[0]["name"], "Renamed Mod")

    def test_stale_cache_entries_pruned(self):
        p = _fabric_jar(self.mods / "demo.jar")
        self._describe()
        p.unlink()
        _jar(self.mods / "other.jar", {"a.txt": "x"})
        self._describe()
        data = json.loads((self.cache / "meta.json").read_text("utf-8"))
        keys = list(data["mods"].keys())
        self.assertEqual(len(keys), 1)
        self.assertIn("other.jar", keys[0])

    def test_icon_missing_inner_path_gives_empty(self):
        # icon 字段指向的成员不存在：不抽取也不报错
        _fabric_jar(self.mods / "demo.jar", icon="does/not/exist.png", write_icon=False)
        row = self._describe()[0]
        self.assertEqual(row["icon"], "")

    def test_icon_path_traversal_rejected(self):
        self.assertEqual(
            mod_info._extract_icon(self.mods / "x.jar", "../evil.png", self.cache), "")

    def test_empty_dir(self):
        self.assertEqual(self._describe(), [])

    def test_corrupt_cache_file_recovers(self):
        _fabric_jar(self.mods / "demo.jar")
        self.cache.mkdir(parents=True, exist_ok=True)
        (self.cache / "meta.json").write_text("{oops", encoding="utf-8")
        rows = self._describe()
        self.assertEqual(rows[0]["name"], "Demo Mod")

    def test_long_description_truncated(self):
        _fabric_jar(self.mods / "demo.jar", description="x" * 5000)
        row = self._describe()[0]
        self.assertLessEqual(len(row["description"]), 1000)


if __name__ == "__main__":
    unittest.main()
