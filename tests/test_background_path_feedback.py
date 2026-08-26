# -*- coding: utf-8 -*-
"""设置页手输背景图路径：打错要说、生效要说、清空也要说。

以前 editingFinished 直接落盘：路径打错时背景不变、零提示，
像什么都没发生；而「选择文件」挑图却有成功气泡——同一件事两种反馈。

钉住：
1. 不存在的路径 → 警告气泡 + 不落盘；
2. 有效图片路径 → 落盘 + 成功气泡；
3. 清空 → 恢复纯色 + 成功气泡。

全程 offscreen + 临时数据目录，不弹任何窗口。
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYMCL_HOME", tempfile.mkdtemp(prefix="pymcl_test_bg_"))

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mclauncher import feedback as _fb  # noqa: E402

_fb.start_heartbeat = lambda *a, **k: None
_fb.stop_heartbeat = lambda *a, **k: None

from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from qfluentwidgets import InfoBar  # noqa: E402

from app.main_window import MainWindow  # noqa: E402
from mclauncher.i18n import tr  # noqa: E402


def _make_window():
    win = MainWindow.__new__(MainWindow)
    win._boot_extras = lambda: None
    MainWindow.__init__(win)
    return win


def _bar_texts(widget) -> str:
    return " ".join(b.title + b.content for b in widget.findChildren(InfoBar))


class BackgroundPathFeedbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.win = _make_window()
        cls.win.resize(1180, 760)
        cls.win.show()
        _app.processEvents()
        cls.win.switchTo("settings")
        _app.processEvents()
        cls.sp = cls.win._ensure_sub("settings")

    @classmethod
    def tearDownClass(cls):
        cls.win.close()
        _app.processEvents()

    def test_1_bad_path_warns_and_keeps_config(self):
        before = self.sp.backend.get_setting("ui_background") or ""
        self.sp.bg_edit.setText("/no/such/image.png")
        self.sp._on_bg_committed()
        _app.processEvents()
        self.assertIn(tr("找不到背景图"), _bar_texts(self.sp),
                      "路径打错必须提示，不能像没发生一样")
        self.assertEqual(self.sp.backend.get_setting("ui_background") or "",
                         before, "无效路径不许落盘")

    def test_2_valid_path_applies_with_toast(self):
        img = Path(tempfile.mkdtemp(prefix="pymcl_bg_")) / "bg.png"
        QImage(4, 4, QImage.Format_RGB32).save(str(img))
        self.sp.bg_edit.setText(str(img))
        self.sp._on_bg_committed()
        _app.processEvents()
        self.assertEqual(self.sp.backend.get_setting("ui_background"), str(img))
        self.assertIn(tr("背景已更新"), _bar_texts(self.sp))

    def test_3_clearing_restores_plain(self):
        self.sp.bg_edit.setText("")
        self.sp._on_bg_committed()
        _app.processEvents()
        self.assertEqual(self.sp.backend.get_setting("ui_background") or "", "")
        self.assertIn(tr("已恢复纯色背景"), _bar_texts(self.sp))


if __name__ == "__main__":
    unittest.main(verbosity=2)
