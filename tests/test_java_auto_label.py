# -*- coding: utf-8 -*-
"""Java「自动选择」：给人看的标签跟语言走，存盘的哨兵永远不变。

钉住的行为：
- Java 下拉框第一项的 label 走 tr()（英文界面显示 Auto，
  之前直接把内部哨兵「自动选择」当标签，英文下也是中文）；
- value 始终是内部哨兵 JAVA_AUTO，切语言不影响已保存的偏好；
- 启动页把翻译后的标签选回去时，写进实例的仍是哨兵而非译文。

全程 offscreen，Java 扫描走本地缓存，不联网。
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYMCL_HOME", tempfile.mkdtemp(prefix="pymcl_test_javaauto_"))

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mclauncher import feedback as _fb  # noqa: E402

_fb.start_heartbeat = lambda *a, **k: None
_fb.stop_heartbeat = lambda *a, **k: None

from mclauncher import i18n  # noqa: E402
from mclauncher.config import CONFIG  # noqa: E402
from mclauncher.i18n import tr  # noqa: E402
from mclauncher.instances import JAVA_AUTO  # noqa: E402

_app = None


def setUpModule():
    global _app
    CONFIG.set("first_run", False)
    CONFIG.set("feedback_consent", False)
    CONFIG.set("auto_check_update", False)
    CONFIG.save()
    from PySide6.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication([])


class JavaAutoLabelTests(unittest.TestCase):
    def tearDown(self):
        i18n.set_language("zh_CN")

    def test_label_translated_value_stable(self):
        from app.backend import BackendAPI
        b = BackendAPI()
        for lang in ("zh_CN", "en"):
            i18n.set_language(lang)
            opts = b.java_combo_options("default", scan_system=False)
            self.assertEqual(opts[0]["label"], tr("自动选择"),
                             f"[{lang}] 第一项标签应跟语言走")
            self.assertEqual(opts[0]["value"], JAVA_AUTO,
                             f"[{lang}] value 必须是稳定哨兵")
            self.assertEqual(b.instance_java_label("default"), tr("自动选择"))

    def test_selecting_translated_label_stores_sentinel(self):
        from unittest import mock
        from app.backend import BackendAPI

        i18n.set_language("en")
        patches = [
            mock.patch.object(BackendAPI, "fetch_news", lambda self: []),
            mock.patch.object(BackendAPI, "cached_news", lambda self: []),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

        from app.pages.launch_page import LaunchPage
        backend = BackendAPI()
        page = LaunchPage(backend)
        _app.processEvents()
        page.reload()
        _app.processEvents()

        self.assertEqual(page.java_box.currentText(), tr("自动选择"),
                         "英文界面下 Java 下拉框应显示译文而非中文哨兵")
        self.assertEqual(page._selected_java(), JAVA_AUTO,
                         "翻译标签必须映射回内部哨兵")
        page._on_java_changed()
        self.assertEqual(backend.get_instance_java(
            page.instance_box.currentText() or "default"), JAVA_AUTO,
            "存盘的必须是哨兵，切语言后不能失效")


if __name__ == "__main__":
    unittest.main(verbosity=2)
