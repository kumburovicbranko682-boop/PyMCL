# -*- coding: utf-8 -*-
"""英文界面不许冒中文：下载条标题、文件选择器状态行、启动横幅副标题、
「加载更多」按钮这些动态拼接的文案必须走翻译目录。

全程 offscreen + 临时数据目录，不弹任何窗口。
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYMCL_HOME", tempfile.mkdtemp(prefix="pymcl_test_i18n_"))

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from mclauncher import i18n  # noqa: E402

_CJK = re.compile(r"[\u4e00-\u9fff]")


class VisibleStringsEnglishTests(unittest.TestCase):
    def setUp(self):
        i18n.set_language("en")

    def tearDown(self):
        i18n.set_language("zh_CN")

    def test_templates_have_english(self):
        cases = {
            ("下载任务（{0}）", 3): "3",
            ("{0} 个文件", 7): "7",
            ("{0} 个匹配 / 共 {1} 个文件", (2, 9)): "2",
            ("加载更多（还有 {0}）", 40): "40",
            ("实例 {0} · 点击「启动游戏」进入世界", "default"): "default",
        }
        for (key, arg), must_contain in cases.items():
            args = arg if isinstance(arg, tuple) else (arg,)
            text = i18n.tr(key).format(*args)
            self.assertIsNone(_CJK.search(text),
                              f"英文界面下 {key!r} 渲染出了中文: {text!r}")
            self.assertIn(str(must_contain), text)

    def test_instance_card_english(self):
        """实例卡的版本计数、Java 行、删除确认句、选 Java 说明句必须走翻译。"""
        for key, args in [
            ("{0} 个版本", (2,)),
            ("确定删除实例「{0}」？其中的存档与配置将一并移除。", ("default",)),
            ("实例「{0}」启动时使用的 Java。自动选择会按游戏版本匹配（1.19+ 用 17，远古版用 8）。",
             ("default",)),
        ]:
            text = i18n.tr(key).format(*args)
            self.assertIsNone(_CJK.search(text),
                              f"英文界面下 {key!r} 渲染出了中文: {text!r}")

        from PySide6.QtWidgets import QLabel
        from app.pages.instance_page import InstanceCard

        class _P:
            pass

        card = InstanceCard({"name": "default", "versions": 2, "mc": "1.21.1",
                             "java_label": ""}, _P())
        texts = " ".join(lab.text() for lab in card.findChildren(QLabel))
        self.assertIsNone(_CJK.search(texts),
                          f"英文界面下实例卡冒中文: {texts!r}")
        self.assertIn("versions", texts)
        self.assertIn("Auto", texts)
        card.deleteLater()
        _app.processEvents()

    def test_download_dock_title_english(self):
        from app.backend import BackendAPI
        from app.pages.tasks_page import DownloadDock

        dock = DownloadDock(BackendAPI(None), None)
        dock._add("t1", i18n.tr("安装游戏") + " 1.21.1")
        text = dock.title.text()
        self.assertIsNone(_CJK.search(text),
                          f"英文界面下下载条标题冒中文: {text!r}")
        dock.deleteLater()
        _app.processEvents()


if __name__ == "__main__":
    unittest.main(verbosity=2)
