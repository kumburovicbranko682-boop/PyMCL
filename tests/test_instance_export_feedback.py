# -*- coding: utf-8 -*-
"""实例卡「导出为 .mrpack」不许静默：点了必须看得出开始了、知道去哪找结果。

导出跑在后台任务里，此前点击处零反馈（无气泡、无飞入），
按钮看起来像坏的，用户也不知道 exports/ 文件夹的存在。

钉住：
1. 点导出按钮 → 真调 backend.export_modpack(实例名)；
2. 飞入动画从按钮出发飞向任务入口（窗口有 fly_to_tasks 时）；
3. 页面出现指明 exports 文件夹的成功气泡；
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
os.environ.setdefault("PYMCL_HOME", tempfile.mkdtemp(prefix="pymcl_test_export_"))

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget  # noqa: E402

_app = QApplication.instance() or QApplication([])

from qfluentwidgets import InfoBar, TransparentToolButton  # noqa: E402

from app.pages.instance_page import InstancePage  # noqa: E402
from mclauncher.i18n import tr  # noqa: E402


class _StubBackend:
    def __init__(self):
        self.export_calls = []

    def get_instances(self):
        return [{"name": "default", "versions": 1, "mc": "1.21.1",
                 "java_label": ""}]

    def export_modpack(self, name, dest=""):
        self.export_calls.append(name)
        return "task-1"


class _Host(QWidget):
    """顶层宿主：records fly_to_tasks，模拟主窗的任务飞入入口。"""

    def __init__(self):
        super().__init__()
        self.fly_calls = []
        self.resize(900, 600)

    def fly_to_tasks(self, source, label, color=None):
        self.fly_calls.append((source, label))


class InstanceExportFeedbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.backend = _StubBackend()
        cls.host = _Host()
        lay = QVBoxLayout(cls.host)
        cls.page = InstancePage(cls.backend)
        lay.addWidget(cls.page)
        cls.host.show()
        _app.processEvents()

    @classmethod
    def tearDownClass(cls):
        cls.host.close()
        _app.processEvents()

    def _export_btn(self):
        for btn in self.page.findChildren(TransparentToolButton):
            if btn.toolTip() == tr("导出为 .mrpack"):
                return btn
        self.fail("实例卡上找不到导出按钮")

    def test_click_export_gives_feedback(self):
        btn = self._export_btn()
        btn.click()
        _app.processEvents()

        self.assertEqual(self.backend.export_calls, ["default"],
                         "导出按钮必须真调 backend.export_modpack")
        self.assertEqual(len(self.host.fly_calls), 1, "应有飞向任务入口的反馈")
        self.assertIs(self.host.fly_calls[0][0], btn, "飞入动画应从按钮出发")
        bars = self.page.findChildren(InfoBar)
        self.assertTrue(bars, "应出现成功气泡")
        contents = " ".join(b.content for b in bars)
        self.assertIn("exports", contents, "气泡必须告诉用户去哪找导出的文件")

    def test_en_catalog_has_sentence(self):
        data = json.loads(
            (Path(__file__).resolve().parent.parent
             / "mclauncher" / "locales" / "en.json").read_text("utf-8"))
        key = "完成后可在启动器目录的 exports 文件夹找到 {0}.mrpack"
        self.assertIn(key, data)
        self.assertIn("exports", data[key])


if __name__ == "__main__":
    unittest.main(verbosity=2)
