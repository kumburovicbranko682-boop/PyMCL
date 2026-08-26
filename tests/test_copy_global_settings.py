# -*- coding: utf-8 -*-
"""「复制全局游戏设置」（HMCL 3.6.12 版本特定设置同款）。

覆盖：version_settings.from_global() 的键值折算（auto 的取舍、
show_log 布尔转 on/off、全屏别名归一），以及版本设置对话框
点按钮后各控件 → payload() 的整条链路。
"""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _patched_config(**overrides):
    from mclauncher.config import CONFIG, DEFAULT_CONFIG
    data = {k: (list(v) if isinstance(v, list) else dict(v) if isinstance(v, dict) else v)
            for k, v in DEFAULT_CONFIG.items()}
    data.update(overrides)
    return patch.object(CONFIG, "data", data)


class FromGlobalTests(unittest.TestCase):
    def test_defaults_map_to_follow_or_off(self):
        from mclauncher import version_settings as vs
        with _patched_config():
            g = vs.from_global()
        self.assertEqual(g["isolation"], "none")
        self.assertEqual(g["java"], vs.DEFAULTS["java"])
        self.assertEqual(g["gc"], "auto")        # auto 是版本级合法 GC 预设
        self.assertEqual(g["gpu"], "")           # gpu 的 auto 没有版本级等价 → 跟随全局
        self.assertEqual(g["renderer"], "auto")
        self.assertEqual(g["show_log"], "off")
        self.assertEqual(g["window_mode"], "window")
        self.assertEqual(g["offline_skin"], "default")

    def test_custom_globals_pass_through(self):
        from mclauncher import version_settings as vs
        with _patched_config(default_isolation="all", memory_mb=6144,
                             default_java="/opt/jdk/bin/java",
                             default_jvm_args="  -XX:+UseZGC  ",
                             default_priority="high", gc_preset="zgc",
                             gpu_mode="discrete", renderer="zink",
                             show_log_window=True, window_mode="fullscreen",
                             width=1920, height=1080, offline_skin="alex"):
            g = vs.from_global()
        self.assertEqual(g["isolation"], "all")
        self.assertEqual(g["memory_mb"], 6144)
        self.assertEqual(g["java"], "/opt/jdk/bin/java")
        self.assertEqual(g["jvm_args"], "-XX:+UseZGC")
        self.assertEqual(g["process_priority"], "high")
        self.assertEqual(g["gc"], "zgc")
        self.assertEqual(g["gpu"], "discrete")
        self.assertEqual(g["renderer"], "zink")
        self.assertEqual(g["show_log"], "on")
        self.assertEqual(g["window_mode"], "maximize")  # fullscreen 别名归一
        self.assertEqual(g["window_width"], 1920)
        self.assertEqual(g["window_height"], 1080)
        self.assertEqual(g["offline_skin"], "alex")

    def test_zero_memory_means_unset(self):
        from mclauncher import version_settings as vs
        with _patched_config(memory_mb=0, width=0, height=0):
            g = vs.from_global()
        self.assertIsNone(g["memory_mb"])
        self.assertIsNone(g["window_width"])
        self.assertIsNone(g["window_height"])


class FakeBackend:
    def get_version_settings(self, inst, ver):
        return {}

    def java_combo_options(self, inst, scan):
        return [{"label": "自动选择", "value": "自动选择"}]

    def get_accounts(self):
        return []

    def global_version_defaults(self):
        return {
            "isolation": "all", "memory_mb": 6144, "java": "自动选择",
            "jvm_args": "-XX:+UseZGC", "process_priority": "high",
            "gc": "zgc", "gpu": "discrete", "renderer": "zink",
            "show_log": "on", "window_mode": "maximize",
            "window_width": 1920, "window_height": 1080, "offline_skin": "alex",
        }


class CopyGlobalDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def _dialog(self):
        from PySide6.QtWidgets import QWidget
        from app.pages.version_setup import VersionSetupDialog
        self._host = QWidget()
        return VersionSetupDialog(FakeBackend(), "default", "1.21.4",
                                  parent=self._host)

    def test_copy_global_fills_controls_and_payload(self):
        dlg = self._dialog()
        dlg._copy_global()
        p = dlg.payload()
        self.assertEqual(p["isolation"], "all")
        self.assertEqual(p["memory_mb"], 6144)
        self.assertEqual(p["jvm_args"], "-XX:+UseZGC")
        self.assertEqual(p["process_priority"], "high")
        self.assertEqual(p["gc"], "zgc")
        self.assertEqual(p["gpu"], "discrete")
        self.assertEqual(p["renderer"], "zink")
        self.assertEqual(p["show_log"], "on")
        self.assertEqual(p["window_mode"], "maximize")
        self.assertEqual(p["window_width"], 1920)
        self.assertEqual(p["window_height"], 1080)
        self.assertEqual(p["offline_skin"], "alex")

    def test_manual_fields_untouched(self):
        """没有全局对应项的字段（服务器/账号/前后命令）不能被覆盖。"""
        dlg = self._dialog()
        dlg.server.setText("play.example.com")
        dlg.pre.setText("echo hi")
        dlg.game.setText("--demo")
        dlg._copy_global()
        p = dlg.payload()
        self.assertEqual(p["server"], "play.example.com")
        self.assertEqual(p["pre_launch"], "echo hi")
        self.assertEqual(p["game_args"], "--demo")


if __name__ == "__main__":
    unittest.main()
