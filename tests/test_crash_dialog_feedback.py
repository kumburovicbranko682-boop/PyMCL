# -*- coding: utf-8 -*-
"""崩溃对话框：导出/查看失败不再无声吞掉。

钉住的行为：
- 「导出错误报告」失败（磁盘满 / 权限）时出错误提示，
  不再 except OSError: pass —— 之前点了毫无反应，像假按钮；
- 导出成功时真的调 export_report 并打开文件；
- show_launcher_error 的「完整日志：」走 tr()（源码不留 f-string）。

全程 offscreen，导出与打开文件打桩，不写真实磁盘、不弹系统窗口。
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYMCL_HOME", tempfile.mkdtemp(prefix="pymcl_test_crash_"))

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mclauncher import feedback as _fb  # noqa: E402

_fb.start_heartbeat = lambda *a, **k: None
_fb.stop_heartbeat = lambda *a, **k: None

_app = None


def setUpModule():
    global _app
    from PySide6.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication([])


_REPORT = {
    "title": "Minecraft 出现错误",
    "headline": "内存不足",
    "detail": "OutOfMemoryError",
    "instance": "default",
    "version": "1.20.1",
}


class CrashDialogFeedbackTests(unittest.TestCase):
    def _dialog(self):
        from PySide6.QtWidgets import QWidget
        from app.pages.crash_dialog import CrashDialog
        host = QWidget()
        host.resize(900, 700)
        self.addCleanup(host.deleteLater)
        dlg = CrashDialog(dict(_REPORT), host)
        _app.processEvents()
        return dlg

    def test_export_failure_is_visible(self):
        from unittest import mock
        from qfluentwidgets import InfoBar
        dlg = self._dialog()

        def boom(_report):
            raise OSError("disk full")

        with mock.patch("app.pages.crash_dialog.export_report", boom):
            dlg.export_btn.click()
        _app.processEvents()
        self.assertTrue(dlg.findChildren(InfoBar),
                        "导出失败必须有可见的错误提示，不能无声")

    def test_export_success_opens_file(self):
        from unittest import mock
        dlg = self._dialog()
        opened = []
        with mock.patch("app.pages.crash_dialog.export_report",
                        lambda r: "/tmp/report.zip"), \
                mock.patch("app.pages.crash_dialog.open_path",
                           lambda p: opened.append(p)):
            dlg.export_btn.click()
        _app.processEvents()
        self.assertEqual(opened, ["/tmp/report.zip"],
                         "导出成功应打开生成的报告")

    def test_no_silent_pass_and_log_copy_translated(self):
        src = (ROOT / "app" / "pages" / "crash_dialog.py").read_text(encoding="utf-8")
        self.assertNotIn("except OSError:\n            pass", src,
                         "失败不许再静默吞掉")
        self.assertNotIn('f"完整日志：', src, "「完整日志：」必须走 tr()")


if __name__ == "__main__":
    unittest.main(verbosity=2)
