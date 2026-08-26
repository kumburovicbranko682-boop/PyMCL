# -*- coding: utf-8 -*-
"""实例页「导出为 .mrpack」：点了必须看得出任务已经开始。

钉住的行为：
- 点导出图标真的调用 BackendAPI.export_modpack（不是假按钮）；
- 有飞入下载任务的动画源（窗口提供 fly_to_tasks 时被调用）；
- 出一条 InfoBar 告诉人进度在「下载任务」、文件会落在 exports 文件夹
 （动效关闭时这是唯一的反馈，之前什么都没有）。

全程 offscreen，后端打桩，不真正导出、不联网。
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYMCL_HOME", tempfile.mkdtemp(prefix="pymcl_test_exp_"))

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mclauncher import feedback as _fb  # noqa: E402

_fb.start_heartbeat = lambda *a, **k: None
_fb.stop_heartbeat = lambda *a, **k: None

from mclauncher.i18n import tr  # noqa: E402

_app = None


def setUpModule():
    global _app
    from PySide6.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication([])


class InstanceExportFeedbackTests(unittest.TestCase):
    def _page(self):
        from unittest import mock
        from PySide6.QtWidgets import QWidget
        from app.backend import BackendAPI

        exports = []
        flies = []
        patches = [
            mock.patch.object(
                BackendAPI, "get_instances",
                lambda self: [{"name": "default", "versions": 1,
                               "mc": "1.20.1", "java_label": ""}]),
            mock.patch.object(
                BackendAPI, "export_modpack",
                lambda self, name, dest="": exports.append(name) or "task-1"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

        host = QWidget()
        host.resize(1000, 700)
        host.fly_to_tasks = lambda *a, **k: flies.append(a)
        self.addCleanup(host.deleteLater)

        from app.pages.instance_page import InstancePage
        page = InstancePage(BackendAPI(), host)
        _app.processEvents()
        return page, exports, flies

    def _export_button(self, page):
        from qfluentwidgets import TransparentToolButton
        for btn in page.findChildren(TransparentToolButton):
            if btn.toolTip() == tr("导出为 .mrpack"):
                return btn
        return None

    def test_export_button_starts_task_with_feedback(self):
        from qfluentwidgets import InfoBar
        page, exports, flies = self._page()
        btn = self._export_button(page)
        self.assertIsNotNone(btn, "实例卡上应有导出按钮")

        btn.click()
        _app.processEvents()
        self.assertEqual(exports, ["default"], "导出按钮必须接到 export_modpack")
        self.assertEqual(len(flies), 1, "任务开始应有飞入下载任务的动画源")
        self.assertTrue(page.findChildren(InfoBar),
                        "动效关闭时 InfoBar 是唯一反馈，必须出现")

    def test_direct_call_without_fly_still_notifies(self):
        from qfluentwidgets import InfoBar
        page, exports, _flies = self._page()
        page.export_pack("default")
        _app.processEvents()
        self.assertEqual(exports, ["default"])
        self.assertTrue(page.findChildren(InfoBar))


if __name__ == "__main__":
    unittest.main(verbosity=2)
