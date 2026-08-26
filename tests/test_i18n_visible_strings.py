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
            ("发布于 {0}", "2024-06-13"): "2024-06-13",
            ("安装 {0}", "1.21.1"): "1.21.1",
            ("版本设置 · {0}", "1.21.1"): "1.21.1",
            ("存档 · {0}", "default"): "default",
            ("「{0}」正在打包，可到下载任务页看进度。", "w1"): "w1",
            ("将删除整个实例「{0}」及其文件，不可恢复。", "inst"): "inst",
            ("将永久删除世界「{0}」，其中的建筑与游戏进度都无法恢复。", "w1"): "w1",
            ("将删除「{0}」。", "x.zip"): "x.zip",
            ("从「{0}」还原存档？", "b1"): "b1",
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

    def test_task_titles_english(self):
        """后端任务标题（任务页 / 底部下载条每次装东西都看得到）必须走翻译。"""
        cases = {
            ("安装整合包 {0}", "AllTheMods.mrpack"): "AllTheMods.mrpack",
            ("安装模组 {0}", "sodium.jar"): "sodium.jar",
            ("安装光影 {0}", "BSL.zip"): "BSL.zip",
            ("安装资源包 {0}", "Faithful.zip"): "Faithful.zip",
            ("安装数据包 {0}", "dp.zip"): "dp.zip",
            ("安装世界 {0}", "world.zip"): "world.zip",
            ("导出启动脚本 {0}", "1.21.1"): "1.21.1",
            ("备份存档 {0}", "w1"): "w1",
            ("下载 Java {0}", 17): "17",
            ("下载 Java {0}（{1}）", (17, "adoptium")): "adoptium",
            ("启动游戏 {0}", "1.21.1"): "1.21.1",
            ("修复 {0}", "1.21.1"): "1.21.1",
            ("导出整合包 {0}", "default"): "default",
            ("检查模组更新 {0}", "default"): "default",
        }
        for (key, arg), must_contain in cases.items():
            args = arg if isinstance(arg, tuple) else (arg,)
            text = i18n.tr(key).format(*args)
            self.assertIsNone(_CJK.search(text),
                              f"英文界面下任务标题 {key!r} 冒中文: {text!r}")
            self.assertIn(str(must_contain), text)

    def test_task_title_prefix_contracts(self):
        """任务图标表和下载计数都按 tr(前缀) 匹配标题开头，两种语言都不能裂。"""
        pairs = [
            ("安装整合包 {0}", "安装整合包"),
            ("安装模组 {0}", "安装模组"),
            ("安装光影 {0}", "安装光影"),
            ("安装资源包 {0}", "安装资源包"),
            ("下载 Java {0}", "下载 Java"),
            ("下载 Java {0}（{1}）", "下载 Java"),
            ("启动游戏 {0}", "启动游戏"),
        ]
        for lang in ("en", "zh_CN"):
            i18n.set_language(lang)
            for template, prefix in pairs:
                self.assertTrue(
                    i18n.tr(template).startswith(i18n.tr(prefix)),
                    f"[{lang}] {template!r} 的译文不再以 {prefix!r} 的译文开头，"
                    "任务图标/下载计数会匹配不上")
        i18n.set_language("en")

        from app.backend import BackendAPI
        launch = i18n.tr("启动游戏 {0}").format("1.21.1")
        self.assertFalse(BackendAPI.is_download_title(launch),
                         "启动任务不该算进下载计数")
        install = i18n.tr("安装模组 {0}").format("sodium.jar")
        self.assertTrue(BackendAPI.is_download_title(install))

    def test_download_counts_follow_language(self):
        """下载量单位跟随语言：英文用 K/M/B，中文保留 万/亿。"""
        from app.widgets import fmt_downloads
        from app.pages import catalog_page, file_pick

        # 两个页面必须用同一份实现，不许再各写一份中文单位
        self.assertIs(catalog_page.fmt_downloads, fmt_downloads)
        self.assertIs(file_pick.fmt_downloads, fmt_downloads)

        i18n.set_language("en")
        for n, expect in [(123_456_789, "M"), (2_000_000_000, "B"),
                          (45_678, "K"), (999, "999")]:
            text = fmt_downloads(n)
            self.assertIsNone(_CJK.search(text),
                              f"英文界面下下载量冒中文: {n} -> {text!r}")
            self.assertIn(expect, text)

        i18n.set_language("zh_CN")
        self.assertIn("亿", fmt_downloads(123_456_789))
        self.assertIn("万", fmt_downloads(45_678))
        self.assertEqual(fmt_downloads(0), "—")
        i18n.set_language("en")

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
