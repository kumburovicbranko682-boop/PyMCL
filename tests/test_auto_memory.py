# -*- coding: utf-8 -*-
"""内存自动分配策略与 launch_flow 的 0=自动语义。"""
from __future__ import annotations

import unittest

from mclauncher import memory


class AutoMemoryTests(unittest.TestCase):
    def test_typical_8g_machine(self):
        # 8G 总内存，5G 可用 → 5120*0.6=3072
        result = memory.auto_memory(total_mb=8192, avail_mb=5120)
        self.assertEqual(result["memory_mb"], 3072)
        self.assertFalse(result["fallback"])

    def test_leaves_system_reserve(self):
        # 4G 总内存，可用几乎全部 → 上限 total-2048
        result = memory.auto_memory(total_mb=4096, avail_mb=3800)
        self.assertEqual(result["memory_mb"], 2048)

    def test_caps_on_huge_machines(self):
        result = memory.auto_memory(total_mb=65536, avail_mb=60000)
        self.assertEqual(result["memory_mb"], 12288)

    def test_floor_on_tiny_machines(self):
        result = memory.auto_memory(total_mb=3072, avail_mb=900)
        self.assertEqual(result["memory_mb"], 1024)

    def test_aligned_to_256(self):
        result = memory.auto_memory(total_mb=16384, avail_mb=9000)
        self.assertEqual(result["memory_mb"] % 256, 0)

    def test_fallback_when_unreadable(self):
        result = memory.auto_memory(total_mb=0, avail_mb=0)
        self.assertEqual(result["memory_mb"], 4096)
        self.assertTrue(result["fallback"])

    def test_reads_real_system(self):
        # 本机真实读取：结果必须在合法区间
        result = memory.auto_memory()
        self.assertGreaterEqual(result["memory_mb"], 1024)
        self.assertLessEqual(result["memory_mb"], 12288)

    def test_mb_helper(self):
        self.assertEqual(memory.auto_memory_mb(8192, 5120), 3072)


class LaunchFlowAutoTests(unittest.TestCase):
    def test_prepare_uses_auto_when_zero(self):
        """memory_mb=0 时 prepare 走自动分配；版本设置存在时优先。"""
        from unittest import mock
        from mclauncher import launch_flow

        class FakeInstance:
            name = "t"

        with mock.patch("mclauncher.launch_flow.version_settings") as vs, \
             mock.patch("mclauncher.launch_flow.global_mods") as gm, \
             mock.patch("mclauncher.memory.auto_memory",
                        return_value={"memory_mb": 3333, "total_mb": 1,
                                      "avail_mb": 1, "fallback": False}):
            vs.load.return_value = {}
            vs.apply_isolation.return_value = __import__("pathlib").Path("/tmp")
            vs.FULLSCREEN_MODES = ()
            gm.apply.return_value = 0
            prep = launch_flow.prepare(FakeInstance(), "1.20.1", memory_mb=0)
            self.assertEqual(prep["memory_mb"], 3333)
            self.assertIsNotNone(prep["memory_auto"])
            # 显式给值则不自动
            prep2 = launch_flow.prepare(FakeInstance(), "1.20.1", memory_mb=6144)
            self.assertEqual(prep2["memory_mb"], 6144)
            self.assertIsNone(prep2["memory_auto"])
            # 版本设置覆盖优先于自动
            vs.load.return_value = {"memory_mb": 2222}
            prep3 = launch_flow.prepare(FakeInstance(), "1.20.1", memory_mb=0)
            self.assertEqual(prep3["memory_mb"], 2222)
            self.assertIsNone(prep3["memory_auto"])


if __name__ == "__main__":
    unittest.main()
