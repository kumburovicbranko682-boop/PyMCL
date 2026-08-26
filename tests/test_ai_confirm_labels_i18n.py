# -*- coding: utf-8 -*-
"""AI 助手的确认卡片 / 工具状态行文案必须跟随界面语言：
英文界面下「安装模组 X → 默认实例」这类 confirm_label 不许冒中文。

纯逻辑测试 + offscreen，不弹任何窗口、不联网。
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYMCL_HOME", tempfile.mkdtemp(prefix="pymcl_test_ai_"))

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mclauncher import i18n  # noqa: E402
from mclauncher.ai.tools import confirm_label  # noqa: E402

_CJK = re.compile(r"[\u4e00-\u9fff]")

_CASES = [
    ("install_game", {"version": "1.20.1", "loader": "fabric"}),
    ("install_mod", {"name": "sodium"}),
    ("install_modpack", {"name": "ATM9", "instance": "atm"}),
    ("install_shader", {"name": "BSL"}),
    ("install_resourcepack", {"name": "Faithful"}),
    ("install_datapack", {"name": "vanilla-tweaks"}),
    ("download_java", {"major": 21}),
    ("launch_game", {}),
    ("create_instance", {"name": "new"}),
    ("delete_instance", {"name": "old"}),
    ("delete_mod", {"filename": "a.jar"}),
    ("disable_mod", {"filename": "a.jar"}),
    ("enable_mod", {"filename": "a.jar"}),
    ("write_mod_config", {"path": "config/x.toml"}),
    ("some_unknown_tool", {}),
]


class ConfirmLabelEnglishTests(unittest.TestCase):
    def setUp(self):
        i18n.set_language("en")

    def tearDown(self):
        i18n.set_language("zh_CN")

    def test_labels_english(self):
        for name, args in _CASES:
            label = confirm_label(name, args)
            self.assertTrue(label, f"{name} 应有文案")
            self.assertIsNone(_CJK.search(label),
                              f"英文界面下 AI 确认文案仍是中文: {name} -> {label!r}")

    def test_labels_keep_arguments(self):
        self.assertIn("sodium", confirm_label("install_mod", {"name": "sodium"}))
        self.assertIn("21", confirm_label("download_java", {"major": 21}))
        self.assertIn("atm", confirm_label(
            "install_modpack", {"name": "ATM9", "instance": "atm"}))


class ConfirmLabelChineseTests(unittest.TestCase):
    def test_labels_stay_chinese(self):
        i18n.set_language("zh_CN")
        label = confirm_label("install_mod", {"name": "sodium"})
        self.assertIn("安装模组", label)
        self.assertIn("默认实例", label)


if __name__ == "__main__":
    unittest.main(verbosity=2)
