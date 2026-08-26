# -*- coding: utf-8 -*-
"""指路文案一致性：让人「去装版本」的每句话都指向真实存在的地方。

侧栏里没有「版本」页，启动页也装不了版本——正确路标只有一个：
「下载 → 原版游戏」。本测试静态扫描 Qt 产品线源码，防止错误路标回潮，
并保证统一后的新句子进了英文目录。
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Qt 产品线的用户可见源码（桥接层 bridge/winui3/wpf/android 与备份目录不在此列）
SCAN_DIRS = [ROOT / "app", ROOT / "mclauncher"]

# 指向不存在页面 / 做不了该事的页面的路标
FORBIDDEN = [
    "到「版本」页",
    "在版本页选择",
    "「启动」页安装",
]

CANONICAL = "「下载 → 原版游戏」"

# 统一后的句子必须留在英文目录里，否则英文界面又冒中文
EN_REQUIRED = [
    "还没有安装任何版本。请先到「下载 → 原版游戏」安装一个版本。",
    "请先选择版本；还没有版本时，到「下载 → 原版游戏」安装",
    "请先到「下载 → 原版游戏」安装一个版本",
]


def _py_files():
    for base in SCAN_DIRS:
        yield from base.rglob("*.py")


class WayfindingCopyTests(unittest.TestCase):
    def test_no_stale_pointers(self):
        bad = []
        for f in _py_files():
            text = f.read_text("utf-8", errors="replace")
            for phrase in FORBIDDEN:
                if phrase in text:
                    bad.append(f"{f.relative_to(ROOT)}: {phrase}")
        self.assertEqual(bad, [], "发现指向不存在页面的路标")

    def test_canonical_path_used_where_it_matters(self):
        for f in (ROOT / "app" / "backend.py",
                  ROOT / "app" / "pages" / "launch_page.py",
                  ROOT / "mclauncher" / "preflight.py"):
            self.assertIn(CANONICAL, f.read_text("utf-8"),
                          f"{f.name} 里的装版本路标应统一为 {CANONICAL}")

    def test_en_catalog_has_new_sentences(self):
        data = json.loads((ROOT / "mclauncher" / "locales" / "en.json")
                          .read_text("utf-8"))
        for key in EN_REQUIRED:
            self.assertIn(key, data)
            self.assertTrue(str(data[key]).strip())


if __name__ == "__main__":
    unittest.main(verbosity=2)
