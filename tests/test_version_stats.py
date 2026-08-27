# -*- coding: utf-8 -*-
"""每版本游玩统计（HMCL 游戏列表「最近游玩」同款）。"""
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from mclauncher import playtime, utils
from mclauncher.config import CONFIG, DEFAULT_CONFIG


class _Isolated(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        self.addCleanup(self._td.cleanup)
        data = {k: (list(v) if isinstance(v, list) else dict(v) if isinstance(v, dict) else v)
                for k, v in DEFAULT_CONFIG.items()}
        for p in (patch.object(utils, "ROOT", self.root),
                  patch.object(CONFIG, "data", data),
                  patch.object(CONFIG, "save", lambda: None)):
            p.start()
            self.addCleanup(p.stop)


class VersionStatsTests(_Isolated):
    def test_stats_from_sessions(self):
        playtime.record_session("default", "1.20.1", 600)
        playtime.record_session("default", "1.21", 120)
        playtime.record_session("default", "1.20.1", 300)

        stats = playtime.version_stats("default")
        self.assertEqual(stats["1.20.1"]["seconds"], 900)
        self.assertEqual(stats["1.21"]["seconds"], 120)
        # last = 会话 start+duration 的最大值，最近记录的应该更晚
        self.assertGreaterEqual(stats["1.20.1"]["last"], stats["1.21"]["last"])
        self.assertGreater(stats["1.21"]["last"], 0)

    def test_empty_instance(self):
        self.assertEqual(playtime.version_stats("ghost"), {})

    def test_version_without_sessions_has_zero_last(self):
        # 会话被裁剪后 versions 总账还在：seconds 保留，last 归零
        playtime.record_session("default", "old", 60)
        data = playtime._load()
        data["instances"]["default"]["sessions"] = []
        playtime._save(data)
        stats = playtime.version_stats("default")
        self.assertEqual(stats["old"]["seconds"], 60)
        self.assertEqual(stats["old"]["last"], 0)


class FormatLastPlayedTests(unittest.TestCase):
    def test_buckets(self):
        now = 1_800_000_000
        self.assertEqual(playtime.format_last_played(0), "")
        self.assertEqual(playtime.format_last_played(now - 5, now=now), "刚刚")
        self.assertEqual(playtime.format_last_played(now - 180, now=now), "3 分钟前")
        self.assertEqual(playtime.format_last_played(now - 7200, now=now), "2 小时前")
        self.assertEqual(playtime.format_last_played(now - 3 * 86400, now=now), "3 天前")
        old = playtime.format_last_played(now - 90 * 86400, now=now)
        self.assertRegex(old, r"^\d{4}-\d{2}-\d{2}$")


class FacadeTests(_Isolated):
    def test_bridge_get_version_stats(self):
        from bridge.api import BackendAPI

        class _Bus:
            def emit(self, *a, **k):
                pass

        api = BackendAPI(_Bus())
        playtime.record_session("default", "1.21", 4000)
        stats = api.get_version_stats("default")
        self.assertIn("1.21", stats)
        row = stats["1.21"]
        self.assertEqual(row["seconds"], 4000)
        self.assertTrue(row["seconds_text"].startswith("1 小时"))
        self.assertEqual(row["last_text"], "刚刚")

    def test_qt_get_version_stats(self):
        from app.backend import BackendAPI as QtBackend
        playtime.record_session("default", "fabric-1.21", 90)
        stats = QtBackend.get_version_stats(None, "default")
        self.assertEqual(stats["fabric-1.21"]["seconds"], 90)
        self.assertTrue(stats["fabric-1.21"]["last"] <= int(time.time()) + 1)


if __name__ == "__main__":
    unittest.main()
