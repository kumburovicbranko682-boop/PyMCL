# -*- coding: utf-8 -*-
"""手动添加 / 移除自定义 Java。"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mclauncher import java as java_mod


class _FakeConfig:
    def __init__(self):
        self.data = {}
        self.saved = 0
        self.java_dir = Path(tempfile.mkdtemp(prefix="pymcl-java-"))

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value

    def save(self):
        self.saved += 1


class CustomJavaTests(unittest.TestCase):
    def setUp(self):
        self.cfg = _FakeConfig()
        self.patcher = mock.patch.object(java_mod, "CONFIG", self.cfg)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()

    def test_add_real_system_java(self):
        real = shutil.which("java")
        if not real:
            self.skipTest("本机没有 java")
        info = java_mod.add_custom_java(real)
        self.assertTrue(info["custom"])
        self.assertEqual(info["exe"], str(Path(real)))
        self.assertIsInstance(info["major"], int)
        self.assertIn(str(Path(real)), self.cfg.data["custom_javas"])
        self.assertGreaterEqual(self.cfg.saved, 1)
        # 重复添加不产生重复项
        java_mod.add_custom_java(real)
        self.assertEqual(len(self.cfg.data["custom_javas"]), 1)
        # 出现在 custom_javas() 与 all_javas()
        listed = java_mod.custom_javas()
        self.assertEqual(len(listed), 1)
        self.assertTrue(any(j.get("custom") for j in java_mod.all_javas()))
        # 移除
        java_mod.remove_custom_java(real)
        self.assertEqual(self.cfg.data["custom_javas"], [])
        self.assertEqual(java_mod.custom_javas(), [])

    def test_rejects_missing_file(self):
        with self.assertRaises(java_mod.JavaError):
            java_mod.add_custom_java("/nonexistent/java")

    def test_rejects_non_java_executable(self):
        with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as f:
            f.write("#!/bin/sh\necho not java\n")
            path = f.name
        Path(path).chmod(0o755)
        try:
            with self.assertRaises(java_mod.JavaError) as ctx:
                java_mod.add_custom_java(path)
            self.assertIn("Java", str(ctx.exception))
            self.assertNotIn("custom_javas", self.cfg.data)
        finally:
            Path(path).unlink()

    def test_missing_path_skipped_but_kept(self):
        self.cfg.data["custom_javas"] = ["/gone/java", ]
        self.assertEqual(java_mod.custom_javas(), [])
        # 配置未被清掉（可能是可移动盘）
        self.assertEqual(self.cfg.data["custom_javas"], ["/gone/java"])


if __name__ == "__main__":
    unittest.main()
