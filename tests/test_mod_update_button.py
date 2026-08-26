# -*- coding: utf-8 -*-
"""模组页「检查并更新」：名字说实话，点了有反馈。

钉住的行为：
- 按钮文字是「检查并更新」——后端任务（_mod_update_impl）查到更新
  会直接安装，不只是"检查"，旧名「检查更新」名不副实；
- 点击真的调用 start_mod_updates，并出现飞入动画源 + InfoBar
  告知进度在「下载任务」、更新会直接装进 mods（之前完全静默）；
- 后端抛错时只出错误提示，不出「已开始」的假消息。

全程 offscreen，后端打桩，不联网。
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYMCL_HOME", tempfile.mkdtemp(prefix="pymcl_test_modupd_"))

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


class ModUpdateButtonTests(unittest.TestCase):
    def _page(self, start_raises=False):
        from unittest import mock
        from PySide6.QtWidgets import QWidget
        from app.backend import BackendAPI

        starts = []
        flies = []

        def fake_start(self, inst):
            if start_raises:
                raise RuntimeError("boom")
            starts.append(inst)
            return "task-1"

        patches = [
            mock.patch.object(
                BackendAPI, "get_instances",
                lambda self: [{"name": "default", "versions": 0,
                               "mc": "", "java_label": ""}]),
            mock.patch.object(
                BackendAPI, "get_mods_targets",
                lambda self, inst: [{"label": tr("实例共享 mods 目录"), "value": ""}]),
            mock.patch.object(
                BackendAPI, "get_installed_mod_entries",
                lambda self, inst, ver: []),
            mock.patch.object(BackendAPI, "start_mod_updates", fake_start),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

        host = QWidget()
        host.resize(1100, 760)
        host.fly_to_tasks = lambda *a, **k: flies.append(a)
        self.addCleanup(host.deleteLater)

        from app.pages.mod_page import ModManagerPage
        page = ModManagerPage(BackendAPI(), host)
        _app.processEvents()
        return page, starts, flies

    def test_button_label_is_honest(self):
        page, _starts, _flies = self._page()
        self.assertEqual(page.update_btn.text(), tr("检查并更新"),
                         "任务会直接安装更新，按钮不能只说「检查」")

    def test_click_starts_task_with_feedback(self):
        from qfluentwidgets import InfoBar
        page, starts, flies = self._page()
        page.update_btn.click()
        _app.processEvents()
        self.assertEqual(starts, ["default"], "按钮必须接到 start_mod_updates")
        self.assertEqual(len(flies), 1, "任务开始应有飞入下载任务的动画源")
        self.assertTrue(page.findChildren(InfoBar), "必须有可见反馈")

    def test_backend_error_shows_only_error(self):
        page, starts, flies = self._page(start_raises=True)
        page.update_btn.click()
        _app.processEvents()
        self.assertEqual(starts, [])
        self.assertEqual(flies, [], "失败时不能播「已开始」的飞入动画")


class CatalogUpdateButtonTests(unittest.TestCase):
    """「下载 → Mod」页顶部的同名按钮曾漏修：名字撒谎且点了零反馈。"""

    def _page(self, start_raises=False):
        from unittest import mock
        from PySide6.QtWidgets import QWidget
        from app.backend import BackendAPI

        starts = []
        flies = []

        def fake_start(self, inst):
            if start_raises:
                raise RuntimeError("boom")
            starts.append(inst)
            return "task-1"

        patches = [
            mock.patch.object(
                BackendAPI, "get_instances",
                lambda self: [{"name": "default", "versions": 0,
                               "mc": "", "java_label": ""}]),
            mock.patch.object(BackendAPI, "start_mod_updates", fake_start),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

        host = QWidget()
        host.resize(1100, 760)
        host.fly_to_tasks = lambda *a, **k: flies.append(a)
        self.addCleanup(host.deleteLater)

        from app.pages.catalog_page import ModPage
        page = ModPage(BackendAPI(), host)
        _app.processEvents()
        return page, starts, flies

    def test_button_label_is_honest(self):
        page, _starts, _flies = self._page()
        self.assertEqual(page.update_btn.text(), tr("检查并更新"),
                         "下载页的按钮同样会直接安装更新，不能只说「检查」")

    def test_click_starts_task_with_feedback(self):
        from qfluentwidgets import InfoBar
        page, starts, flies = self._page()
        page.update_btn.click()
        _app.processEvents()
        self.assertEqual(starts, ["default"], "按钮必须接到 start_mod_updates")
        self.assertEqual(len(flies), 1, "任务开始应有飞入下载任务的动画源")
        self.assertTrue(page.findChildren(InfoBar), "必须有可见反馈")

    def test_backend_error_shows_only_error(self):
        page, starts, flies = self._page(start_raises=True)
        page.update_btn.click()
        _app.processEvents()
        self.assertEqual(starts, [])
        self.assertEqual(flies, [], "失败时不能播「已开始」的飞入动画")


if __name__ == "__main__":
    unittest.main(verbosity=2)
