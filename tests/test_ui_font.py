# -*- coding: utf-8 -*-
"""界面字体自定义（HMCL 设置「字体」同款）。

覆盖：配置默认值 / 主题包携带字体 / 两个门面的 get_settings、save_settings /
pcl_chrome.apply_ui_font 的字族排序。
"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from mclauncher import themes, utils
from mclauncher.config import CONFIG, DEFAULT_CONFIG


class _Isolated(unittest.TestCase):
    """utils.ROOT 和 CONFIG 指到临时目录，save() 不落盘。"""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        self.addCleanup(self._td.cleanup)
        data = {k: (list(v) if isinstance(v, list) else dict(v) if isinstance(v, dict) else v)
                for k, v in DEFAULT_CONFIG.items()}
        for p in (patch.object(utils, "ROOT", self.root),
                  patch.object(CONFIG, "data", data),
                  patch.object(CONFIG, "save", lambda: None)):
            p.start()
            self.addCleanup(p.stop)


class ConfigDefaultTests(unittest.TestCase):
    def test_default_empty(self):
        self.assertEqual(DEFAULT_CONFIG.get("ui_font_family"), "")


class ThemePackTests(_Isolated):
    """主题包必须携带 ui_font_family：保存时带出、加载时写回。"""

    def test_save_and_load_carries_font(self):
        CONFIG.set("ui_font_family", "LXGW WenKai")
        theme = themes.save_theme("字体主题")
        self.assertEqual(theme.get("ui_font_family"), "LXGW WenKai")

        CONFIG.set("ui_font_family", "")
        themes.load_theme("字体主题")
        self.assertEqual(CONFIG.get("ui_font_family"), "LXGW WenKai")

    def test_old_theme_without_font_keeps_current(self):
        # 旧版主题包没有 ui_font_family 键：加载时不得动当前设置
        theme = themes.save_theme("旧主题")
        path = themes._theme_path("旧主题")
        import json
        data = json.loads(path.read_text("utf-8"))
        data.pop("ui_font_family", None)
        path.write_text(json.dumps(data, ensure_ascii=False), "utf-8")

        CONFIG.set("ui_font_family", "Fira Sans")
        themes.load_theme("旧主题")
        self.assertEqual(CONFIG.get("ui_font_family"), "Fira Sans")
        self.assertIsInstance(theme, dict)


class BridgeFacadeTests(_Isolated):
    """无 Qt 门面：get_settings 带键、save_settings 局部更新 + strip。"""

    def setUp(self):
        super().setUp()
        from bridge.api import BackendAPI

        class _Bus:
            def emit(self, *a, **k):
                pass

        self.api = BackendAPI(_Bus())

    def test_get_settings_has_key(self):
        settings = self.api.get_settings()
        self.assertIn("ui_font_family", settings)
        self.assertEqual(settings["ui_font_family"], "")
        # 顺带补齐的主题键也必须在
        self.assertIn("theme_color", settings)
        self.assertIn("ui_background", settings)

    def test_save_strip_and_partial_update(self):
        self.api.save_settings({"ui_font_family": "  Fira Sans  "})
        self.assertEqual(CONFIG.get("ui_font_family"), "Fira Sans")
        # 没提交键 = 保持现值
        self.api.save_settings({"ui_dark": True})
        self.assertEqual(CONFIG.get("ui_font_family"), "Fira Sans")
        # 提交空串 = 清空（恢复默认字族）
        self.api.save_settings({"ui_font_family": ""})
        self.assertEqual(CONFIG.get("ui_font_family"), "")


class QtFacadeTests(_Isolated):
    """Qt 门面按既有契约以 self=None 直调（save_settings 只依赖模块级 CONFIG）。"""

    def setUp(self):
        super().setUp()
        # save_settings 尾部会预热下载源探测，测试里掐掉网络副作用
        for target in ("mclauncher.source.invalidate_probe",
                       "mclauncher.source.warmup_async",
                       "mclauncher.net.apply_proxy_policy"):
            p = patch(target, lambda *a, **k: None)
            p.start()
            self.addCleanup(p.stop)

    def test_round_trip(self):
        from app.backend import BackendAPI as QtBackend
        settings = QtBackend.get_settings(None)
        self.assertEqual(settings.get("ui_font_family"), "")

        QtBackend.save_settings(None, {"ui_font_family": " 更纱黑体 "})
        self.assertEqual(CONFIG.get("ui_font_family"), "更纱黑体")
        self.assertEqual(QtBackend.get_settings(None).get("ui_font_family"), "更纱黑体")

        # 局部更新：别的键不动字体
        QtBackend.save_settings(None, {"theme_color": "#123456"})
        self.assertEqual(CONFIG.get("ui_font_family"), "更纱黑体")

        QtBackend.save_settings(None, {"ui_font_family": ""})
        self.assertEqual(CONFIG.get("ui_font_family"), "")


class ApplyUiFontTests(_Isolated):
    """apply_ui_font 把自定义字体排到 Fluent 字族最前；空值恢复默认。"""

    def test_families_order(self):
        from app.pcl_chrome import FLUENT_FONT_STACK, apply_ui_font
        from qfluentwidgets import fontFamilies

        CONFIG.set("ui_font_family", "LXGW WenKai")
        apply_ui_font()
        fams = fontFamilies()
        self.assertEqual(fams[0], "LXGW WenKai")
        for f in FLUENT_FONT_STACK:
            self.assertIn(f, fams)

        CONFIG.set("ui_font_family", "")
        apply_ui_font()
        self.assertEqual(fontFamilies(), list(FLUENT_FONT_STACK))

    def test_custom_equal_to_default_not_duplicated(self):
        from app.pcl_chrome import apply_ui_font
        from qfluentwidgets import fontFamilies

        CONFIG.set("ui_font_family", "Microsoft YaHei")
        apply_ui_font()
        fams = fontFamilies()
        self.assertEqual(fams[0], "Microsoft YaHei")
        self.assertEqual(fams.count("Microsoft YaHei"), 1)


if __name__ == "__main__":
    unittest.main()
