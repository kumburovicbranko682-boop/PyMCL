# -*- coding: utf-8 -*-
"""游戏语言对齐启动器：options.txt 首次写入、不覆盖玩家选择、版本感知代码。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mclauncher import game_options, launch_flow
from mclauncher.instances import Instance


class TestMcLang(unittest.TestCase):
    def test_chinese_modern(self):
        self.assertEqual(game_options.mc_lang("zh_CN", "1.20.1"), "zh_cn")
        self.assertEqual(game_options.mc_lang("zh_CN", "fabric-loader-0.15-1.19.4"), "zh_cn")
        self.assertEqual(game_options.mc_lang("zh_CN", ""), "zh_cn")

    def test_chinese_legacy_casing(self):
        self.assertEqual(game_options.mc_lang("zh_CN", "1.8.9"), "zh_CN")
        self.assertEqual(game_options.mc_lang("zh_CN", "1.10.2"), "zh_CN")
        self.assertEqual(game_options.mc_lang("zh_CN", "1.11"), "zh_cn")

    def test_traditional_regions(self):
        self.assertEqual(game_options.mc_lang("zh_TW", "1.20.1"), "zh_tw")
        self.assertEqual(game_options.mc_lang("zh_HK", "1.8.9"), "zh_HK")

    def test_english_no_write(self):
        self.assertEqual(game_options.mc_lang("en", "1.20.1"), "")
        self.assertEqual(game_options.mc_lang("", "1.20.1"), "")


class TestEnsureLanguage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def read(self):
        return (self.dir / "options.txt").read_text("utf-8")

    def test_creates_file(self):
        wrote = game_options.ensure_language(self.dir, "1.20.1", "zh_CN")
        self.assertTrue(wrote)
        self.assertEqual(self.read(), "lang:zh_cn\n")

    def test_appends_when_no_lang(self):
        (self.dir / "options.txt").write_text("fov:0.5\nrenderDistance:12\n", "utf-8")
        wrote = game_options.ensure_language(self.dir, "1.20.1", "zh_CN")
        self.assertTrue(wrote)
        self.assertEqual(self.read(), "fov:0.5\nrenderDistance:12\nlang:zh_cn\n")

    def test_appends_without_trailing_newline(self):
        (self.dir / "options.txt").write_text("fov:0.5", "utf-8")
        game_options.ensure_language(self.dir, "1.20.1", "zh_CN")
        self.assertEqual(self.read(), "fov:0.5\nlang:zh_cn\n")

    def test_never_overwrites_player_choice(self):
        (self.dir / "options.txt").write_text("lang:en_us\nfov:0.5\n", "utf-8")
        wrote = game_options.ensure_language(self.dir, "1.20.1", "zh_CN")
        self.assertFalse(wrote)
        self.assertEqual(self.read(), "lang:en_us\nfov:0.5\n")

    def test_english_launcher_no_write(self):
        wrote = game_options.ensure_language(self.dir, "1.20.1", "en")
        self.assertFalse(wrote)
        self.assertFalse((self.dir / "options.txt").exists())

    def test_default_lang_from_i18n(self):
        with mock.patch("mclauncher.i18n.current_language", return_value="zh_CN"):
            wrote = game_options.ensure_language(self.dir, "1.20.1")
        self.assertTrue(wrote)
        self.assertIn("lang:zh_cn", self.read())


class TestPrepareIntegration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        patcher = mock.patch(
            "mclauncher.instances.get_instance_path",
            side_effect=lambda name: self.root / "instances" / name)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def test_prepare_writes_language(self):
        inst = Instance("lang-test")
        inst.create()
        (inst.versions_dir() / "1.20.1").mkdir(parents=True)
        with mock.patch("mclauncher.i18n.current_language", return_value="zh_CN"):
            prep = launch_flow.prepare(inst, "1.20.1")
        opts = Path(prep["game_dir"]) / "options.txt"
        self.assertTrue(opts.is_file())
        self.assertIn("lang:zh_cn", opts.read_text("utf-8"))


if __name__ == "__main__":
    unittest.main()
