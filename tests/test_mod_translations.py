# -*- coding: utf-8 -*-
"""中文模组名数据库测试（对标 PCL2 / HMCL 的 mcmod.cn 数据集）。"""
import sys
import time
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mclauncher import mod_translations as mt  # noqa: E402
from mclauncher import mods  # noqa: E402

SAMPLE = """#
# Hello Minecraft! Launcher
# mcmod.cn
create;2021;create;机械动力;Create;
createaddition;3437;createaddition;机械动力：创想附加;Create Crafts & Additions;CC&A
sodium;2785;sodium;钠;Sodium;
jei;459;jei,JEI;JEI物品管理器;Just Enough Items;JEI
;30;Forge,forge;Minecraft Forge;;Forge
;9;mod_EE;等价交换2;Equivalent Exchange 2;EE2
fake-eng;77;;English Name Only;English Mod;

;;;;;
"""
# 缓存与下载有 100KB 的完整性下限，测试样本补齐注释行
PADDED = SAMPLE + ("# padding\n" * 20000)


def _index_sample():
    mt._reset_for_tests()
    mt._build_index(mt.parse(SAMPLE))


class ParseTest(unittest.TestCase):
    def test_fields_and_skipping(self):
        recs = mt.parse(SAMPLE)
        # 注释、空行、六段全空的行都被跳过
        self.assertEqual(len(recs), 7)
        slug, mcmod_id, name_cn, name_en, abbr, modids = recs[0]
        self.assertEqual(slug, "create")
        self.assertEqual(mcmod_id, "2021")
        self.assertEqual(name_cn, "机械动力")
        self.assertEqual(name_en, "Create")
        self.assertEqual(abbr, "")
        self.assertEqual(modids, ("create",))

    def test_modids_split(self):
        recs = mt.parse(SAMPLE)
        jei = next(r for r in recs if r[0] == "jei")
        self.assertEqual(jei[5], ("jei", "JEI"))
        noid = next(r for r in recs if r[0] == "fake-eng")
        self.assertEqual(noid[5], ())

    def test_empty_slug_entry_kept(self):
        recs = mt.parse(SAMPLE)
        forge = next(r for r in recs if r[1] == "30")
        self.assertEqual(forge[0], "")
        self.assertEqual(forge[2], "Minecraft Forge")


class SearchTest(unittest.TestCase):
    def setUp(self):
        _index_sample()

    def tearDown(self):
        mt._reset_for_tests()

    def test_exact_before_prefix(self):
        out = mt.search_chinese("机械动力")
        self.assertEqual(out[0]["slug"], "create")
        self.assertEqual(out[1]["slug"], "createaddition")

    def test_exact_single_char(self):
        out = mt.search_chinese("钠")
        self.assertEqual(out[0]["slug"], "sodium")

    def test_abbr_match_case_insensitive(self):
        out = mt.search_chinese("JEI")
        self.assertTrue(any(r["slug"] == "jei" for r in out))
        out = mt.search_chinese("jei")
        self.assertTrue(any(r["slug"] == "jei" for r in out))

    def test_substring_match(self):
        out = mt.search_chinese("物品管理")
        self.assertEqual(out[0]["slug"], "jei")

    def test_no_match(self):
        self.assertEqual(mt.search_chinese("不存在的模组名"), [])

    def test_unloaded_returns_empty(self):
        mt._reset_for_tests()
        self.assertEqual(mt.search_chinese("机械动力"), [])


class AnnotateTest(unittest.TestCase):
    def setUp(self):
        _index_sample()

    def tearDown(self):
        mt._reset_for_tests()

    def test_annotate_by_slug_and_title(self):
        rows = [
            {"slug": "create", "title": "Create"},
            {"slug": "", "title": "Sodium"},          # 按英文名兜底
            {"slug": "unknown", "title": "Unknown"},  # 不在数据集
        ]
        mt.annotate_hits(rows)
        self.assertEqual(rows[0]["name_cn"], "机械动力")
        self.assertEqual(rows[0]["mcmod_url"], "https://www.mcmod.cn/class/2021.html")
        self.assertEqual(rows[1]["name_cn"], "钠")
        self.assertNotIn("name_cn", rows[2])
        self.assertNotIn("mcmod_url", rows[2])

    def test_non_cjk_translation_not_shown(self):
        # 「译名」没有中文（如 English Name Only）时不注 name_cn，但百科链接照给
        rows = [{"slug": "fake-eng", "title": "English Mod"}]
        mt.annotate_hits(rows)
        self.assertNotIn("name_cn", rows[0])
        self.assertEqual(rows[0]["mcmod_url"], "https://www.mcmod.cn/class/77.html")

    def test_unloaded_triggers_warmup_only(self):
        mt._reset_for_tests()
        rows = [{"slug": "create", "title": "Create"}]
        with mock.patch.object(mt, "load_async") as warm:
            mt.annotate_hits(rows)
        warm.assert_called_once()
        self.assertNotIn("name_cn", rows[0])

    def test_mcmod_url_validation(self):
        self.assertEqual(mt.mcmod_url("2021"), "https://www.mcmod.cn/class/2021.html")
        self.assertEqual(mt.mcmod_url(""), "")
        self.assertEqual(mt.mcmod_url("abc"), "")

    def test_has_cjk(self):
        self.assertTrue(mt.has_cjk("机械动力"))
        self.assertTrue(mt.has_cjk("Create 机械"))
        self.assertFalse(mt.has_cjk("Create"))
        self.assertFalse(mt.has_cjk(""))
        self.assertFalse(mt.has_cjk(None))


class LocalModsTest(unittest.TestCase):
    """本地已装模组译名（HMCL 模组列表同款：modid / 英文名双重匹配）。"""

    def setUp(self):
        _index_sample()

    def tearDown(self):
        mt._reset_for_tests()

    def test_lookup_by_modid_case_insensitive(self):
        self.assertEqual(mt.lookup_local(modid="JEI")["slug"], "jei")
        self.assertEqual(mt.lookup_local(modid="mod_EE")["mcmod_id"], "9")

    def test_lookup_falls_back_to_english_name(self):
        rec = mt.lookup_local(modid="not-in-dataset", name="Sodium")
        self.assertEqual(rec["slug"], "sodium")
        self.assertIsNone(mt.lookup_local(modid="nope", name="Nope"))

    def test_annotate_local_rows(self):
        rows = [
            {"filename": "jei-1.0.jar", "id": "jei", "mod_name": "Just Enough Items"},
            {"filename": "unknown.jar", "id": "totally-unknown"},
            {"filename": "plain.jar"},
        ]
        mt.annotate_local_mods(rows)
        self.assertEqual(rows[0]["name_cn"], "JEI物品管理器")
        self.assertEqual(rows[0]["mcmod_url"], "https://www.mcmod.cn/class/459.html")
        self.assertNotIn("name_cn", rows[1])
        self.assertNotIn("name_cn", rows[2])

    def test_non_cjk_translation_link_only(self):
        # Minecraft Forge 的「译名」没有中文 -> 不注 name_cn，百科链接照给
        rows = [{"id": "forge", "filename": "forge.jar"}]
        mt.annotate_local_mods(rows)
        self.assertNotIn("name_cn", rows[0])
        self.assertEqual(rows[0]["mcmod_url"], "https://www.mcmod.cn/class/30.html")

    def test_unloaded_triggers_warmup_only(self):
        mt._reset_for_tests()
        rows = [{"id": "jei"}]
        with mock.patch.object(mt, "load_async") as warm:
            mt.annotate_local_mods(rows)
        warm.assert_called_once()
        self.assertNotIn("name_cn", rows[0])


class DetailedListTest(unittest.TestCase):
    """mods.list_mod_entries_at(detailed=True)：jar 元数据 + 译名注解。"""

    def setUp(self):
        _index_sample()
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        mods._jar_meta_cache.clear()

    def tearDown(self):
        self.tmp.cleanup()
        mt._reset_for_tests()
        mods._jar_meta_cache.clear()

    def _fabric_jar(self, name, modid, title, version="1.2.3"):
        import json
        import zipfile
        with zipfile.ZipFile(self.dir / name, "w") as zf:
            zf.writestr("fabric.mod.json",
                        json.dumps({"id": modid, "name": title, "version": version}))

    def _forge_jar(self, name, modid, title, version="4.5.6"):
        import zipfile
        toml = ('modLoader="javafml"\n[[mods]]\n'
                f'modId="{modid}"\ndisplayName="{title}"\nversion="{version}"\n')
        with zipfile.ZipFile(self.dir / name, "w") as zf:
            zf.writestr("META-INF/mods.toml", toml)

    def test_detailed_fields_and_translation(self):
        self._fabric_jar("jei-fabric.jar", "jei", "Just Enough Items")
        self._forge_jar("create.jar.disabled", "create", "Create")
        (self.dir / "plain.jar").write_bytes(b"not a zip")
        rows = mods.list_mod_entries_at(self.dir, detailed=True)
        by_name = {r["filename"]: r for r in rows}
        jei = by_name["jei-fabric.jar"]
        self.assertEqual(jei["id"], "jei")
        self.assertEqual(jei["mod_name"], "Just Enough Items")
        self.assertEqual(jei["mod_version"], "1.2.3")
        self.assertEqual(jei["loader"], "fabric")
        self.assertEqual(jei["name_cn"], "JEI物品管理器")
        self.assertEqual(jei["mcmod_url"], "https://www.mcmod.cn/class/459.html")
        create = by_name["create.jar.disabled"]
        self.assertFalse(create["enabled"])
        self.assertEqual(create["id"], "create")   # modid == 文件名主干也要保留
        self.assertEqual(create["loader"], "forge")
        self.assertEqual(create["name_cn"], "机械动力")
        plain = by_name["plain.jar"]
        self.assertNotIn("mod_name", plain)   # 解析失败静默退回文件名显示
        self.assertNotIn("name_cn", plain)

    def test_placeholder_version_hidden(self):
        self._forge_jar("w.jar", "w", "W", version="${file.jarVersion}")
        rows = mods.list_mod_entries_at(self.dir, detailed=True)
        self.assertNotIn("mod_version", rows[0])
        self.assertEqual(rows[0]["mod_name"], "W")

    def test_meta_cached_by_size_mtime(self):
        self._fabric_jar("jei-fabric.jar", "jei", "Just Enough Items")
        mods.list_mod_entries_at(self.dir, detailed=True)
        with mock.patch("mclauncher.ai.conflict.inspect_jar") as insp:
            rows = mods.list_mod_entries_at(self.dir, detailed=True)
        insp.assert_not_called()
        self.assertEqual(rows[0]["mod_name"], "Just Enough Items")

    def test_plain_listing_stays_cheap(self):
        self._fabric_jar("jei-fabric.jar", "jei", "Just Enough Items")
        with mock.patch("mclauncher.ai.conflict.inspect_jar") as insp:
            rows = mods.list_mod_entries_at(self.dir)
        insp.assert_not_called()
        self.assertNotIn("mod_name", rows[0])


class _FetchDM:
    def __init__(self, text=None, err=None):
        self.text = text
        self.err = err
        self.calls = 0

    def fetch_text(self, url, **_kw):
        self.calls += 1
        if self.err:
            raise self.err
        return self.text


class LoadTest(unittest.TestCase):
    def setUp(self):
        mt._reset_for_tests()
        self.tmp = tempfile.TemporaryDirectory()
        self.cache = Path(self.tmp.name) / "mod_data.txt"
        self.patcher = mock.patch.object(mt, "cache_file", lambda: self.cache)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.tmp.cleanup()
        mt._reset_for_tests()

    def test_fresh_cache_no_network(self):
        self.cache.write_text(PADDED, encoding="utf-8")
        dm = _FetchDM(err=RuntimeError("must not be called"))
        self.assertTrue(mt.load(dm))
        self.assertEqual(dm.calls, 0)
        self.assertEqual(mt.search_chinese("钠")[0]["slug"], "sodium")

    def test_stale_cache_refetches_and_rewrites(self):
        self.cache.write_text(PADDED, encoding="utf-8")
        old = time.time() - 8 * 24 * 3600
        import os
        os.utime(self.cache, (old, old))
        v2 = PADDED.replace("机械动力;Create", "机械动力2;Create")
        dm = _FetchDM(text=v2)
        self.assertTrue(mt.load(dm))
        self.assertEqual(dm.calls, 1)
        self.assertIn("机械动力2", self.cache.read_text(encoding="utf-8"))

    def test_fetch_failure_falls_back_to_stale_cache(self):
        self.cache.write_text(PADDED, encoding="utf-8")
        old = time.time() - 30 * 24 * 3600
        import os
        os.utime(self.cache, (old, old))
        self.assertTrue(mt.load(_FetchDM(err=RuntimeError("offline"))))
        self.assertTrue(mt.loaded())

    def test_no_cache_no_network_degrades(self):
        dm = _FetchDM(err=RuntimeError("offline"))
        self.assertFalse(mt.load(dm))
        # 本进程内不再重试
        self.assertFalse(mt.load(_FetchDM(text=PADDED)))
        self.assertEqual(dm.calls, 1)

    def test_truncated_download_rejected(self):
        # 半截响应（代理截断）不落缓存、不建索引
        self.assertFalse(mt.load(_FetchDM(text="# mcmod\ncreate;1;;机械;C;\n")))
        self.assertFalse(self.cache.exists())


class DatasetHitsTest(unittest.TestCase):
    """mods._dataset_hits: 中文名 → Modrinth 批量 + CurseForge slug 兜底。"""

    def setUp(self):
        _index_sample()

    def tearDown(self):
        mt._reset_for_tests()

    def _dm(self):
        dm = mock.Mock()
        dm.fetch_json.return_value = [
            {"slug": "create", "title": "Create", "downloads": 5,
             "description": "gears", "icon_url": "i.png"},
        ]
        return dm

    def test_bulk_modrinth_then_cf_fallback(self):
        cf_obj = {"id": 11, "slug": "createaddition",
                  "name": "Create Crafts & Additions",
                  "authors": [{"name": "mrh"}], "downloadCount": 3,
                  "summary": "wires", "categories": []}
        with mock.patch.object(mt, "load", return_value=True), \
                mock.patch.object(mods, "cf_by_slug", return_value=cf_obj) as cf:
            hits = mods._dataset_hits(self._dm(), "机械动力")
        self.assertEqual(hits[0]["source"], "modrinth")
        self.assertEqual(hits[0]["slug"], "create")
        self.assertEqual(hits[0]["name_cn"], "机械动力")
        self.assertTrue(hits[0]["matched_alias"])
        self.assertEqual(hits[0]["mcmod_url"], "https://www.mcmod.cn/class/2021.html")
        self.assertEqual(hits[1]["source"], "curseforge")
        self.assertEqual(hits[1]["name_cn"], "机械动力：创想附加")
        cf.assert_called_once()

    def test_sources_modrinth_only_skips_cf(self):
        with mock.patch.object(mt, "load", return_value=True), \
                mock.patch.object(mods, "cf_by_slug") as cf:
            hits = mods._dataset_hits(self._dm(), "机械动力",
                                      sources=("modrinth",))
        cf.assert_not_called()
        self.assertEqual([h["source"] for h in hits], ["modrinth"])

    def test_sources_cf_only_skips_bulk(self):
        dm = mock.Mock()
        cf_obj = {"id": 7, "slug": "create", "name": "Create", "authors": [],
                  "downloadCount": 1, "summary": "", "categories": []}
        with mock.patch.object(mt, "load", return_value=True), \
                mock.patch.object(mods, "cf_by_slug", return_value=cf_obj):
            hits = mods._dataset_hits(dm, "机械动力", sources=("curseforge",))
        dm.fetch_json.assert_not_called()
        self.assertTrue(hits)
        self.assertTrue(all(h["source"] == "curseforge" for h in hits))

    def test_dataset_unavailable_returns_empty(self):
        with mock.patch.object(mt, "load", return_value=False):
            self.assertEqual(mods._dataset_hits(self._dm(), "机械动力"), [])


class ChineseSearchRouteTest(unittest.TestCase):
    """search_mods_chinese: 别名 miss 后走数据集，再走全文回退。"""

    def setUp(self):
        _index_sample()

    def tearDown(self):
        mt._reset_for_tests()

    def test_dataset_step_used_after_alias_miss(self):
        hit = {"source": "modrinth", "slug": "create", "title": "Create",
               "name_cn": "机械动力", "matched_alias": True}
        with mock.patch("mclauncher.catalog.lookup_mod_alias",
                        return_value=(None, None, None)), \
                mock.patch("mclauncher.catalog.fuzzy_match_mod", return_value=[]), \
                mock.patch.object(mods, "_dataset_hits", return_value=[hit]) as ds, \
                mock.patch.object(mods, "search_mods") as full:
            out = mods.search_mods_chinese(mock.Mock(), "机械动力")
        ds.assert_called_once()
        full.assert_not_called()
        self.assertEqual(out, [hit])

    def test_fulltext_fallback_respects_sources(self):
        with mock.patch("mclauncher.catalog.lookup_mod_alias",
                        return_value=(None, None, None)), \
                mock.patch("mclauncher.catalog.fuzzy_match_mod", return_value=[]), \
                mock.patch.object(mods, "_dataset_hits", return_value=[]), \
                mock.patch.object(mods, "search_mods", return_value=[]) as mr, \
                mock.patch.object(mods, "search_curseforge", return_value=[]) as cf:
            mods.search_mods_chinese(mock.Mock(), "冷门模组名",
                                     sources=("curseforge",))
        mr.assert_not_called()
        cf.assert_called_once()

    def test_alias_hits_filtered_by_source_then_next_step(self):
        # 别名只命中 Modrinth，但用户选了 CurseForge：别名结果被过滤，继续走数据集
        mr_hit = {"source": "modrinth", "slug": "sodium", "title": "Sodium"}
        cf_hit = {"source": "curseforge", "id": 1, "title": "Sodium"}
        with mock.patch("mclauncher.catalog.lookup_mod_alias",
                        return_value=("sodium", None, "Sodium")), \
                mock.patch.object(mods, "_alias_to_modrinth_hits",
                                  return_value=[mr_hit]), \
                mock.patch("mclauncher.catalog.fuzzy_match_mod", return_value=[]), \
                mock.patch.object(mods, "_dataset_hits",
                                  return_value=[cf_hit]) as ds:
            out = mods.search_mods_chinese(mock.Mock(), "钠",
                                           sources=("curseforge",))
        ds.assert_called_once()
        self.assertEqual(out, [cf_hit])


PACK_SAMPLE = """#
# mcmod.cn
gt-new-horizons;1;;格雷科技：新视野;GT: New Horizons;GTNH
skyfactory-4;5;;天空工厂4;SkyFactory 4;SF4
rlcraft;16;;超现实 x 虚拟生存;RLCraft;RLC
all-the-mods-9;900;;All The Mods 9;;ATM9
"""


def _index_pack_sample():
    mt._build_pack_index(mt.parse(PACK_SAMPLE))


class PackDatasetTest(unittest.TestCase):
    def setUp(self):
        mt._reset_for_tests()
        _index_pack_sample()

    def tearDown(self):
        mt._reset_for_tests()

    def test_search_and_abbr(self):
        out = mt.search_packs_chinese("天空工厂")
        self.assertEqual(out[0]["slug"], "skyfactory-4")
        out = mt.search_packs_chinese("gtnh")
        self.assertEqual(out[0]["slug"], "gt-new-horizons")

    def test_annotate_pack_rows_uses_modpack_url(self):
        rows = [{"slug": "rlcraft", "title": "RLCraft"},
                {"slug": "all-the-mods-9", "title": "All The Mods 9"}]
        mt.annotate_pack_hits(rows)
        self.assertEqual(rows[0]["name_cn"], "超现实 x 虚拟生存")
        self.assertEqual(rows[0]["mcmod_url"], "https://www.mcmod.cn/modpack/16.html")
        # 「译名」没有中文时不注 name_cn，百科链接照给
        self.assertNotIn("name_cn", rows[1])
        self.assertEqual(rows[1]["mcmod_url"], "https://www.mcmod.cn/modpack/900.html")

    def test_pack_unloaded_triggers_warmup_only(self):
        mt._reset_for_tests()
        rows = [{"slug": "rlcraft", "title": "RLCraft"}]
        with mock.patch.object(mt, "load_packs_async") as warm:
            mt.annotate_pack_hits(rows)
        warm.assert_called_once()
        self.assertNotIn("name_cn", rows[0])

    def test_mod_and_pack_datasets_independent(self):
        # 只建了整合包索引：模组接口未加载、整合包接口可用
        self.assertFalse(mt.loaded())
        self.assertTrue(mt.packs_loaded())
        self.assertEqual(mt.search_chinese("天空工厂"), [])


class PackDatasetHitsTest(unittest.TestCase):
    """modpack._pack_dataset_hits: 中文名 → Modrinth 批量 + CF slug 兜底 + 筛选。"""

    def setUp(self):
        mt._reset_for_tests()
        _index_pack_sample()
        from mclauncher import modpack
        self.modpack = modpack

    def tearDown(self):
        mt._reset_for_tests()

    def _dm(self, gvs=("1.20.1",)):
        dm = mock.Mock()
        dm.fetch_json.return_value = [
            {"slug": "skyfactory-4", "title": "SkyFactory 4", "downloads": 7,
             "description": "sky", "icon_url": "", "game_versions": list(gvs),
             "categories": []},
        ]
        return dm

    def test_bulk_then_cf_fallback(self):
        cf_obj = {"id": 285109, "slug": "rlcraft", "name": "RLCraft",
                  "authors": [{"name": "Shivaxi"}], "downloadCount": 9,
                  "summary": "hard", "categories": [],
                  "latestFilesIndexes": [{"gameVersion": "1.12.2"}]}
        with mock.patch.object(mt, "load_packs", return_value=True), \
                mock.patch("mclauncher.mods.cf_by_slug", return_value=cf_obj):
            hits = self.modpack._pack_dataset_hits(self._dm(), "工厂")
        # 「工厂」只包含命中 skyfactory-4（天空工厂4）
        self.assertTrue(any(h["slug"] == "skyfactory-4" for h in hits))
        sf = next(h for h in hits if h["slug"] == "skyfactory-4")
        self.assertEqual(sf["name_cn"], "天空工厂4")
        self.assertEqual(sf["mcmod_url"], "https://www.mcmod.cn/modpack/5.html")
        self.assertTrue(sf["matched_alias"])
        self.assertIn("MC 1.20.1", sf["description"])

    def test_game_version_filter_drops_mismatch(self):
        with mock.patch.object(mt, "load_packs", return_value=True):
            hits = self.modpack._pack_dataset_hits(
                self._dm(gvs=("1.20.1",)), "天空工厂", game_version="1.12.2")
        self.assertEqual(hits, [])

    def test_dataset_unavailable_returns_empty(self):
        with mock.patch.object(mt, "load_packs", return_value=False):
            self.assertEqual(self.modpack._pack_dataset_hits(self._dm(), "天空工厂"), [])


class PackChineseSearchRouteTest(unittest.TestCase):
    """search_modpacks_chinese: 别名 miss → 数据集 → 全文回退。"""

    def setUp(self):
        mt._reset_for_tests()
        from mclauncher import modpack
        self.modpack = modpack

    def tearDown(self):
        mt._reset_for_tests()

    def test_dataset_step_used_after_alias_miss(self):
        hit = {"source": "modrinth", "slug": "skyfactory-4", "title": "SkyFactory 4",
               "name_cn": "天空工厂4", "matched_alias": True}
        with mock.patch("mclauncher.catalog.lookup_modpack_alias",
                        return_value=(None, None, None)), \
                mock.patch.object(self.modpack, "_pack_dataset_hits",
                                  return_value=[hit]) as ds, \
                mock.patch.object(self.modpack, "modrinth_search") as full:
            out = self.modpack.search_modpacks_chinese(mock.Mock(), "天空工厂")
        ds.assert_called_once()
        full.assert_not_called()
        self.assertEqual(out, [hit])

    def test_fulltext_fallback_when_dataset_empty(self):
        with mock.patch("mclauncher.catalog.lookup_modpack_alias",
                        return_value=(None, None, None)), \
                mock.patch.object(self.modpack, "_pack_dataset_hits",
                                  return_value=[]), \
                mock.patch.object(self.modpack, "modrinth_search",
                                  return_value=[]) as mr, \
                mock.patch.object(self.modpack, "search_cf_modpacks",
                                  return_value=[]) as cf:
            self.modpack.search_modpacks_chinese(mock.Mock(), "冷门整合包")
        mr.assert_called_once()
        cf.assert_called_once()


class BridgeRouteTest(unittest.TestCase):
    def test_cjk_query_routes_to_chinese_search(self):
        try:
            from bridge.api import BackendAPI
        except Exception as e:  # 环境缺依赖时跳过，不算失败
            self.skipTest(f"bridge.api 不可导入: {e}")
        import types
        fake = types.SimpleNamespace(
            _mod_cache=[], _search_offset=BackendAPI._search_offset)
        hit = {"source": "modrinth", "slug": "create", "title": "Create",
               "name_cn": "机械动力", "mcmod_url": "https://www.mcmod.cn/class/2021.html",
               "downloads": 9, "description": "gears"}
        with mock.patch("mclauncher.mods.search_mods_chinese",
                        return_value=[hit]) as zh, \
                mock.patch("mclauncher.mods.search_mods") as full:
            rows = BackendAPI.search_mods(fake, "机械动力", "Modrinth")
        zh.assert_called_once()
        self.assertEqual(zh.call_args.kwargs.get("sources"), ("modrinth",))
        full.assert_not_called()
        self.assertEqual(rows[0]["name_cn"], "机械动力")
        self.assertEqual(rows[0]["mcmod_url"], "https://www.mcmod.cn/class/2021.html")

    def test_cjk_offset_page_two_empty(self):
        try:
            from bridge.api import BackendAPI
        except Exception as e:
            self.skipTest(f"bridge.api 不可导入: {e}")
        import types
        fake = types.SimpleNamespace(
            _mod_cache=[], _search_offset=BackendAPI._search_offset)
        with mock.patch("mclauncher.mods.search_mods_chinese") as zh:
            rows = BackendAPI.search_mods(fake, "机械动力", "Modrinth",
                                          {"offset": 30})
        zh.assert_not_called()
        self.assertEqual(rows, [])


if __name__ == "__main__":
    unittest.main()
