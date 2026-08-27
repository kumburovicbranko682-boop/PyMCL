# -*- coding: utf-8 -*-
"""资源信息刷新相关的纯单元测试（不依赖外网）。

覆盖：年式版本号解析、Java 25 映射、NeoForge 前缀推导、
社区源默认顺序、GitHub 代理列表迁移、更新清单回退与 SHA-256 门禁、
中文别名修正、新闻合并去重。
"""
from __future__ import annotations

import unittest
from unittest import mock

from mclauncher import catalog, manifest, mirrors, news, source, updater
from mclauncher.java import adoptium_major
from mclauncher.mods import _english_terms_from_hits, _has_cjk, _mc_sort_key, _mc_version_from_text
from mclauncher.neoforge_meta import neoforge_version_prefix


class McVersionTextTests(unittest.TestCase):
    def test_year_release(self):
        self.assertEqual(_mc_version_from_text("26.2"), "26.2")
        self.assertEqual(_mc_version_from_text("26.1.1"), "26.1.1")

    def test_year_prerelease(self):
        self.assertEqual(_mc_version_from_text("26.3-snapshot-10"), "26.3-snapshot-10")
        self.assertEqual(_mc_version_from_text("26.2-rc-2"), "26.2-rc-2")
        self.assertEqual(_mc_version_from_text("26.2-pre-1"), "26.2-pre-1")

    def test_mod_version_is_not_mc(self):
        # 主版本 3–19 视为模组自身版本
        self.assertIsNone(_mc_version_from_text("5.2.1"))
        self.assertIsNone(_mc_version_from_text("12.4"))

    def test_legacy_still_works(self):
        self.assertEqual(_mc_version_from_text("1.20.1"), "1.20.1")
        self.assertEqual(_mc_version_from_text("1.20.1-forge-47.2.0"), "1.20.1")

    def test_extract_from_loader_id(self):
        self.assertEqual(_mc_version_from_text("26.1-neoforge-26.1.2.95"), "26.1")
        # 不能把 26.1.2 中间的 "1.2" 当成 MC 版本
        self.assertEqual(_mc_version_from_text("neoforge-26.1.2"), "26.1.2")

    def test_sort_key_year_above_legacy(self):
        self.assertGreater(_mc_sort_key("26.1"), _mc_sort_key("1.21.11"))
        self.assertGreater(_mc_sort_key("26.2"), _mc_sort_key("26.1.3"))


class VanillaIdTests(unittest.TestCase):
    def test_year_ids_are_vanilla(self):
        self.assertTrue(manifest.is_vanilla_id("26.2"))
        self.assertTrue(manifest.is_vanilla_id("26.1.1"))
        self.assertTrue(manifest.is_vanilla_id("26.3-snapshot-10"))
        self.assertTrue(manifest.is_vanilla_id("26.2-rc-2"))
        self.assertTrue(manifest.is_vanilla_id("26.2-pre-1"))

    def test_week_snapshot_and_legacy(self):
        self.assertTrue(manifest.is_vanilla_id("25w14a"))
        self.assertTrue(manifest.is_vanilla_id("1.21.1"))
        self.assertTrue(manifest.is_vanilla_id("1.19-pre1"))

    def test_loader_ids_are_not_vanilla(self):
        self.assertFalse(manifest.is_vanilla_id("26.1-neoforge-26.1.2.95"))
        self.assertFalse(manifest.is_vanilla_id("fabric-loader-0.16.9-26.1"))

    def test_mc_version_tuple_year_snapshot(self):
        self.assertEqual(manifest.mc_version_tuple("26.3-snapshot-10"), (26, 3, 0))
        self.assertTrue(manifest.looks_like_minecraft_version("26.3-snapshot-10"))
        self.assertFalse(manifest.looks_like_minecraft_version("5.2.1"))


class CatalogReleaseIdsTests(unittest.TestCase):
    MANIFEST = {
        "versions": [
            {"id": "26.3-snapshot-2", "type": "snapshot"},
            {"id": "26.2", "type": "release"},
            {"id": "26.1.1", "type": "release"},
            {"id": "1.21.11", "type": "release"},
            {"id": "1.21.10", "type": "release"},
            {"id": "1.20.1", "type": "release"},
            {"id": "1.19.2", "type": "release"},
            {"id": "1.18.2", "type": "release"},
            {"id": "1.16.5", "type": "release"},
            {"id": "1.12.2", "type": "release"},
        ]
    }

    def test_latest_first_and_classics_present(self):
        ids = manifest.catalog_release_ids(self.MANIFEST, limit=4)
        self.assertEqual(ids[0], "26.2")
        self.assertNotIn("26.3-snapshot-2", ids)
        for classic in ("1.20.1", "1.19.2", "1.18.2", "1.16.5", "1.12.2"):
            self.assertIn(classic, ids)

    def test_accepts_backend_rows(self):
        rows = [{"version": "26.2", "type": "release"},
                {"version": "26.3-snapshot-2", "type": "snapshot"}]
        self.assertEqual(manifest.catalog_release_ids(rows), ["26.2"])

    def test_empty_manifest(self):
        self.assertEqual(manifest.catalog_release_ids({}), [])
        self.assertEqual(manifest.catalog_release_ids(None), [])


class AdoptiumMajorTests(unittest.TestCase):
    def test_lts_mapping(self):
        self.assertEqual(adoptium_major(8), 8)
        self.assertEqual(adoptium_major(17), 17)
        self.assertEqual(adoptium_major(21), 21)

    def test_above_21_maps_to_25(self):
        self.assertEqual(adoptium_major(22), 25)
        self.assertEqual(adoptium_major(25), 25)
        self.assertEqual(adoptium_major(26), 25)


class NeoForgePrefixTests(unittest.TestCase):
    def test_legacy_1_20_1(self):
        self.assertEqual(neoforge_version_prefix("1.20.1"), "47.1")

    def test_modern_1_x(self):
        self.assertEqual(neoforge_version_prefix("1.20.2"), "20.2")
        self.assertEqual(neoforge_version_prefix("1.20.4"), "20.4")
        self.assertEqual(neoforge_version_prefix("1.21"), "21.0")
        self.assertEqual(neoforge_version_prefix("1.21.1"), "21.1")

    def test_year_versions_keep_first_segment(self):
        self.assertEqual(neoforge_version_prefix("26.1"), "26.1")
        self.assertEqual(neoforge_version_prefix("26.2"), "26.2")
        self.assertEqual(neoforge_version_prefix("26.1.2"), "26.1.2")

    def test_unsupported(self):
        self.assertIsNone(neoforge_version_prefix("1.19.2"))
        self.assertIsNone(neoforge_version_prefix(""))
        self.assertIsNone(neoforge_version_prefix("abc"))


class CommunitySourceOrderTests(unittest.TestCase):
    def test_auto_is_official_first(self):
        with mock.patch.object(source, "community_mode", return_value="auto"):
            self.assertFalse(source.community_mirror_first())

    def test_mcim_is_mirror_first(self):
        with mock.patch.object(source, "community_mode", return_value="mcim"):
            self.assertTrue(source.community_mirror_first())

    def test_official_only(self):
        with mock.patch.object(source, "community_mode", return_value="official"):
            self.assertFalse(source.community_mirror_first())

    def test_modrinth_bases_auto_official_first(self):
        with mock.patch.object(source, "community_mode", return_value="auto"):
            bases = source.modrinth_api_bases()
        self.assertTrue(bases[0].startswith("https://api.modrinth.com"))


class GithubProxyTests(unittest.TestCase):
    def _prefixes_with(self, configured):
        from mclauncher.config import CONFIG

        def fake_get(key, default=None):
            if key == "github_proxy_prefixes":
                return configured
            return default

        with mock.patch.object(CONFIG, "get", side_effect=fake_get):
            return mirrors._prefixes()

    def test_default_has_no_dead_proxy(self):
        self.assertNotIn("https://gitproxy.mrhjx.cn/", mirrors.GITHUB_PROXY_PREFIXES)
        self.assertIn("https://ghfast.top/", mirrors.GITHUB_PROXY_PREFIXES)
        self.assertIn("https://gh.llkk.cc/", mirrors.GITHUB_PROXY_PREFIXES)

    def test_remote_source_fetch_uses_built_in_raw_mirrors(self):
        expected_prefixes = (
            "https://gitproxy.mrhjx.cn/",
            "https://ghproxy.vip/",
            "https://gh-proxy.com/",
            "https://v6.gh-proxy.org/",
            "https://cdn.gh-proxy.com/",
        )
        self.assertEqual(mirrors.RAW_GITHUB_MIRROR_PREFIXES, expected_prefixes)
        self.assertEqual(
            mirrors.REMOTE_SOURCE_URLS[:len(expected_prefixes)],
            tuple(prefix + mirrors.REMOTE_SOURCE_RAW_URL for prefix in expected_prefixes),
        )
        self.assertEqual(
            mirrors.REMOTE_SOURCE_URLS[len(expected_prefixes)],
            mirrors.REMOTE_SOURCE_RAW_URL,
        )
        self.assertNotIn("https://gitproxy.mrhjx.cn/", mirrors.GITHUB_PROXY_PREFIXES)

    def test_old_config_with_dead_proxy_is_migrated(self):
        old = ["https://gitproxy.mrhjx.cn/", "https://my.custom.proxy/", "https://gh-proxy.com/"]
        got = self._prefixes_with(old)
        self.assertNotIn("https://gitproxy.mrhjx.cn/", got)
        # 用户自加的前缀保留，并并入新默认
        self.assertIn("https://my.custom.proxy/", got)
        self.assertIn("https://ghfast.top/", got)

    def test_custom_config_without_dead_proxy_untouched(self):
        custom = ["https://my.custom.proxy/"]
        self.assertEqual(self._prefixes_with(custom), ("https://my.custom.proxy/",))

    def test_parse_remote_sources_ignores_comments_blanks_and_non_https(self):
        text = """
        # one URL per line

        https://ghfast.top/
        http://insecure.example/
        not-a-url
        https://bmclapi2.bangbang93.com
        """
        self.assertEqual(
            mirrors.parse_source_urls(text),
            (
                "https://ghfast.top/",
                "https://bmclapi2.bangbang93.com",
            ),
        )

    def test_remote_fetch_failure_falls_back_to_builtins(self):
        with (
            mock.patch.object(mirrors, "_remote_prefixes", None),
            mock.patch.object(mirrors, "_remote_fetched_at", 0.0),
            mock.patch.object(mirrors, "_remote_fetch_succeeded", False),
            mock.patch.object(mirrors, "_fetch_source_text", side_effect=OSError("offline")),
        ):
            got = mirrors.refresh_remote_sources(force=True)
        self.assertEqual(got, mirrors.GITHUB_PROXY_PREFIXES)

    def test_remote_refresh_merges_duplicates_and_filters_dead_prefixes(self):
        text = """
        https://new.gh-proxy.example/
        https://gitproxy.mrhjx.cn/
        https://new.gh-proxy.example
        https://ghfast.top/
        https://bmclapi2.bangbang93.com
        """
        with (
            mock.patch.object(mirrors, "_remote_prefixes", None),
            mock.patch.object(mirrors, "_remote_fetched_at", 0.0),
            mock.patch.object(mirrors, "_remote_fetch_succeeded", False),
            mock.patch.object(mirrors, "_fetch_source_text", return_value=text),
        ):
            got = mirrors.refresh_remote_sources(force=True)
        self.assertEqual(
            got,
            ("https://new.gh-proxy.example/", "https://ghfast.top/"),
        )
        self.assertNotIn("https://gitproxy.mrhjx.cn/", got)


class UpdaterTests(unittest.TestCase):
    def test_dead_default_url_detected(self):
        self.assertTrue(updater.is_dead_update_url("https://pymcl.dev/update.json"))
        self.assertTrue(updater.is_dead_update_url("https://www.pymcl.dev/update.json"))
        self.assertFalse(updater.is_dead_update_url("https://example.com/update.json"))
        self.assertFalse(updater.is_dead_update_url(""))

    def test_manifest_from_github_release_with_digest(self):
        rel = {
            "tag_name": "v9.9.9",
            "body": "更新说明",
            "assets": [{
                "name": "PyMCL.exe",
                "browser_download_url": "https://github.com/o/r/releases/download/v9.9.9/PyMCL.exe",
                "digest": "sha256:" + "a" * 64,
            }],
        }
        data = updater.manifest_from_github_release(rel)
        self.assertEqual(data["version"], "9.9.9")
        self.assertEqual(data["sha256"], "a" * 64)
        self.assertTrue(data["url"].endswith("PyMCL.exe"))

    def test_manifest_from_github_release_sha_in_body(self):
        rel = {
            "tag_name": "v9.9.9",
            "body": "notes\nSHA-256: " + "b" * 64,
            "assets": [{"name": "PyMCL.zip", "browser_download_url": "https://x/PyMCL.zip"}],
        }
        data = updater.manifest_from_github_release(rel)
        self.assertEqual(data["sha256"], "b" * 64)

    def test_check_github_release_unsigned_blocks_auto_download(self):
        class ReleaseDM:
            def fetch_json(self, url, timeout=0):
                assert "api.github.com" in url
                return {
                    "tag_name": "v99.0.0",
                    "body": "no hash here",
                    "assets": [{"name": "PyMCL.exe",
                                "browser_download_url": "https://x/PyMCL.exe"}],
                }

        with mock.patch.object(updater, "manifest_url", return_value=""):
            info = updater.check(ReleaseDM())
        self.assertEqual(info["latest"], "99.0.0")
        self.assertFalse(info["has_update"])  # 没有 sha256 不允许自动下载
        self.assertIn("SHA-256", info["message"])

    def test_check_github_release_signed(self):
        class ReleaseDM:
            def fetch_json(self, url, timeout=0):
                return {
                    "tag_name": "v99.0.0",
                    "body": "sha256: " + "c" * 64,
                    "assets": [{"name": "PyMCL.exe",
                                "browser_download_url": "https://x/PyMCL.exe"}],
                }

        with mock.patch.object(updater, "manifest_url", return_value=""):
            info = updater.check(ReleaseDM())
        self.assertTrue(info["ok"])
        self.assertTrue(info["has_update"])
        self.assertEqual(info["sha256"], "c" * 64)

    def test_dead_custom_url_falls_back_to_github(self):
        calls = []

        class DM:
            def fetch_json(self, url, timeout=0):
                calls.append(url)
                return {"tag_name": "v0.0.1", "assets": []}

        with mock.patch.object(updater, "manifest_url",
                               return_value="https://pymcl.dev/update.json"):
            updater.check(DM())
        self.assertEqual(len(calls), 1)
        self.assertIn("api.github.com", calls[0])


class CatalogAliasTests(unittest.TestCase):
    def test_optifine_not_mapped_to_iris(self):
        for key in ("optifine", "高清修复"):
            entry = catalog.MOD_ALIASES.get(key) or {}
            self.assertNotEqual(entry.get("slug"), "iris", key)
            self.assertFalse(entry.get("slug"), f"{key} 不应携带 slug")
            self.assertIn("OptiFine", entry.get("title") or "")

    def test_iris_aliases_still_point_to_iris(self):
        self.assertEqual((catalog.MOD_ALIASES.get("光影") or {}).get("slug"), "iris")
        self.assertEqual((catalog.MOD_ALIASES.get("iris") or {}).get("slug"), "iris")

    def test_phosphor_redirects_to_starlight(self):
        for key in ("磷", "phosphor"):
            entry = catalog.MOD_ALIASES.get(key) or {}
            self.assertEqual(entry.get("slug"), "starlight", key)

    def test_dead_slugs_removed(self):
        for key in ("考古", "糖果世界", "经验书", "巫术", "witchcraft", "xp book"):
            entry = catalog.MOD_ALIASES.get(key) or {}
            self.assertFalse(entry.get("slug"), f"{key} 的 slug 应已移除")

    def test_popular_mods_not_pinned_to_1_20_1(self):
        for name, _source, _key, mc, _loader in catalog.POPULAR_MODS:
            self.assertNotEqual(mc, "1.20.1", name)

    def test_native_catalog_json_in_sync(self):
        import json
        from pathlib import Path
        root = Path(__file__).resolve().parents[1]
        data = json.loads((root / "native/data/catalog.json").read_text(encoding="utf-8"))
        aliases = data.get("mod_aliases") or {}
        self.assertEqual(aliases, catalog.MOD_ALIASES)


class ChineseSearchHelperTests(unittest.TestCase):
    def test_has_cjk(self):
        self.assertTrue(_has_cjk("旅行地图"))
        self.assertFalse(_has_cjk("journeymap"))

    def test_english_terms_from_hits(self):
        hits = [
            {"title": "JourneyMap"},
            {"title": "JourneyMap Integration"},
            {"title": "Xaero's Minimap"},
        ]
        terms = _english_terms_from_hits(hits)
        self.assertIn("journeymap", terms)


class NewsMergeTests(unittest.TestCase):
    def test_news_urls_include_mojang_news(self):
        self.assertIn("https://launchercontent.mojang.com/news.json", news.NEWS_URLS)

    def test_merge_rows_dedupe_and_cap(self):
        rows = [{"title": "A", "version": "1"}] * 3 + [
            {"title": f"t{i}", "version": ""} for i in range(20)
        ]
        merged = news._merge_rows(rows)
        titles = [r["title"] for r in merged]
        self.assertEqual(titles.count("A"), 1)
        self.assertLessEqual(len(merged), news.MAX_ROWS)

    def test_news_rows_prefers_java(self):
        payload = {"entries": [
            {"title": "Bedrock thing", "text": "x", "newsType": ["News page", "Bedrock"]},
            {"title": "Java thing", "text": "y", "newsType": ["News page", "Java"],
             "newsPageImage": {"url": "/images/a.png"}},
        ]}
        rows = news._news_rows(payload)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "Java thing")
        self.assertTrue(rows[0]["image"].startswith("https://launchercontent.mojang.com/"))


if __name__ == "__main__":
    unittest.main()
