# -*- coding: utf-8 -*-
"""设置里的「界面动画」总开关必须真的管住所有动效。

钉住的行为：
- ui_motion=False 时：下载悬浮球直接落位（不创建位移动画）、
  「飞入下载任务」的小球完全不出现；
- ui_motion=True 时：飞入动画照常创建（开关不是反的）。

全程 offscreen，不弹任何窗口，数据目录用临时目录。
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYMCL_HOME", tempfile.mkdtemp(prefix="pymcl_test_motion_"))

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mclauncher import feedback as _fb  # noqa: E402

_fb.start_heartbeat = lambda *a, **k: None
_fb.stop_heartbeat = lambda *a, **k: None

from mclauncher.config import CONFIG  # noqa: E402

_app = None


def setUpModule():
    global _app
    CONFIG.set("first_run", False)
    CONFIG.set("feedback_consent", False)
    CONFIG.set("auto_check_update", False)
    CONFIG.save()
    from PySide6.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication([])


def _make_window():
    from unittest import mock
    from app.backend import BackendAPI
    patches = [
        mock.patch.object(BackendAPI, "fetch_news", lambda self: []),
        mock.patch.object(BackendAPI, "cached_news", lambda self: []),
        mock.patch.object(BackendAPI, "fetch_version_list", lambda self: []),
        mock.patch.object(BackendAPI, "check_update", lambda self: {}),
    ]
    for p in patches:
        p.start()
    from app.main_window import MainWindow
    win = MainWindow()
    win._pymcl_test_patches = patches
    return win


def _close(win):
    for p in getattr(win, "_pymcl_test_patches", []):
        p.stop()
    win.close()
    _app.processEvents()


class MotionMasterSwitchTests(unittest.TestCase):
    def test_motion_off_dock_places_instantly(self):
        CONFIG.set("ui_motion", False)
        CONFIG.save()
        win = _make_window()
        try:
            win.show()
            _app.processEvents()
            win.download_dock._active = {"t1": "任务"}
            win._place_download_dock()
            _app.processEvents()
            self.assertIsNone(win._dock_anim,
                              "界面动画关闭时悬浮球不应创建位移动画")
            self.assertTrue(win.download_dock.isVisible())
        finally:
            _close(win)
            CONFIG.set("ui_motion", True)
            CONFIG.save()

    def test_motion_off_suppresses_fly(self):
        CONFIG.set("ui_motion", False)
        CONFIG.set("ui_fly_animation", True)
        CONFIG.save()
        win = _make_window()
        try:
            win.show()
            _app.processEvents()
            win.fly_to_tasks(win.launch_page.launch_btn, "测试")
            _app.processEvents()
            self.assertFalse(getattr(win, "_fly_jobs", []),
                             "界面动画关闭时不应创建飞入动画")
        finally:
            _close(win)
            CONFIG.set("ui_motion", True)
            CONFIG.save()

    def test_motion_on_fly_still_works(self):
        CONFIG.set("ui_motion", True)
        CONFIG.set("ui_fly_animation", True)
        CONFIG.save()
        win = _make_window()
        try:
            win.show()
            _app.processEvents()
            win.fly_to_tasks(win.launch_page.launch_btn, "测试")
            self.assertTrue(getattr(win, "_fly_jobs", []),
                            "界面动画开启时飞入动画应正常创建")
        finally:
            _close(win)


class CatBarIndicatorTests(unittest.TestCase):
    """下载分类横条的绿色下划线动画也要听总开关的。"""

    def _bar(self):
        from PySide6.QtWidgets import QWidget
        from app.pages.download_hub import DownloadCatBar
        host = QWidget()
        host.resize(800, 60)
        bar = DownloadCatBar(host)
        bar.resize(800, 48)
        pages = [QWidget(host), QWidget(host)]
        bar.add_item("原版游戏", pages[0])
        bar.add_item("Mod", pages[1])
        host.show()
        _app.processEvents()
        self.addCleanup(host.deleteLater)
        return bar, pages

    def test_indicator_jumps_when_motion_off(self):
        from PySide6.QtCore import QAbstractAnimation
        CONFIG.set("ui_motion", False)
        CONFIG.save()
        try:
            bar, pages = self._bar()
            bar.select_page(pages[0])
            _app.processEvents()
            bar.select_page(pages[1])
            _app.processEvents()
            self.assertNotEqual(bar._ind_anim.state(), QAbstractAnimation.Running,
                                "界面动画关闭时下划线不应播动画")
            btn, _ = bar._buttons[id(pages[1])]
            self.assertEqual(bar._indicator.geometry(), bar._indicator_rect(btn),
                             "关动画时下划线应直接落到目标位置")
        finally:
            CONFIG.set("ui_motion", True)
            CONFIG.save()

    def test_indicator_animates_when_motion_on(self):
        from PySide6.QtCore import QAbstractAnimation
        CONFIG.set("ui_motion", True)
        CONFIG.save()
        bar, pages = self._bar()
        bar.select_page(pages[0])
        _app.processEvents()
        bar.select_page(pages[1])
        self.assertEqual(bar._ind_anim.state(), QAbstractAnimation.Running,
                         "界面动画开启时下划线应有平滑过渡")


if __name__ == "__main__":
    unittest.main(verbosity=2)
