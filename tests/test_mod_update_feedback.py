# -*- coding: utf-8 -*-
"""模组管理页「检查更新」不许静默：点了必须看得出开始了、知道去哪看结果。

检查跑在后台任务里，此前点击处零反馈，按钮看起来像坏的。

钉住：
1. 点「检查更新」→ 真调 backend.start_mod_updates(当前实例)；
2. 飞入动画从按钮出发（窗口有 fly_to_tasks 时）；
3. 出现指路「下载任务」页的气泡；
4. 新句子进了英文目录。

全程 offscreen + 临时数据目录，不弹任何窗口。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYMCL_HOME", tempfile.mkdtemp(prefix="pymcl_test_modupd_"))

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget  # noqa: E402

_app = QApplication.instance() or QApplication([])

from qfluentwidgets import InfoBar  # noqa: E402

from app.pages.mod_page import ModManagerPage  # noqa: E402
from mclauncher.i18n import tr  # noqa: E402


class _StubBackend:
    def __init__(self):
        self.update_calls = []

    def get_instances(self):
        return [{"name": "default", "versions": 1, "mc": "1.21.1",
                 "java_label": ""}]

    def get_mods_targets(self, instance):
        return [{"label": tr("实例共享 mods 目录"), "value": ""}]

    def get_installed_mod_entries(self, instance, version):
        return []

    def start_mod_updates(self, instance):
        self.update_calls.append(instance)
        return "task-1"


class _Host(QWidget):
    def __init__(self):
        super().__init__()
        self.fly_calls = []
        self.resize(1000, 700)

    def fly_to_tasks(self, source, label, color=None):
        self.fly_calls.append((source, label))


class ModUpdateFeedbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.backend = _StubBackend()
        cls.host = _Host()
        lay = QVBoxLayout(cls.host)
        cls.page = ModManagerPage(cls.backend)
        lay.addWidget(cls.page)
        cls.host.show()
        _app.processEvents()

    @classmethod
    def tearDownClass(cls):
        cls.host.close()
        _app.processEvents()

    def test_click_check_updates_gives_feedback(self):
        self.page.update_btn.click()
        _app.processEvents()

        self.assertEqual(self.backend.update_calls, ["default"],
                         "按钮必须真调 backend.start_mod_updates")
        self.assertEqual(len(self.host.fly_calls), 1, "应有飞向任务入口的反馈")
        self.assertIs(self.host.fly_calls[0][0], self.page.update_btn)
        bars = self.page.findChildren(InfoBar)
        self.assertTrue(bars, "应出现成功气泡")
        texts = " ".join(b.title + b.content for b in bars)
        self.assertIn(tr("下载任务"), texts, "气泡必须指路任务页")

    def test_en_catalog_has_sentences(self):
        data = json.loads(
            (Path(__file__).resolve().parent.parent
             / "mclauncher" / "locales" / "en.json").read_text("utf-8"))
        for key in ("已开始检查模组更新", "结果见「下载任务」页",
                    "将删除模组文件「{0}」，不可恢复。"):
            self.assertIn(key, data)
            self.assertTrue(str(data[key]).strip())


if __name__ == "__main__":
    unittest.main(verbosity=2)
