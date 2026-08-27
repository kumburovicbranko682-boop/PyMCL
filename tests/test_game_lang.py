# -*- coding: utf-8 -*-
"""首次启动自动设置游戏语言（game_options）。"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mclauncher import game_options  # noqa: E402


class _Inst:
    """最小实例桩：只提供 version_json。"""

    def __init__(self, jsons=None):
        self._jsons = jsons or {}

    def version_json(self, vid):
        return self._jsons.get(vid)


class LangCodeTests(unittest.TestCase):
    def test_modern_versions_lowercase(self):
        self.assertEqual(game_options.lang_code_for_version("zh_cn", "1.11"), "zh_cn")
        self.assertEqual(game_options.lang_code_for_version("zh_cn", "1.20.1"), "zh_cn")
        self.assertEqual(game_options.lang_code_for_version("ZH_CN", "1.20.1"), "zh_cn")

    def test_legacy_versions_upper_region(self):
        self.assertEqual(game_options.lang_code_for_version("zh_cn", "1.10.2"), "zh_CN")
        self.assertEqual(game_options.lang_code_for_version("zh_cn", "1.7.10"), "zh_CN")
        self.assertEqual(game_options.lang_code_for_version("en_us", "1.8.9"), "en_US")

    def test_unknown_version_treated_as_modern(self):
        self.assertEqual(game_options.lang_code_for_version("zh_cn", None), "zh_cn")
        self.assertEqual(game_options.lang_code_for_version("zh_cn", "23w31a"), "zh_cn")

    def test_empty_lang(self):
        self.assertEqual(game_options.lang_code_for_version("", "1.20.1"), "")


class TargetLangTests(unittest.TestCase):
    def test_auto_follows_launcher_chinese(self):
        self.assertEqual(game_options.target_lang("auto", launcher_lang="zh_CN"), "zh_cn")

    def test_auto_english_is_noop(self):
        # MC 出厂默认就是 en_us，auto+英文启动器没必要写
        self.assertEqual(game_options.target_lang("auto", launcher_lang="en"), "")

    def test_off_disables(self):
        self.assertEqual(game_options.target_lang("off", launcher_lang="zh_CN"), "")

    def test_explicit_code(self):
        self.assertEqual(game_options.target_lang("zh_cn", launcher_lang="en"), "zh_cn")
        self.assertEqual(game_options.target_lang("en_us", launcher_lang="zh_CN"), "en_us")

    def test_default_reads_config(self):
        with patch.object(game_options.CONFIG, "get", return_value="off"):
            self.assertEqual(game_options.target_lang(launcher_lang="zh_CN"), "")


class EnsureLangTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.gdir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _options(self) -> Path:
        return self.gdir / "options.txt"

    def test_creates_file_when_missing(self):
        code = game_options.ensure_lang(self.gdir, mc_version="1.20.1",
                                        setting="auto", launcher_lang="zh_CN")
        self.assertEqual(code, "zh_cn")
        self.assertEqual(self._options().read_text("utf-8"), "lang:zh_cn\n")

    def test_legacy_casing_written(self):
        code = game_options.ensure_lang(self.gdir, mc_version="1.7.10",
                                        setting="auto", launcher_lang="zh_CN")
        self.assertEqual(code, "zh_CN")
        self.assertIn("lang:zh_CN", self._options().read_text("utf-8"))

    def test_appends_when_no_lang_line(self):
        self._options().write_text("fov:0.0\nfullscreen:false", encoding="utf-8")
        code = game_options.ensure_lang(self.gdir, mc_version="1.20.1",
                                        setting="auto", launcher_lang="zh_CN")
        self.assertEqual(code, "zh_cn")
        text = self._options().read_text("utf-8")
        self.assertIn("fov:0.0", text)
        self.assertTrue(text.endswith("lang:zh_cn\n"))
        # 追加时补了换行，原最后一行没被粘住
        self.assertIn("fullscreen:false\nlang:zh_cn", text)

    def test_never_overwrites_existing_lang(self):
        self._options().write_text("lang:ja_jp\nfov:0.0\n", encoding="utf-8")
        code = game_options.ensure_lang(self.gdir, mc_version="1.20.1",
                                        setting="auto", launcher_lang="zh_CN")
        self.assertEqual(code, "")
        self.assertEqual(self._options().read_text("utf-8"), "lang:ja_jp\nfov:0.0\n")

    def test_off_setting_touches_nothing(self):
        code = game_options.ensure_lang(self.gdir, mc_version="1.20.1",
                                        setting="off", launcher_lang="zh_CN")
        self.assertEqual(code, "")
        self.assertFalse(self._options().exists())

    def test_auto_english_touches_nothing(self):
        code = game_options.ensure_lang(self.gdir, mc_version="1.20.1",
                                        setting="auto", launcher_lang="en")
        self.assertEqual(code, "")
        self.assertFalse(self._options().exists())

    def test_creates_missing_game_dir(self):
        sub = self.gdir / "versions" / "1.20.1"
        code = game_options.ensure_lang(sub, mc_version="1.20.1",
                                        setting="zh_cn", launcher_lang="en")
        self.assertEqual(code, "zh_cn")
        self.assertTrue((sub / "options.txt").is_file())


class BaseMcVersionTests(unittest.TestCase):
    def test_plain_release_id(self):
        self.assertEqual(game_options.base_mc_version(_Inst(), "1.20.1"), "1.20.1")

    def test_loader_prefixed_id(self):
        # mc_version_tuple 能从 1.20.1-forge-47.2.0 前缀解析
        self.assertEqual(game_options.base_mc_version(_Inst(), "1.20.1-forge-47.2.0"),
                         "1.20.1-forge-47.2.0")
        self.assertEqual(game_options.lang_code_for_version("zh_cn", "1.20.1-forge-47.2.0"),
                         "zh_cn")

    def test_inherits_chain(self):
        inst = _Inst({
            "fabric-loader-0.15.0-mypack": {"inheritsFrom": "1.10.2"},
        })
        self.assertEqual(game_options.base_mc_version(inst, "fabric-loader-0.15.0-mypack"),
                         "1.10.2")

    def test_unresolvable_returns_empty(self):
        self.assertEqual(game_options.base_mc_version(_Inst(), "custompack"), "")
        self.assertEqual(game_options.base_mc_version(_Inst(), ""), "")

    def test_cycle_is_safe(self):
        inst = _Inst({
            "a": {"inheritsFrom": "b"},
            "b": {"inheritsFrom": "a"},
        })
        self.assertEqual(game_options.base_mc_version(inst, "a"), "")


class PrepareIntegrationTests(unittest.TestCase):
    """launch_flow.prepare 真的会写 options.txt 并带回 game_lang。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_prepare_writes_lang(self):
        from mclauncher import launch_flow

        class _FullInst:
            def __init__(self, path):
                self.path = path
                self.name = "t"

            def versions_dir(self):
                return self.path / "versions"

            def version_json(self, vid):
                return None

        inst = _FullInst(self.root)
        (self.root / "versions" / "1.20.1").mkdir(parents=True)
        with patch.object(game_options, "target_lang", return_value="zh_cn"):
            prep = launch_flow.prepare(inst, "1.20.1")
        self.assertEqual(prep.get("game_lang"), "zh_cn")
        self.assertEqual((self.root / "options.txt").read_text("utf-8"), "lang:zh_cn\n")

    def test_prepare_existing_lang_untouched(self):
        from mclauncher import launch_flow

        class _FullInst:
            def __init__(self, path):
                self.path = path
                self.name = "t"

            def versions_dir(self):
                return self.path / "versions"

            def version_json(self, vid):
                return None

        inst = _FullInst(self.root)
        (self.root / "versions" / "1.20.1").mkdir(parents=True)
        (self.root / "options.txt").write_text("lang:en_gb\n", encoding="utf-8")
        with patch.object(game_options, "target_lang", return_value="zh_cn"):
            prep = launch_flow.prepare(inst, "1.20.1")
        self.assertEqual(prep.get("game_lang"), "")
        self.assertEqual((self.root / "options.txt").read_text("utf-8"), "lang:en_gb\n")


if __name__ == "__main__":
    unittest.main()
