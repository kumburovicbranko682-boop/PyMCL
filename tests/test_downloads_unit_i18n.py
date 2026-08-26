# -*- coding: utf-8 -*-
"""下载量单位必须跟随界面语言，英文界面不能冒出「万 / 亿」。

钉住的行为：
- 中文界面：万 / 亿；
- 英文界面：K / M / B；
- file_pick 与 catalog_page 共用同一份实现，不再各自漂移。

纯逻辑测试 + offscreen 环境，不弹窗、不联网。
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYMCL_HOME", tempfile.mkdtemp(prefix="pymcl_test_dlunit_"))

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mclauncher import i18n  # noqa: E402


class DownloadsUnitTests(unittest.TestCase):
    def tearDown(self):
        i18n.set_language("zh_CN")

    def test_chinese_units(self):
        i18n.set_language("zh_CN")
        from app.pages.catalog_page import fmt_downloads
        self.assertEqual(fmt_downloads(999), "999")
        self.assertEqual(fmt_downloads(15_300), "2万")
        self.assertEqual(fmt_downloads(1_200_000), "120万")
        self.assertEqual(fmt_downloads(100_000_000), "1亿")
        self.assertEqual(fmt_downloads(230_000_000), "2.3亿")
        self.assertEqual(fmt_downloads(0), "—")
        self.assertEqual(fmt_downloads("junk"), "—")

    def test_english_units(self):
        i18n.set_language("en")
        from app.pages.catalog_page import fmt_downloads
        self.assertEqual(fmt_downloads(999), "999")
        self.assertEqual(fmt_downloads(15_300), "15.3K")
        self.assertEqual(fmt_downloads(1_200_000), "1.2M")
        self.assertEqual(fmt_downloads(2_000_000_000), "2B")
        for n in (15_300, 1_200_000, 100_000_000):
            out = fmt_downloads(n)
            self.assertNotIn("万", out, f"英文界面出现中文单位: {out}")
            self.assertNotIn("亿", out, f"英文界面出现中文单位: {out}")

    def test_file_pick_shares_the_same_formatter(self):
        from app.pages import catalog_page, file_pick
        self.assertIs(file_pick.fmt_downloads, catalog_page.fmt_downloads,
                      "两处格式化必须是同一份实现，避免再次漂移")


if __name__ == "__main__":
    unittest.main(verbosity=2)
