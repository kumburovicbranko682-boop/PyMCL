# -*- coding: utf-8 -*-
"""崩溃框的「导出错误报告 / 查看输出」失败时必须有可见反馈，不许静默。

对话框只构造不 exec；export_report / open_path 打桩，不碰真实磁盘弹窗。
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYMCL_HOME", tempfile.mkdtemp(prefix="pymcl_test_crashdlg_"))

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402
from qfluentwidgets import InfoBar  # noqa: E402

_app = QApplication.instance() or QApplication([])
_host = QWidget()
_host.resize(1000, 700)

from app.pages import crash_dialog as cd  # noqa: E402


class CrashDialogFeedbackTests(unittest.TestCase):
    def test_export_failure_is_visible(self):
        dlg = cd.CrashDialog({"detail": "boom", "title": "t"}, _host)
        orig = cd.export_report
        cd.export_report = lambda report: (_ for _ in ()).throw(OSError("磁盘满"))
        try:
            dlg._export()
            _app.processEvents()
            bars = dlg.findChildren(InfoBar)
            self.assertTrue(bars, "导出失败必须弹出可见错误提示")
        finally:
            cd.export_report = orig
            dlg.deleteLater()
            _app.processEvents()

    def test_export_success_opens_report(self):
        dlg = cd.CrashDialog({"detail": "boom", "title": "t"}, _host)
        opened = []
        orig_export, orig_open = cd.export_report, cd.open_path
        cd.export_report = lambda report: "/tmp/report.zip"
        cd.open_path = lambda p: opened.append(p)
        try:
            dlg._export()
            self.assertEqual(opened, ["/tmp/report.zip"])
        finally:
            cd.export_report, cd.open_path = orig_export, orig_open
            dlg.deleteLater()
            _app.processEvents()


if __name__ == "__main__":
    unittest.main(verbosity=2)
