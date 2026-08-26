# -*- coding: utf-8 -*-
"""i18n catalog loading (also used by the frozen exe)."""
from __future__ import annotations

import unittest
from pathlib import Path

from mclauncher import i18n


class LocalesDirTests(unittest.TestCase):
    def test_source_tree_catalogs_exist(self):
        base = i18n._locales_dir()
        self.assertTrue(base.is_dir(), base)
        self.assertTrue((base / "zh_CN.json").is_file())
        self.assertTrue((base / "en.json").is_file())

    def test_meipass_override(self):
        import sys
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            bundled = Path(raw) / "mclauncher" / "locales"
            bundled.mkdir(parents=True)
            (bundled / "zh_CN.json").write_text('{"启动游戏": "启动游戏"}', encoding="utf-8")
            old = getattr(sys, "_MEIPASS", None)
            sys._MEIPASS = raw
            try:
                self.assertEqual(i18n._locales_dir(), bundled)
            finally:
                if old is None:
                    delattr(sys, "_MEIPASS")
                else:
                    sys._MEIPASS = old

    def test_available_languages(self):
        langs = i18n.available_languages()
        self.assertIn("zh_CN", langs)
        self.assertIn("en", langs)


if __name__ == "__main__":
    unittest.main()
