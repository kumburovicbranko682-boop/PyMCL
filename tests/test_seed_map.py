# -*- coding: utf-8 -*-
"""Chunk Base 种子地图快速入口（HMCL 3.6.14 世界管理同款）。

URL 约定：
- 正式版号（1.21 / 1.21.4）映射成 platform=java_1_21(_4)；
- 快照等非 x.y(.z) 版本不带 platform，交给网站默认；
- 种子可为负数；没有种子必须报错而不是拼出坏链接。
"""
import unittest
from unittest.mock import Mock

from mclauncher.saves import SaveError, chunkbase_url


class ChunkbaseUrlTests(unittest.TestCase):
    def test_release_version_maps_to_platform(self):
        url = chunkbase_url("123456", "1.21.4")
        self.assertEqual(
            url, "https://www.chunkbase.com/apps/seed-map#seed=123456&platform=java_1_21_4")

    def test_two_part_version(self):
        url = chunkbase_url("7", "1.21")
        self.assertTrue(url.endswith("#seed=7&platform=java_1_21"))

    def test_negative_seed_kept(self):
        url = chunkbase_url(-8123456789012345678, "1.20.1")
        self.assertIn("seed=-8123456789012345678", url)

    def test_snapshot_version_omits_platform(self):
        url = chunkbase_url("42", "24w14a")
        self.assertEqual(url, "https://www.chunkbase.com/apps/seed-map#seed=42")

    def test_empty_version_omits_platform(self):
        self.assertEqual(chunkbase_url("42"),
                         "https://www.chunkbase.com/apps/seed-map#seed=42")

    def test_unknown_app_falls_back_to_seed_map(self):
        self.assertIn("/apps/seed-map#", chunkbase_url("42", app="evil/../path"))

    def test_other_supported_app(self):
        self.assertIn("/apps/slime-finder#", chunkbase_url("42", app="slime-finder"))

    def test_missing_seed_raises(self):
        with self.assertRaises(SaveError):
            chunkbase_url("")


class FacadeTests(unittest.TestCase):
    """world_seed_map_url：从 list_saves 行里取种子/版本再拼链接。"""

    def _api(self, rows):
        from bridge.api import BackendAPI
        api = BackendAPI.__new__(BackendAPI)   # 不跑 __init__，只测这一个方法
        api.list_saves = Mock(return_value=rows)
        return api

    def test_builds_url_from_save_row(self):
        api = self._api([{"name": "MyWorld", "seed": "99", "mc_version": "1.21.4"}])
        url = api.world_seed_map_url("default", "MyWorld")
        self.assertEqual(
            url, "https://www.chunkbase.com/apps/seed-map#seed=99&platform=java_1_21_4")

    def test_unknown_world_raises(self):
        api = self._api([])
        with self.assertRaises(SaveError):
            api.world_seed_map_url("default", "Nope")

    def test_world_without_seed_raises(self):
        api = self._api([{"name": "Old", "seed": "", "mc_version": ""}])
        with self.assertRaises(SaveError):
            api.world_seed_map_url("default", "Old")


if __name__ == "__main__":
    unittest.main()
