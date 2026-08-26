# -*- coding: utf-8 -*-
"""实例页文案：和真实行为一致，且全部走翻译。

钉住的行为：
- 「自动选择」的解释不再声称「1.19+ 用 17」（实际按版本 JSON 的
  javaVersion 匹配，1.20.5+ 要 21；Java 页也说的是「按需自动匹配下载」）；
- 实例卡的「N 个版本」「Java · 自动选择」走 tr()，英文界面不再夹生中文；
- 删除确认、Java 选择说明的键在两份语言文件里都有。

全程 offscreen，后端打桩，不弹对话框。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYMCL_HOME", tempfile.mkdtemp(prefix="pymcl_test_icopy_"))

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mclauncher import feedback as _fb  # noqa: E402

_fb.start_heartbeat = lambda *a, **k: None
_fb.stop_heartbeat = lambda *a, **k: None

from mclauncher.i18n import tr  # noqa: E402

_app = None


def setUpModule():
    global _app
    from PySide6.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication([])


class InstanceCopyTests(unittest.TestCase):
    def test_no_stale_java_claim(self):
        src = (ROOT / "app" / "pages" / "instance_page.py").read_text(encoding="utf-8")
        self.assertNotIn("1.19+", src,
                         "「1.19+ 用 17」和实际匹配逻辑（1.20.5+ 要 21）矛盾，不要复活")
        self.assertIn("tr(\"实例「{0}」启动时使用的 Java", src,
                      "Java 选择说明必须走翻译")

    def test_locale_keys_exist(self):
        keys = [
            "{0} 个版本",
            "确定删除实例「{0}」？其中的存档与配置将一并移除。",
            "实例「{0}」启动时使用的 Java。自动选择会按所选版本的要求匹配，缺了会在启动时自动下载。",
        ]
        for loc in ("en.json", "zh_CN.json"):
            data = json.loads((ROOT / "mclauncher" / "locales" / loc).read_text(encoding="utf-8"))
            for k in keys:
                self.assertIn(k, data, f"{loc} 缺少键：{k}")

    def test_card_renders_translated_text(self):
        from unittest import mock
        from PySide6.QtWidgets import QWidget
        from qfluentwidgets import CaptionLabel
        from app.backend import BackendAPI
        p = mock.patch.object(
            BackendAPI, "get_instances",
            lambda self: [{"name": "default", "versions": 2,
                           "mc": "1.20.1", "java_label": ""}])
        p.start()
        self.addCleanup(p.stop)
        host = QWidget()
        host.resize(1000, 700)
        self.addCleanup(host.deleteLater)
        from app.pages.instance_page import InstancePage
        page = InstancePage(BackendAPI(), host)
        _app.processEvents()
        texts = [w.text() for w in page.findChildren(CaptionLabel)]
        self.assertIn(tr("{0} 个版本").format(2), texts,
                      "版本数应经 tr() 渲染")
        self.assertIn("Java · " + tr("自动选择"), texts,
                      "未指定 Java 时应显示翻译后的「自动选择」")


if __name__ == "__main__":
    unittest.main(verbosity=2)
