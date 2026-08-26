# -*- coding: utf-8 -*-
"""角标脉冲动效：必须真的能播（不抛错）、动画结束摘掉 effect、
ui_motion 关闭时一帧都不播。

背景：pop() 曾把 QGraphicsScale（QGraphicsTransform）塞给
setGraphicsEffect，每次调用都 TypeError，动画从未播出过。
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYMCL_HOME", tempfile.mkdtemp(prefix="pymcl_test_pop_"))

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

_app = QApplication.instance() or QApplication([])

from mclauncher.config import CONFIG  # noqa: E402
from app.motion import fade, pop  # noqa: E402


def _spin_until(cond, timeout_s: float = 3.0) -> bool:
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        _app.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return cond()


class PopTests(unittest.TestCase):
    def setUp(self):
        CONFIG.set("ui_motion", True)
        self.lbl = QLabel("3")
        self.lbl.resize(20, 16)
        self.lbl.show()
        _app.processEvents()

    def tearDown(self):
        self.lbl.deleteLater()
        _app.processEvents()

    def test_pop_plays_and_cleans_up(self):
        pop(self.lbl, ms=20)
        self.assertIsNotNone(self.lbl.graphicsEffect(), "动效开着就该真的在播")
        self.assertTrue(
            _spin_until(lambda: not getattr(self.lbl, "_mcl_anims", [])),
            "动画应该能结束")
        self.assertIsNone(self.lbl.graphicsEffect(), "结束后必须摘掉 effect")

    def test_pop_skips_when_motion_off(self):
        CONFIG.set("ui_motion", False)
        try:
            pop(self.lbl, ms=20)
            self.assertIsNone(self.lbl.graphicsEffect())
            self.assertFalse(getattr(self.lbl, "_mcl_anims", []))
        finally:
            CONFIG.set("ui_motion", True)

    def test_fade_motion_off_jumps_to_done(self):
        CONFIG.set("ui_motion", False)
        try:
            hits = []
            fade(self.lbl, 0.0, 1.0, ms=100, on_done=lambda: hits.append(1))
            self.assertEqual(hits, [1], "动效关闭时应立即到终态")
            self.assertIsNone(self.lbl.graphicsEffect())
        finally:
            CONFIG.set("ui_motion", True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
