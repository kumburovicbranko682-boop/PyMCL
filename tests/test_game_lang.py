# -*- coding: utf-8 -*-
"""首次启动游戏语言测试（对标 PCL2/HMCL：新版本第一次进游戏就是启动器语言）。"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mclauncher import launch_flow  # noqa: E402


class McMinorTest(unittest.TestCase):
    def test_parse_from_version_ids(self):
        f = launch_flow._mc_minor
        self.assertEqual(f("1.20.1"), 20)
        self.assertEqual(f("1.8.9-forge1.8.9-11.15.1.2318"), 8)
        self.assertEqual(f("fabric-loader-0.15.11-1.20.1"), 20)
        self.assertEqual(f("1.7.10"), 7)
        self.assertIsNone(f("neoforge-21.1.77"))
        self.assertIsNone(f(""))
        self.assertIsNone(f(None))

    def test_lang_code_casing(self):
        f = launch_flow._lang_code_for
        self.assertEqual(f("1.8.9", "zh_cn"), "zh_CN")     # 1.10 及以前带大写地区
        self.assertEqual(f("1.10.2", "zh_cn"), "zh_CN")
        self.assertEqual(f("1.11", "zh_cn"), "zh_cn")      # 1.11+ 全小写
        self.assertEqual(f("1.20.1-fabric", "zh_cn"), "zh_cn")
        self.assertEqual(f("unknown", "zh_cn"), "zh_cn")   # 解析不出按现代格式


class EnsureGameLanguageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, version="1.20.1", game_lang="auto", ui_lang="zh_CN"):
        cfg = {"game_lang": game_lang}
        with mock.patch.object(launch_flow.CONFIG, "get",
                               side_effect=lambda k, d=None: cfg.get(k, d)), \
                mock.patch("mclauncher.i18n.current_language", return_value=ui_lang):
            return launch_flow.ensure_game_language(self.dir, version)

    def test_first_launch_writes_lang(self):
        out = self._run()
        self.assertEqual(out, "zh_cn")
        self.assertEqual((self.dir / "options.txt").read_text(encoding="utf-8"),
                         "lang:zh_cn\n")

    def test_old_version_uses_uppercase_region(self):
        out = self._run(version="1.7.10-Forge10.13.4")
        self.assertEqual(out, "zh_CN")
        self.assertIn("lang:zh_CN", (self.dir / "options.txt").read_text(encoding="utf-8"))

    def test_never_overwrites_existing_options(self):
        (self.dir / "options.txt").write_text("lang:ja_jp\nfov:1.0\n", encoding="utf-8")
        out = self._run()
        self.assertIsNone(out)
        self.assertIn("lang:ja_jp", (self.dir / "options.txt").read_text(encoding="utf-8"))

    def test_english_launcher_skips(self):
        # 启动器是英文时原版默认就是英文，不写文件
        out = self._run(ui_lang="en")
        self.assertIsNone(out)
        self.assertFalse((self.dir / "options.txt").exists())

    def test_pref_off_skips(self):
        out = self._run(game_lang="off")
        self.assertIsNone(out)
        self.assertFalse((self.dir / "options.txt").exists())

    def test_pref_explicit_zh_ignores_ui_lang(self):
        out = self._run(game_lang="zh_cn", ui_lang="en")
        self.assertEqual(out, "zh_cn")

    def test_pref_en_us_skips(self):
        out = self._run(game_lang="en_us", ui_lang="zh_CN")
        self.assertIsNone(out)
        self.assertFalse((self.dir / "options.txt").exists())


class SettingsRoundTripTest(unittest.TestCase):
    """game_lang 必须从设置页 round-trip 进 config 并被 prepare 消费。"""

    def test_backend_save_and_get(self):
        try:
            from app.backend import BackendAPI
        except Exception as e:
            self.skipTest(f"app.backend 不可导入: {e}")
        from mclauncher.config import CONFIG
        old = CONFIG.get("game_lang")
        try:
            with mock.patch.object(CONFIG, "save"):
                BackendAPI.save_settings(None, {"game_lang": "zh_cn"})
                self.assertEqual(CONFIG.get("game_lang"), "zh_cn")
                BackendAPI.save_settings(None, {})   # 未提交时保持现值
                self.assertEqual(CONFIG.get("game_lang"), "zh_cn")
        finally:
            CONFIG.set("game_lang", old or "auto")

    def test_bridge_save_patch(self):
        try:
            from bridge.api import BackendAPI
        except Exception as e:
            self.skipTest(f"bridge.api 不可导入: {e}")
        from mclauncher.config import CONFIG
        old = CONFIG.get("game_lang")
        try:
            with mock.patch.object(CONFIG, "save"):
                BackendAPI.save_settings(None, {"game_lang": "off"})
                self.assertEqual(CONFIG.get("game_lang"), "off")
        finally:
            CONFIG.set("game_lang", old or "auto")


if __name__ == "__main__":
    unittest.main()
