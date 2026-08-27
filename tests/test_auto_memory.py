# -*- coding: utf-8 -*-
"""自动内存分配测试：auto_memory_mb 边界、resolve_memory 优先级、prepare 集成。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import mclauncher.launch_flow as lf
from mclauncher.config import CONFIG
from mclauncher.launch_flow import auto_memory_mb, resolve_memory


class _ConfigSandbox(unittest.TestCase):
    """隔离 CONFIG：测试期间不落盘，结束后还原 auto_memory。"""

    def setUp(self):
        self._orig = CONFIG.get("auto_memory", False)
        CONFIG.set("auto_memory", False)
        patcher = mock.patch.object(CONFIG, "save", lambda: None)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(lambda: CONFIG.set("auto_memory", self._orig))


class AutoMemoryMbTests(unittest.TestCase):
    def test_typical_16gb_machine(self):
        # 16 GB 总 / 8 GB 可用：60% = 4915 → 256 对齐 4864
        self.assertEqual(auto_memory_mb(16384, 8192), 4864)

    def test_low_memory_floor(self):
        # 4 GB 总 / 1.5 GB 可用：60% = 921，托底 1024
        self.assertEqual(auto_memory_mb(4096, 1536), 1024)

    def test_high_memory_cap(self):
        # 64 GB 总 / 48 GB 可用：不超过 12288 上限
        self.assertEqual(auto_memory_mb(65536, 49152), 12288)

    def test_cap_by_half_total(self):
        # 8 GB 总 / 7 GB 可用：60% = 4300，但上限是总内存一半 4096
        self.assertEqual(auto_memory_mb(8192, 7168), 4096)

    def test_unknown_memory_falls_back(self):
        self.assertEqual(auto_memory_mb(0, 0), 4096)
        self.assertEqual(auto_memory_mb(None, None), 4096)
        self.assertEqual(auto_memory_mb("?", "?"), 4096)

    def test_alignment(self):
        self.assertEqual(auto_memory_mb(16384, 8000) % 256, 0)


class ResolveMemoryTests(_ConfigSandbox):
    def test_version_setting_wins(self):
        CONFIG.set("auto_memory", True)
        mem, src = resolve_memory({"memory_mb": 6144}, 2048)
        self.assertEqual((mem, src), (6144, "version"))

    def test_auto_when_enabled(self):
        CONFIG.set("auto_memory", True)
        with mock.patch("mclauncher.sysinfo.memory_info",
                        return_value={"total_mb": 16384, "avail_mb": 8192}):
            mem, src = resolve_memory({}, 2048)
        self.assertEqual((mem, src), (4864, "auto"))

    def test_manual_when_disabled(self):
        mem, src = resolve_memory({}, 2048)
        self.assertEqual((mem, src), (2048, "manual"))

    def test_manual_none_gives_zero(self):
        mem, src = resolve_memory({}, None)
        self.assertEqual((mem, src), (0, "manual"))

    def test_auto_survives_sysinfo_failure(self):
        CONFIG.set("auto_memory", True)
        with mock.patch("mclauncher.sysinfo.memory_info",
                        side_effect=RuntimeError("boom")):
            mem, src = resolve_memory({}, 2048)
        self.assertEqual((mem, src), (4096, "auto"))


class _FakeInstance:
    def __init__(self, root: Path):
        self._root = root
        self.path = root

    def versions_dir(self) -> Path:
        return self._root / "versions"


class PrepareIntegrationTests(_ConfigSandbox):
    def _prepare(self, memory_mb=None):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "versions" / "1.20.1").mkdir(parents=True)
            return lf.prepare(_FakeInstance(root), "1.20.1", memory_mb=memory_mb)

    def test_manual_passthrough(self):
        prep = self._prepare(memory_mb=3072)
        self.assertEqual(prep["memory_mb"], 3072)
        self.assertEqual(prep["memory_source"], "manual")

    def test_auto_applied(self):
        CONFIG.set("auto_memory", True)
        with mock.patch("mclauncher.sysinfo.memory_info",
                        return_value={"total_mb": 16384, "avail_mb": 8192}):
            prep = self._prepare(memory_mb=3072)
        self.assertEqual(prep["memory_mb"], 4864)
        self.assertEqual(prep["memory_source"], "auto")

    def test_version_setting_beats_auto(self):
        from mclauncher import version_settings as vs
        CONFIG.set("auto_memory", True)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "versions" / "1.20.1").mkdir(parents=True)
            inst = _FakeInstance(root)
            vs.save(inst, "1.20.1", {"memory_mb": 6144})
            prep = lf.prepare(inst, "1.20.1", memory_mb=3072)
        self.assertEqual(prep["memory_mb"], 6144)
        self.assertEqual(prep["memory_source"], "version")


if __name__ == "__main__":
    unittest.main()
