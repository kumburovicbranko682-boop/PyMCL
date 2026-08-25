# -*- coding: utf-8 -*-
"""mcmod.cn 中文数据库：解析 / 查询 / 搜索 / 注释（对齐 PCL2 / HMCL）。"""
from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from mclauncher import mod_translations as trdb
from mclauncher import utils

FIXTURE = """#
# mcmod.cn
industrial-craft;2;IC2,ic2;工业时代2;Industrial Craft 2;IC2
create;533;create;机械动力;Create;
;7;slotless;无槽位模组;;SM
twilight-forest;39;twilightforest;暮色森林;The Twilight Forest;TF
bad-line-only-4;x;y
jei;25;jei;JEI物品管理器;Just Enough Items;JEI
"""

PACK_FIXTURE = """# comment
better-mc;10001;;更好的我的世界;Better MC;BMC
"""


class Sandbox(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        patcher = mock.patch.object(utils, "ROOT", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)
        trdb._cache.clear()
        self.addCleanup(trdb._cache.clear)

    def write_db(self, kind="mod", text=FIXTURE):
        path = trdb.data_path(kind)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path


class TestParse(Sandbox):
    def test_parse_skips_comments_and_bad_lines(self):
        self.write_db()
        data = trdb._load("mod")
        self.assertEqual(len(data["records"]), 5)
        rec = data["by_slug"]["industrial-craft"]
        self.assertEqual(rec["name"], "工业时代2")
        self.assertEqual(rec["mod_ids"], ["IC2", "ic2"])
        self.assertEqual(rec["mcmod"], "2")

    def test_no_data_file(self):
        self.assertIsNone(trdb._load("mod"))
        self.assertEqual(trdb.search("工业"), [])
        self.assertIsNone(trdb.lookup_by_slug("create"))
        self.assertFalse(trdb.available("mod"))

    def test_reload_on_mtime_change(self):
        import os
        path = self.write_db()
        self.assertEqual(len(trdb._load("mod")["records"]), 5)
        path.write_text("only-one;1;m1;唯一;Only One;\n", encoding="utf-8")
        os.utime(path, (path.stat().st_atime, path.stat().st_mtime + 10))
        self.assertEqual(len(trdb._load("mod")["records"]), 1)


class TestCleanSubname(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(trdb.clean_subname("Industrial Craft 2"), "IndustrialCraft2")
        self.assertEqual(trdb.clean_subname("Create: Astral"), "CreateAstral")
        self.assertEqual(trdb.clean_subname("a.b+c\\d"), "a.b+c\\d")
        # 含不支持的字符时整个弃用
        self.assertEqual(trdb.clean_subname("日本語Mod"), "")
        self.assertEqual(trdb.clean_subname(""), "")


class TestLookup(Sandbox):
    def test_lookup_by_slug(self):
        self.write_db()
        self.assertEqual(trdb.lookup_by_slug("Industrial-Craft")["name"], "工业时代2")
        self.assertIsNone(trdb.lookup_by_slug("no-such"))
        self.assertIsNone(trdb.lookup_by_slug(""))

    def test_lookup_local_subname_first(self):
        self.write_db()
        rec = trdb.lookup_local("ic2", "Industrial Craft 2")
        self.assertEqual(rec["name"], "工业时代2")
        # 英文名命中但 modid 不匹配时回退按 modid 查
        rec2 = trdb.lookup_local("create", "Industrial Craft 2")
        self.assertEqual(rec2["name"], "机械动力")
        # 只有 modid
        self.assertEqual(trdb.lookup_local("twilightforest", "")["name"], "暮色森林")
        self.assertIsNone(trdb.lookup_local("unknown", "Unknown Mod"))

    def test_mcmod_url(self):
        self.write_db()
        rec = trdb.lookup_by_slug("industrial-craft")
        self.assertEqual(trdb.mcmod_url(rec), "https://www.mcmod.cn/class/2.html")
        self.write_db("modpack", PACK_FIXTURE)
        pk = trdb.lookup_by_slug("better-mc", "modpack")
        self.assertEqual(trdb.mcmod_url(pk, "modpack"),
                         "https://www.mcmod.cn/modpack/10001.html")

    def test_display_name(self):
        self.write_db()
        rec = trdb.lookup_by_slug("industrial-craft")
        self.assertEqual(trdb.display_name(rec), "[IC2] 工业时代2 (Industrial Craft 2)")


class TestSearch(Sandbox):
    def test_exact_and_partial(self):
        self.write_db()
        self.assertEqual(trdb.search("工业时代2")[0]["curseforge"], "industrial-craft")
        self.assertEqual(trdb.search("机械动力")[0]["curseforge"], "create")
        # 子串
        self.assertEqual(trdb.search("暮色")[0]["curseforge"], "twilight-forest")

    def test_spaces_removed_and_fuzzy(self):
        self.write_db()
        self.assertEqual(trdb.search("工业 时代2")[0]["curseforge"], "industrial-craft")
        # LCS 容错：差 3 个字符以内仍命中（HMCL 同款阈值）
        hits = trdb.search("工业时代2mod")
        self.assertTrue(any(r["curseforge"] == "industrial-craft" for r in hits))

    def test_abbr_and_english(self):
        self.write_db()
        self.assertTrue(any(r["name"] == "暮色森林" for r in trdb.search("TF")))
        self.assertTrue(any(r["curseforge"] == "jei"
                            for r in trdb.search("Just Enough Items")))

    def test_no_hit(self):
        self.write_db()
        self.assertEqual(trdb.search("完全不存在的模组名称九九九"), [])
        self.assertEqual(trdb.search(""), [])

    def test_contains_cjk(self):
        self.assertTrue(trdb.contains_cjk("机械动力"))
        self.assertTrue(trdb.contains_cjk("create 机械"))
        self.assertFalse(trdb.contains_cjk("create"))
        self.assertFalse(trdb.contains_cjk(""))


class TestEnsureData(Sandbox):
    def test_download_via_dm(self):
        dm = mock.Mock()
        dm.fetch_text.return_value = FIXTURE
        path = trdb.ensure_data("mod", dm)
        self.assertTrue(path.is_file())
        self.assertIn("工业时代2", path.read_text(encoding="utf-8"))
        # 新鲜缓存不再触发下载
        dm.fetch_text.reset_mock()
        trdb.ensure_data("mod", dm)
        dm.fetch_text.assert_not_called()

    def test_failure_keeps_stale(self):
        import os
        path = self.write_db()
        os.utime(path, (1, 1))  # 远古 mtime，触发刷新
        dm = mock.Mock()
        dm.fetch_text.side_effect = OSError("offline")
        with mock.patch("requests.get", side_effect=OSError("offline")):
            got = trdb.ensure_data("mod", dm)
        self.assertEqual(got, path)
        self.assertIn("工业时代2", path.read_text(encoding="utf-8"))

    def test_failure_without_cache(self):
        dm = mock.Mock()
        dm.fetch_text.side_effect = OSError("offline")
        with mock.patch("requests.get", side_effect=OSError("offline")):
            self.assertIsNone(trdb.ensure_data("mod", dm))

    def test_garbage_response_not_written(self):
        dm = mock.Mock()
        dm.fetch_text.return_value = "<html>404 page</html>"
        self.assertIsNone(trdb.ensure_data("mod", dm))
        self.assertFalse(trdb.data_path("mod").is_file())


class TestAnnotate(Sandbox):
    def test_annotate_hits(self):
        self.write_db()
        hits = [
            {"slug": "create", "title": "Create", "source": "modrinth"},
            {"slug": "unknown-mod", "title": "X", "source": "modrinth"},
            {"id": 123, "title": "no slug"},
        ]
        trdb.annotate_hits(hits, "mod")
        self.assertEqual(hits[0]["chinese_name"], "机械动力")
        self.assertEqual(hits[0]["mcmod_url"], "https://www.mcmod.cn/class/533.html")
        self.assertNotIn("chinese_name", hits[1])
        self.assertNotIn("chinese_name", hits[2])

    def test_annotate_without_db_is_noop(self):
        hits = [{"slug": "create", "title": "Create"}]
        trdb.annotate_hits(hits, "mod")
        self.assertNotIn("chinese_name", hits[0])


def make_fabric_jar(path: Path, mod_id="ic2", name="Industrial Craft 2"):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("fabric.mod.json", json.dumps(
            {"id": mod_id, "name": name, "version": "1.0.0"}))


class TestInstalledAnnotation(Sandbox):
    def test_annotate_installed_mods(self):
        from mclauncher import mods as mods_mod
        mods_mod._annotate_cache.clear()
        self.addCleanup(mods_mod._annotate_cache.clear)
        self.write_db()
        mods_dir = self.root / "mods"
        mods_dir.mkdir()
        make_fabric_jar(mods_dir / "ic2.jar")
        make_fabric_jar(mods_dir / "unknown.jar", "unknownmod", "Unknown Mod")
        rows = mods_mod.list_mod_entries_at(mods_dir)
        mods_mod.annotate_installed_mods(rows, mods_dir)
        by_name = {r["filename"]: r for r in rows}
        self.assertEqual(by_name["ic2.jar"]["chinese_name"], "工业时代2")
        self.assertEqual(by_name["ic2.jar"]["mcmod_url"],
                         "https://www.mcmod.cn/class/2.html")
        self.assertNotIn("chinese_name", by_name["unknown.jar"])

    def test_noop_without_db(self):
        from mclauncher import mods as mods_mod
        mods_mod._annotate_cache.clear()
        self.addCleanup(mods_mod._annotate_cache.clear)
        mods_dir = self.root / "mods"
        mods_dir.mkdir()
        make_fabric_jar(mods_dir / "ic2.jar")
        rows = mods_mod.list_mod_entries_at(mods_dir)
        mods_mod.annotate_installed_mods(rows, mods_dir)
        self.assertNotIn("chinese_name", rows[0])


class TestChineseSearchIntegration(Sandbox):
    def test_db_step_resolves_via_modrinth(self):
        """别名目录未命中时，mcmod 数据库应把中文名解析成可安装的搜索结果。"""
        from mclauncher import mods as mods_mod
        self.write_db()
        dm = mock.Mock()
        canned = [{"source": "modrinth", "slug": "twilight-forest",
                   "title": "The Twilight Forest", "author": "?",
                   "downloads": 9, "description": "", "icon_url": ""}]
        with mock.patch.object(mods_mod, "_alias_to_modrinth_hits",
                               return_value=list(canned)) as mr, \
             mock.patch("mclauncher.catalog.lookup_mod_alias",
                        return_value=(None, None, None)), \
             mock.patch("mclauncher.catalog.fuzzy_match_mod", return_value=[]), \
             mock.patch.object(trdb, "ensure_data", return_value=trdb.data_path("mod")):
            hits = mods_mod.search_mods_chinese(dm, "暮色森林")
        self.assertTrue(hits)
        top = hits[0]
        self.assertEqual(top["slug"], "twilight-forest")
        self.assertEqual(top["chinese_name"], "暮色森林")
        self.assertEqual(top["mcmod_url"], "https://www.mcmod.cn/class/39.html")
        self.assertTrue(top["matched_alias"])
        mr.assert_called()


class TestBridgeFacade(Sandbox):
    def test_cjk_query_routes_to_chinese_search(self):
        self.write_db()
        from bridge.api import BackendAPI
        from mclauncher import mods as mods_mod
        api = BackendAPI.__new__(BackendAPI)
        canned = [{"source": "modrinth", "slug": "create", "title": "Create",
                   "author": "simibubi", "downloads": 1, "description": "d"}]
        with mock.patch.object(mods_mod, "search_mods_chinese",
                               return_value=list(canned)) as zh, \
             mock.patch.object(mods_mod, "search_mods") as full:
            rows = api.search_mods("机械动力", "modrinth")
        zh.assert_called_once()
        full.assert_not_called()
        self.assertEqual(rows[0]["slug"], "create")
        self.assertEqual(rows[0]["chinese_name"], "机械动力")
        self.assertEqual(rows[0]["mcmod_url"], "https://www.mcmod.cn/class/533.html")

    def test_english_query_keeps_fulltext(self):
        from bridge.api import BackendAPI
        from mclauncher import mods as mods_mod
        api = BackendAPI.__new__(BackendAPI)
        with mock.patch.object(mods_mod, "search_mods_chinese") as zh, \
             mock.patch.object(mods_mod, "search_mods", return_value=[]) as full:
            api.search_mods("sodium", "modrinth")
        zh.assert_not_called()
        full.assert_called_once()


if __name__ == "__main__":
    unittest.main()
