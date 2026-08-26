# -*- coding: utf-8 -*-
"""「界面动画」总开关必须说到做到：关闭后飞入动画不播、悬浮球瞬移、
下载/更多分类条的指示条直接落位。

设置卡文案写着「关闭则全部瞬时」，此前 fly_to_tasks 只看
ui_fly_animation 子开关、下载悬浮球的位移动画和分类条指示条
根本不看开关。
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYMCL_HOME", tempfile.mkdtemp(prefix="pymcl_test_master_"))

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mclauncher import feedback as _fb  # noqa: E402

_fb.start_heartbeat = lambda *a, **k: None
_fb.stop_heartbeat = lambda *a, **k: None

from PySide6.QtCore import QPoint  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from mclauncher.config import CONFIG  # noqa: E402
from app.fly_anim import FlyBall  # noqa: E402
from app.main_window import MainWindow  # noqa: E402


def _make_window():
    win = MainWindow.__new__(MainWindow)
    win._boot_extras = lambda: None
    MainWindow.__init__(win)
    return win


class MasterSwitchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.win = _make_window()
        cls.win.resize(1180, 760)
        cls.win.show()
        _app.processEvents()

    @classmethod
    def tearDownClass(cls):
        cls.win.close()
        _app.processEvents()

    def setUp(self):
        CONFIG.set("ui_motion", True)
        CONFIG.set("ui_fly_animation", True)

    def tearDown(self):
        CONFIG.set("ui_motion", True)

    def test_fly_plays_when_motion_on(self):
        self.win.fly_to_tasks(self.win.side, "测试")
        _app.processEvents()
        self.assertTrue(self.win.findChildren(FlyBall),
                        "动效全开时飞球应该出现")
        for b in self.win.findChildren(FlyBall):
            b.deleteLater()
        _app.processEvents()

    def test_fly_respects_master_switch(self):
        CONFIG.set("ui_motion", False)
        before = len(self.win.findChildren(FlyBall))
        self.win.fly_to_tasks(self.win.side, "测试")
        _app.processEvents()
        self.assertEqual(len(self.win.findChildren(FlyBall)), before,
                         "总开关关闭时不许再飞")

    def test_anim_pos_jumps_when_motion_off(self):
        CONFIG.set("ui_motion", False)
        from PySide6.QtWidgets import QLabel

        lbl = QLabel("x", self.win)
        lbl.move(0, 0)
        hits = []
        result = self.win._anim_pos(lbl, QPoint(50, 60), 300,
                                    done=lambda: hits.append(1))
        self.assertIsNone(result, "动效关闭时不应创建动画对象")
        self.assertEqual((lbl.x(), lbl.y()), (50, 60), "应直接到终态")
        self.assertEqual(hits, [1], "完成回调应立即触发")
        lbl.deleteLater()

    def _make_cat_bar(self):
        from PySide6.QtWidgets import QWidget
        from app.pages.download_hub import DownloadCatBar

        bar = DownloadCatBar()
        bar.resize(600, 48)
        bar.show()
        _app.processEvents()
        pages = [QWidget(), QWidget()]
        bar.add_item("原版游戏", pages[0])
        bar.add_item("Mod", pages[1])
        _app.processEvents()
        bar.select_page(pages[0], animate=False)
        _app.processEvents()
        return bar, pages

    def test_cat_indicator_jumps_when_motion_off(self):
        from PySide6.QtCore import QAbstractAnimation

        CONFIG.set("ui_motion", False)
        bar, pages = self._make_cat_bar()
        bar.select_page(pages[1])
        self.assertNotEqual(bar._ind_anim.state(), QAbstractAnimation.Running,
                            "总开关关闭时指示条不许播动画")
        target = bar._indicator_rect(bar._buttons[id(pages[1])][0])
        self.assertEqual(bar._indicator.geometry(), target, "指示条应直接落位")
        bar.deleteLater()

    def test_cat_indicator_animates_when_motion_on(self):
        from PySide6.QtCore import QAbstractAnimation

        bar, pages = self._make_cat_bar()
        bar.select_page(pages[1])
        self.assertEqual(bar._ind_anim.state(), QAbstractAnimation.Running,
                         "动效全开时指示条应有补间")
        bar._ind_anim.stop()
        bar.deleteLater()


if __name__ == "__main__":
    unittest.main(verbosity=2)
