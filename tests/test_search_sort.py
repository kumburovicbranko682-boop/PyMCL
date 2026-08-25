# -*- coding: utf-8 -*-
"""下载页搜索排序 + 分页（PCL2/HMCL 排序下拉与「加载更多」同款）。"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mclauncher import modpack as modpack_mod  # noqa: E402
from mclauncher import mods as mods_mod  # noqa: E402


class _FakeDM:
    """捕获 fetch_json 的 params，返回空结果。"""

    def __init__(self):
        self.calls = []

    def fetch_json(self, url, params=None, **kw):
        self.calls.append({"url": url, "params": dict(params or {})})
        return {"hits": [], "data": []}


class SortKeyTests(unittest.TestCase):
    def test_mr_index_known_keys(self):
        self.assertEqual(mods_mod.mr_sort_index("downloads"), "downloads")
        self.assertEqual(mods_mod.mr_sort_index("updated"), "updated")
        self.assertEqual(mods_mod.mr_sort_index("newest"), "newest")
        self.assertEqual(mods_mod.mr_sort_index("follows"), "follows")
        self.assertEqual(mods_mod.mr_sort_index("relevance"), "relevance")

    def test_mr_index_default_keeps_old_behavior(self):
        # 留空：有词按相关度，无词按下载量（改造前的行为）
        self.assertEqual(mods_mod.mr_sort_index("", "sodium"), "relevance")
        self.assertEqual(mods_mod.mr_sort_index("", ""), "downloads")
        self.assertEqual(mods_mod.mr_sort_index(None, "  "), "downloads")

    def test_cf_sort_field(self):
        self.assertEqual(mods_mod.cf_sort_field("downloads"), 6)
        self.assertEqual(mods_mod.cf_sort_field("updated"), 3)
        self.assertEqual(mods_mod.cf_sort_field("newest"), 11)
        # 默认 / 未知键 / follows 都退回人气
        self.assertEqual(mods_mod.cf_sort_field(""), 2)
        self.assertEqual(mods_mod.cf_sort_field("bogus"), 2)
        self.assertEqual(mods_mod.cf_sort_field("follows"), 2)


class ModrinthParamsTests(unittest.TestCase):
    def test_search_mods_sort_and_offset(self):
        dm = _FakeDM()
        mods_mod.search_mods(dm, "sodium", sort="downloads", offset=30)
        params = dm.calls[0]["params"]
        self.assertEqual(params["index"], "downloads")
        self.assertEqual(params["offset"], 30)

    def test_search_mods_defaults_unchanged(self):
        dm = _FakeDM()
        mods_mod.search_mods(dm, "sodium")
        params = dm.calls[0]["params"]
        self.assertEqual(params["index"], "relevance")
        self.assertNotIn("offset", params)

    def test_search_modrinth_projects_sort_and_offset(self):
        dm = _FakeDM()
        mods_mod.search_modrinth_projects(dm, "x", "shader", sort="newest", offset=60)
        params = dm.calls[0]["params"]
        self.assertEqual(params["index"], "newest")
        self.assertEqual(params["offset"], 60)

    def test_modpack_search_sort_and_offset(self):
        dm = _FakeDM()
        modpack_mod.modrinth_search(dm, "atm", sort="updated", offset=25)
        params = dm.calls[0]["params"]
        self.assertEqual(params["index"], "updated")
        self.assertEqual(params["offset"], 25)

    def test_modpack_search_default_relevance(self):
        dm = _FakeDM()
        modpack_mod.modrinth_search(dm, "atm")
        params = dm.calls[0]["params"]
        self.assertEqual(params["index"], "relevance")
        self.assertNotIn("offset", params)


class CurseforgeParamsTests(unittest.TestCase):
    def _capture(self, **kw):
        captured = {}

        def fake_fetch(dm, path, api_key=None, params=None, **_kw):
            captured.update(params or {})
            return {"data": []}

        with patch.object(mods_mod, "_cf_fetch", fake_fetch):
            mods_mod.search_curseforge(object(), "jei", **kw)
        return captured

    def test_sort_and_offset(self):
        params = self._capture(sort="updated", offset=10)
        self.assertEqual(params["sortField"], 3)
        self.assertEqual(params["sortOrder"], "desc")
        self.assertEqual(params["index"], 10)

    def test_default_popularity(self):
        params = self._capture()
        self.assertEqual(params["sortField"], 2)
        self.assertEqual(params["index"], 0)

    def test_cf_modpacks_passthrough(self):
        captured = {}

        def fake_fetch(dm, path, api_key=None, params=None, **_kw):
            captured.update(params or {})
            return {"data": []}

        with patch.object(mods_mod, "_cf_fetch", fake_fetch):
            modpack_mod.search_cf_modpacks(object(), "atm", sort="downloads", offset=50)
        self.assertEqual(captured["sortField"], 6)
        self.assertEqual(captured["index"], 50)


class FacadePassthroughTests(unittest.TestCase):
    """两个门面都要把 extra 里的 sort/offset 传进核心搜索。"""

    def _qt(self):
        from app.backend import BackendAPI as QtBackend
        return QtBackend.__new__(QtBackend)

    def _bridge(self):
        from bridge.api import BackendAPI
        return BackendAPI.__new__(BackendAPI)

    def test_qt_search_mods_passthrough(self):
        api = self._qt()
        captured = {}

        def fake(dm, q, limit=30, game_version=None, categories=None, sort="", offset=0):
            captured.update(sort=sort, offset=offset)
            return []

        with patch("mclauncher.mods.search_mods", fake):
            api.search_mods("sodium", "Modrinth", {"sort": "downloads", "offset": 30})
        self.assertEqual(captured, {"sort": "downloads", "offset": 30})

    def test_bridge_search_mods_passthrough(self):
        api = self._bridge()
        captured = {}

        def fake(dm, q, limit=30, game_version=None, categories=None, sort="", offset=0):
            captured.update(sort=sort, offset=offset)
            return []

        with patch("mclauncher.mods.search_mods", fake):
            api.search_mods("sodium", "Modrinth", {"sort": "updated", "offset": 60})
        self.assertEqual(captured, {"sort": "updated", "offset": 60})

    def test_qt_modpacks_offset_skips_alias_and_passes_through(self):
        api = self._qt()
        captured = {}
        alias_called = []

        def fake_mr(dm, q, limit=25, game_version=None, categories=None, sort="", offset=0):
            captured.update(sort=sort, offset=offset)
            return []

        with patch("mclauncher.modpack.modrinth_search", fake_mr), \
             patch("mclauncher.modpack.search_modpacks_chinese",
                   lambda *a, **k: alias_called.append(1) or []):
            api.search_modpacks("atm", "Modrinth", {"sort": "newest", "offset": 25})
        self.assertEqual(captured, {"sort": "newest", "offset": 25})
        # 翻页时跳过中文别名目录（它只有一页）
        self.assertEqual(alias_called, [])

    def test_facade_search_signatures_match(self):
        import inspect
        from app.backend import BackendAPI as QtBackend
        from bridge.api import BackendAPI
        for name in ("search_mods", "search_modpacks", "search_shaders",
                     "search_resourcepacks", "search_datapacks", "search_worlds"):
            qt = getattr(QtBackend, name)
            br = getattr(BackendAPI, name)
            self.assertEqual(inspect.signature(qt), inspect.signature(br),
                             f"{name} 签名不一致")


if __name__ == "__main__":
    unittest.main()
