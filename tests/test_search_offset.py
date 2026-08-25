# -*- coding: utf-8 -*-
"""搜索翻页（offset）测试：对标 PCL2 下载页翻页 / HMCL 加载更多。

Modrinth 用 offset 参数，CurseForge 用 index 参数；开分类过滤时
CF 的 pageSize 翻倍，index 必须同步翻倍才能保证窗口不重叠。
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mclauncher import mods, modpack  # noqa: E402
from mclauncher import catalog_files  # noqa: E402


class _FakeDM:
    """记录 fetch_json 的 URL 与参数，返回固定负载。"""

    def __init__(self, payload=None):
        self.calls = []
        self.payload = payload if payload is not None else {"hits": []}

    def fetch_json(self, url, params=None, **_kw):
        self.calls.append((url, dict(params or {})))
        return self.payload


class ModrinthOffsetTest(unittest.TestCase):
    def test_search_mods_passes_offset(self):
        dm = _FakeDM()
        mods.search_mods(dm, "sodium", limit=30, offset=30)
        _, params = dm.calls[0]
        self.assertEqual(params["offset"], 30)
        self.assertEqual(params["limit"], 30)

    def test_search_mods_page_one_has_no_offset(self):
        dm = _FakeDM()
        mods.search_mods(dm, "sodium", limit=30)
        _, params = dm.calls[0]
        self.assertNotIn("offset", params)

    def test_search_projects_passes_offset(self):
        dm = _FakeDM()
        mods.search_modrinth_projects(dm, "bsl", "shader", limit=30, offset=60)
        _, params = dm.calls[0]
        self.assertEqual(params["offset"], 60)

    def test_modpack_search_passes_offset(self):
        dm = _FakeDM()
        modpack.modrinth_search(dm, "skyblock", limit=30, offset=30)
        _, params = dm.calls[0]
        self.assertEqual(params["offset"], 30)

    def test_modpack_search_page_one_has_no_offset(self):
        dm = _FakeDM()
        modpack.modrinth_search(dm, "skyblock", limit=30)
        _, params = dm.calls[0]
        self.assertNotIn("offset", params)


class CurseforgeOffsetTest(unittest.TestCase):
    def test_index_follows_offset(self):
        dm = _FakeDM(payload={"data": []})
        mods.search_curseforge(dm, "jei", limit=30, offset=30)
        _, params = dm.calls[0]
        self.assertEqual(params["index"], 30)
        self.assertEqual(params["pageSize"], 30)

    def test_index_zero_by_default(self):
        dm = _FakeDM(payload={"data": []})
        mods.search_curseforge(dm, "jei", limit=30)
        _, params = dm.calls[0]
        self.assertEqual(params["index"], 0)

    def test_category_filter_doubles_window(self):
        # 分类过滤是客户端过滤：pageSize 翻倍拉取，index 必须同步翻倍
        dm = _FakeDM(payload={"data": []})
        mods.search_curseforge(dm, "jei", limit=30, offset=30,
                               categories=["performance"])
        _, params = dm.calls[0]
        self.assertEqual(params["pageSize"], 60)
        self.assertEqual(params["index"], 60)

    def test_cf_modpack_search_passes_offset(self):
        with mock.patch.object(modpack, "search_cf_modpacks",
                               wraps=modpack.search_cf_modpacks):
            with mock.patch("mclauncher.mods.search_curseforge",
                            return_value=[]) as cf:
                modpack.search_cf_modpacks(_FakeDM(), "atm", limit=30, offset=90)
        self.assertEqual(cf.call_args.kwargs.get("offset"), 90)


class SearchProjectsOffsetTest(unittest.TestCase):
    """catalog_files.search_projects 是世界搜索与桥的公共入口，offset 走 extra。"""

    def test_offset_forwarded_to_both_sources(self):
        with mock.patch("mclauncher.mods.search_mods", return_value=[]) as mr, \
                mock.patch("mclauncher.mods.search_curseforge", return_value=[]) as cf:
            catalog_files.search_projects(
                _FakeDM(), "mod", "sodium", "all", {"offset": 30})
        self.assertEqual(mr.call_args.kwargs.get("offset"), 30)
        self.assertEqual(cf.call_args.kwargs.get("offset"), 30)

    def test_bad_offset_falls_back_to_zero(self):
        with mock.patch("mclauncher.mods.search_curseforge", return_value=[]) as cf:
            catalog_files.search_projects(
                _FakeDM(), "world", "castle", "curseforge", {"offset": "abc"})
        self.assertEqual(cf.call_args.kwargs.get("offset"), 0)


class BridgeOffsetHelperTest(unittest.TestCase):
    def test_search_offset_parsing(self):
        try:
            from bridge.api import BackendAPI
        except Exception as e:  # 环境缺依赖时跳过，不算失败
            self.skipTest(f"bridge.api 不可导入: {e}")
        f = BackendAPI._search_offset
        self.assertEqual(f(None), 0)
        self.assertEqual(f({}), 0)
        self.assertEqual(f({"offset": 30}), 30)
        self.assertEqual(f({"offset": "60"}), 60)
        self.assertEqual(f({"offset": -5}), 0)
        self.assertEqual(f({"offset": "junk"}), 0)


if __name__ == "__main__":
    unittest.main()
