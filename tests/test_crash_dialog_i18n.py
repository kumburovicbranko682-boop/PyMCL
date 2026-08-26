# -*- coding: utf-8 -*-
"""崩溃弹窗在英文界面必须能看懂：建议操作按钮、一键修复结果、帮助文案
不许冒中文。崩溃时刻正是用户最需要看懂提示的时刻。

全程 offscreen + 临时数据目录，不弹任何窗口（只构造，不 exec）。
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYMCL_HOME", tempfile.mkdtemp(prefix="pymcl_test_crash_"))

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from mclauncher import i18n  # noqa: E402
from app.backend import BackendAPI  # noqa: E402

_CJK = re.compile(r"[\u4e00-\u9fff]")

_REPORT = {
    "title": "Game crashed",
    "headline": "boom",
    "detail": "stack trace here",
    "instance": "default",
    "version": "1.21.1",
    "actions": [
        {"id": "disable_mods", "label": "禁用嫌疑 Mod", "mods": ["a.jar"]},
        {"id": "open_mods_folder", "label": "打开 Mods 文件夹排查"},
        {"id": "bump_memory", "label": "提高默认内存", "memory_mb": 6144},
        {"id": "need_java", "label": "下载 Java 21", "major": 21},
        {"id": "repair_version", "label": "修复该版本文件", "version": "1.21.1"},
        {"id": "open_gpu_hint", "label": "查看显卡驱动提示"},
        {"id": "reset_jvm_args", "label": "清空自定义 JVM 参数"},
        {"id": "open_crash_file", "label": "打开崩溃报告", "path": "/nonexistent"},
    ],
}


class CrashDialogEnglishTests(unittest.TestCase):
    def setUp(self):
        i18n.set_language("en")

    def tearDown(self):
        i18n.set_language("zh_CN")

    def _dialog(self):
        from app.pages.crash_dialog import CrashDialog
        return CrashDialog(dict(_REPORT), None, backend=BackendAPI(None))

    def test_action_buttons_english(self):
        from PySide6.QtWidgets import QPushButton
        dlg = self._dialog()
        try:
            fixed = {"重新启动", "确定", "查看输出", "导出错误报告", "发送给开发者"}
            texts = [b.text() for b in dlg.findChildren(QPushButton)
                     if b.text() and b.text() not in fixed]
            self.assertTrue(texts, "崩溃弹窗应有建议操作按钮")
            for t in texts:
                self.assertIsNone(_CJK.search(t),
                                  f"英文界面下崩溃按钮仍是中文: {t!r}")
            # 动态 label「下载 Java 21」要保留版本号
            self.assertTrue(any("21" in t for t in texts),
                            f"Java 按钮丢了版本号: {texts}")
        finally:
            dlg.deleteLater()

    def test_help_footer_english(self):
        from qfluentwidgets import CaptionLabel
        dlg = self._dialog()
        try:
            caps = [w.text() for w in dlg.findChildren(CaptionLabel) if w.text()]
            self.assertTrue(caps, "弹窗底部应有帮助文案")
            for t in caps:
                self.assertIsNone(_CJK.search(t),
                                  f"英文界面下帮助文案仍是中文: {t!r}")
        finally:
            dlg.deleteLater()

    def test_action_result_messages_english(self):
        backend = BackendAPI(None)
        backend.repair_version = lambda inst, ver: "t-repair"
        backend.download_java = lambda major, vendor="adoptium": "t-java"
        cases = [
            {"id": "disable_mods", "mods": []},
            {"id": "repair_version", "version": "1.21.1"},
            {"id": "repair_version"},                      # 无版本号的失败分支
            {"id": "need_java", "major": 21},
            {"id": "bump_memory", "memory_mb": 6144},
            {"id": "open_crash_file"},                     # 没有文件可开
            {"id": "open_crash_file", "path": "/nonexistent/x.txt"},
            {"id": "open_gpu_hint"},
            {"id": "reset_jvm_args"},
            {"id": "no_such_action"},
        ]
        for action in cases:
            result = backend.apply_crash_action(action, {})
            msg = (result or {}).get("message") or ""
            self.assertTrue(msg, f"{action['id']} 应返回消息")
            self.assertIsNone(_CJK.search(msg),
                              f"英文界面下 {action['id']} 的结果仍是中文: {msg!r}")


class CrashDialogChineseTests(unittest.TestCase):
    def test_labels_stay_chinese(self):
        i18n.set_language("zh_CN")
        from PySide6.QtWidgets import QPushButton
        from app.pages.crash_dialog import CrashDialog
        dlg = CrashDialog(dict(_REPORT), None, backend=BackendAPI(None))
        try:
            texts = [b.text() for b in dlg.findChildren(QPushButton)]
            self.assertIn("禁用嫌疑 Mod", texts)
            self.assertIn("下载 Java 21", texts)
        finally:
            dlg.deleteLater()


if __name__ == "__main__":
    unittest.main(verbosity=2)
