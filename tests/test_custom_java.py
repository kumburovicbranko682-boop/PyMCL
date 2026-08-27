from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mclauncher import java as java_mod
from mclauncher.config import CONFIG


class _ConfigSandbox(unittest.TestCase):
    """隔离 CONFIG：测试期间不落盘，结束后还原 custom_javas。"""

    def setUp(self):
        self._orig = CONFIG.get("custom_javas")
        CONFIG.set("custom_javas", [])
        patcher = mock.patch.object(CONFIG, "save", lambda: None)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(lambda: CONFIG.set("custom_javas", self._orig))


def _fake_java(root: Path, name="java") -> Path:
    p = root / name
    p.write_text("#!/bin/sh\necho fake\n")
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return p


class AddCustomJavaTests(_ConfigSandbox):
    def test_add_probes_and_persists(self):
        with tempfile.TemporaryDirectory() as td:
            exe = _fake_java(Path(td))
            with mock.patch.object(java_mod, "get_java_major", return_value=17):
                entry = java_mod.add_custom_java(str(exe))
        self.assertEqual(entry["major"], 17)
        self.assertEqual(entry["exe"], str(exe.resolve()))
        stored = CONFIG.get("custom_javas")
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["major"], 17)

    def test_add_rejects_missing_file(self):
        with self.assertRaises(ValueError):
            java_mod.add_custom_java("/no/such/java")

    def test_add_rejects_non_java_binary(self):
        with tempfile.TemporaryDirectory() as td:
            exe = _fake_java(Path(td), name="python3")
            with self.assertRaises(ValueError):
                java_mod.add_custom_java(str(exe))

    def test_add_rejects_unprobeable(self):
        with tempfile.TemporaryDirectory() as td:
            exe = _fake_java(Path(td))
            with mock.patch.object(java_mod, "get_java_major", return_value=None):
                with self.assertRaises(ValueError):
                    java_mod.add_custom_java(str(exe))

    def test_add_dedupes_same_path(self):
        with tempfile.TemporaryDirectory() as td:
            exe = _fake_java(Path(td))
            with mock.patch.object(java_mod, "get_java_major", return_value=17):
                java_mod.add_custom_java(str(exe))
                java_mod.add_custom_java(str(exe))
        self.assertEqual(len(CONFIG.get("custom_javas")), 1)


class CustomJavaListTests(_ConfigSandbox):
    def test_listed_and_removed(self):
        with tempfile.TemporaryDirectory() as td:
            exe = _fake_java(Path(td))
            CONFIG.set("custom_javas", [
                {"exe": str(exe), "major": 21, "name": "Java 21（手动添加）"},
                {"exe": "/gone/java", "major": 8, "name": "Java 8"},
            ])
            rows = java_mod.custom_javas()
            # 失踪的文件不列出
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["major"], 21)
            self.assertTrue(rows[0]["custom"])

            # all_javas / cached_all_javas 都包含手动项
            self.assertIn(str(exe), [j["exe"] for j in java_mod.cached_all_javas()])

            self.assertTrue(java_mod.remove_custom_java(str(exe)))
            self.assertFalse(java_mod.remove_custom_java(str(exe)))
            self.assertEqual(
                [e["exe"] for e in CONFIG.get("custom_javas")], ["/gone/java"])


if __name__ == "__main__":
    unittest.main()
