# -*- coding: utf-8 -*-
"""模组/整合包中文名数据库（HMCL mod_data.txt 同源）：解析、查找、搜索、标注。"""

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mclauncher import mod_translate as mt  # noqa: E402
from mclauncher import utils  # noqa: E402

SAMPLE = """#
# Hello Minecraft! Launcher
# mcmod.cn
#
industrial-craft;2;IC2,ic2;工业时代2;Industrial Craft 2;IC2
jei;25;jei;JEI物品管理器;Just Enough Items;JEI
sodium;35;sodium;钠;Sodium;
;99;;无来源模组;No Source Mod;NSM
badline;only;three
create;55;create;机械动力;Create;
"""

SAMPLE_PACK = """# comment
gt-new-horizons;1;;格雷科技：新视野;GT: New Horizons;GTNH
;3;;飞翔之路3;Flying Road 3;
"""


class _Isolated(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        self.addCleanup(self._td.cleanup)
        p = patch.object(utils, "ROOT", self.root)
        p.start()
        self.addCleanup(p.stop)
        # 清空模块级缓存，并掐掉后台预热线程
        mt._records.clear()
        mt._slug_index.clear()
        mt._modid_index.clear()
        mt._last_fail.clear()
        mt._warmed = True
        self.addCleanup(mt._records.clear)
        self.addCleanup(mt._slug_index.clear)
        self.addCleanup(mt._modid_index.clear)
        self.addCleanup(mt._last_fail.clear)

    def _write_cache(self, kind: str, text: str, age_sec: int = 0):
        f = mt._cache_file(kind)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, "utf-8")
        if age_sec:
            old = time.time() - age_sec
            os.utime(f, (old, old))
        return f

    def _load_sample(self):
        self._write_cache("mod", SAMPLE)
        return mt.load("mod", allow_network=False)


class ParseTests(unittest.TestCase):
    def test_skips_comments_and_malformed(self):
        recs = mt.parse(SAMPLE)
        self.assertEqual(len(recs), 5)
        self.assertEqual(recs[0]["curseforge"], "industrial-craft")
        self.assertEqual(recs[0]["mcmod"], "2")
        self.assertEqual(recs[0]["mod_ids"], ["IC2", "ic2"])
        self.assertEqual(recs[0]["name"], "工业时代2")
        self.assertEqual(recs[0]["subname"], "Industrial Craft 2")
        self.assertEqual(recs[0]["abbr"], "IC2")

    def test_empty_text(self):
        self.assertEqual(mt.parse(""), [])
        self.assertEqual(mt.parse(None), [])


class LabelTests(unittest.TestCase):
    def test_full_label(self):
        rec = mt.parse(SAMPLE)[0]
        self.assertEqual(mt.display_label(rec), "[IC2] 工业时代2 (Industrial Craft 2)")

    def test_no_abbr(self):
        rec = [r for r in mt.parse(SAMPLE) if r["name"] == "钠"][0]
        self.assertEqual(mt.display_label(rec), "钠 (Sodium)")

    def test_empty(self):
        self.assertEqual(mt.display_label(None), "")

    def test_wiki_url(self):
        recs = mt.parse(SAMPLE)
        self.assertEqual(mt.wiki_url(recs[0], "mod"), "https://www.mcmod.cn/class/2.html")
        packs = mt.parse(SAMPLE_PACK)
        self.assertEqual(mt.wiki_url(packs[0], "modpack"),
                         "https://www.mcmod.cn/modpack/1.html")
        self.assertEqual(mt.wiki_url({"mcmod": ""}, "mod"), "")


class HasCjkTests(unittest.TestCase):
    def test_cjk(self):
        self.assertTrue(mt.has_cjk("机械动力"))
        self.assertTrue(mt.has_cjk("sodium 钠"))
        self.assertFalse(mt.has_cjk("sodium"))
        self.assertFalse(mt.has_cjk(""))
        self.assertFalse(mt.has_cjk(None))


class LoadTests(_Isolated):
    def test_load_from_fresh_cache(self):
        self._write_cache("mod", SAMPLE)
        recs = mt.load("mod", allow_network=False)
        self.assertEqual(len(recs), 5)

    def test_load_fetches_and_caches(self):
        with patch.object(mt, "_fetch", return_value=SAMPLE) as fk:
            recs = mt.load("mod")
        self.assertEqual(len(recs), 5)
        self.assertEqual(fk.call_count, 1)
        # 第二次直接用内存缓存
        with patch.object(mt, "_fetch") as fk2:
            mt.load("mod")
        fk2.assert_not_called()

    def test_stale_cache_used_when_fetch_fails(self):
        self._write_cache("mod", SAMPLE, age_sec=10 * 24 * 3600)
        with patch.object(mt, "_fetch", side_effect=OSError("net down")):
            recs = mt.load("mod")
        self.assertEqual(len(recs), 5)

    def test_fail_backoff(self):
        calls = []

        def boom(kind, dm=None):
            calls.append(kind)
            raise OSError("net down")

        with patch.object(mt, "_fetch", side_effect=boom):
            self.assertEqual(mt.load("mod"), [])
            self.assertEqual(mt.load("mod"), [])  # 退避期内不再重试
        self.assertEqual(len(calls), 1)

    def test_unknown_kind(self):
        self.assertEqual(mt.load("shader"), [])


class LookupTests(_Isolated):
    def test_for_slug(self):
        self._load_sample()
        self.assertEqual(mt.for_slug("JEI")["name"], "JEI物品管理器")
        self.assertIsNone(mt.for_slug("nope"))
        self.assertIsNone(mt.for_slug(""))

    def test_for_modid(self):
        self._load_sample()
        self.assertEqual(mt.for_modid("ic2")["name"], "工业时代2")
        self.assertIsNone(mt.for_modid("unknown"))


class SearchTests(_Isolated):
    def test_cn_partial(self):
        self._load_sample()
        hits = mt.search_cn("工业")
        self.assertTrue(hits and hits[0]["curseforge"] == "industrial-craft")

    def test_abbr_exact(self):
        self._load_sample()
        hits = mt.search_cn("ic2")
        self.assertEqual(hits[0]["curseforge"], "industrial-craft")

    def test_subname(self):
        self._load_sample()
        hits = mt.search_cn("sodium")
        self.assertEqual(hits[0]["name"], "钠")

    def test_multi_token(self):
        self._load_sample()
        hits = mt.search_cn("机械 动力")
        self.assertEqual(hits[0]["curseforge"], "create")

    def test_no_match(self):
        self._load_sample()
        self.assertEqual(mt.search_cn("不存在的模组名"), [])
        self.assertEqual(mt.search_cn(""), [])

    def test_best_cn_match_requires_cjk(self):
        self._load_sample()
        self.assertIsNone(mt.best_cn_match("sodium"))
        rec = mt.best_cn_match("机械动力")
        self.assertEqual(rec["curseforge"], "create")

    def test_modpack_kind(self):
        self._write_cache("modpack", SAMPLE_PACK)
        mt.load("modpack", allow_network=False)
        rec = mt.best_cn_match("格雷科技", kind="modpack")
        self.assertEqual(rec["curseforge"], "gt-new-horizons")

    def test_punctuation_tolerant(self):
        """「格雷科技新视野」应匹配「格雷科技：新视野」（标点差异）。"""
        self._write_cache("modpack", SAMPLE_PACK)
        mt.load("modpack", allow_network=False)
        rec = mt.best_cn_match("格雷科技新视野", kind="modpack")
        self.assertEqual(rec["curseforge"], "gt-new-horizons")


class AnnotateTests(_Isolated):
    def test_annotates_matching_rows(self):
        self._load_sample()
        rows = [
            {"source": "curseforge", "slug": "jei", "title": "Just Enough Items"},
            {"source": "modrinth", "slug": "sodium", "title": "Sodium"},
            {"source": "modrinth", "slug": "unknown-mod", "title": "Unknown"},
        ]
        mt.annotate(rows, "mod")
        self.assertEqual(rows[0]["cn_name"], "JEI物品管理器")
        self.assertEqual(rows[0]["mcmod_url"], "https://www.mcmod.cn/class/25.html")
        self.assertEqual(rows[1]["cn_name"], "钠")
        self.assertNotIn("cn_name", rows[2])

    def test_skips_titles_already_chinese(self):
        self._load_sample()
        rows = [{"source": "curseforge", "slug": "jei", "title": "JEI中文版"}]
        mt.annotate(rows, "mod")
        self.assertNotIn("cn_name", rows[0])

    def test_dataset_not_loaded_is_noop(self):
        rows = [{"slug": "jei", "title": "JEI"}]
        self.assertIs(mt.annotate(rows, "mod"), rows)
        self.assertNotIn("cn_name", rows[0])


class CurseforgeIntegrationTests(_Isolated):
    """search_curseforge 的中文映射：先 slug 精确、再英文名全文。"""

    def setUp(self):
        super().setUp()
        self._load_sample()

    def test_cjk_query_translated_and_exact_slug_prepended(self):
        from mclauncher import mods
        seen = []

        def fake_cf_fetch(dm, path, api_key=None, params=None, timeout=None):
            seen.append(dict(params or {}))
            if "slug" in (params or {}):
                return {"data": [{"id": 1, "slug": "industrial-craft",
                                  "name": "Industrial Craft 2"}]}
            return {"data": [{"id": 9, "slug": "other", "name": "Other"}]}

        with patch.object(mods, "_cf_fetch", side_effect=fake_cf_fetch):
            hits = mods.search_curseforge(None, "工业时代2", limit=10)
        # 第一次全文搜用英文名，兜底再按 slug 精确查并置顶
        self.assertEqual(seen[0].get("searchFilter"), "Industrial Craft 2")
        self.assertEqual(seen[1].get("slug"), "industrial-craft")
        self.assertEqual(hits[0]["slug"], "industrial-craft")
        # 标注了中文名
        self.assertEqual(hits[0].get("cn_name"), "工业时代2")

    def test_english_query_untouched(self):
        from mclauncher import mods
        seen = []

        def fake_cf_fetch(dm, path, api_key=None, params=None, timeout=None):
            seen.append(dict(params or {}))
            return {"data": []}

        with patch.object(mods, "_cf_fetch", side_effect=fake_cf_fetch):
            mods.search_curseforge(None, "sodium", limit=10)
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0].get("searchFilter"), "sodium")


class ModrinthIntegrationTests(_Isolated):
    def test_cjk_query_translated(self):
        self._load_sample()
        from mclauncher import mods
        captured = {}

        class FakeDM:
            def fetch_json(self, url, params=None, timeout=None, expand=True):
                captured["params"] = dict(params or {})
                return {"hits": [{"slug": "sodium", "title": "Sodium",
                                  "author": "jellysquid"}]}

        rows = mods.search_mods(FakeDM(), "钠")
        self.assertEqual(captured["params"]["query"], "Sodium")
        self.assertEqual(rows[0].get("cn_name"), "钠")


class FacadeFieldTests(unittest.TestCase):
    def test_row_builders_pass_cn_fields(self):
        import inspect
        from app import backend as qt_backend
        from bridge import api as bridge_api
        for mod in (qt_backend, bridge_api):
            src = inspect.getsource(mod)
            self.assertGreaterEqual(src.count('"cn_name"'), 3, mod.__name__)
            self.assertGreaterEqual(src.count('"mcmod_url"'), 3, mod.__name__)


if __name__ == "__main__":
    unittest.main()
