# -*- coding: utf-8 -*-
"""崩溃对话框的「查看输出 / 导出错误报告」失败时必须说话。

以前：open_path 打不开返回 False，但两个按钮都不看返回值，
OSError 也被 pass 吞掉——日志被清理后点「查看输出」毫无反应，
导出失败也毫无反应，人只会觉得按钮是坏的。

现在：打不开就报「文件不存在或已被清理」；导出失败报原因；
导出成功但拉不起文件管理器时至少告诉人文件在哪。

场景在子进程里跑：PYMCL_HOME 指临时目录、offscreen、
open_path / export_report / InfoBar 全打桩（不开外部程序、不弹窗）。
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

_SCENARIO = r"""
import json, os
from pathlib import Path

home = Path(os.environ["PYMCL_HOME"])
(home / "config.json").write_text(json.dumps({
    "first_run": False,
    "feedback_consent": False,
    "auto_check_update": False,
}), encoding="utf-8")

from PySide6.QtWidgets import QApplication
app = QApplication([])

import app.pages.crash_dialog as cd
from app.pages.crash_dialog import CrashDialog

def check(cond, msg):
    if not cond:
        print("FAIL:", msg, flush=True)
        raise SystemExit(1)

shown = []
class FakeInfoBar:
    @staticmethod
    def error(title, msg, **kw):
        shown.append(("error", str(title), str(msg)))
    @staticmethod
    def success(title, msg, **kw):
        shown.append(("success", str(title), str(msg)))
    @staticmethod
    def warning(title, msg, **kw):
        shown.append(("warning", str(title), str(msg)))
    @staticmethod
    def info(title, msg, **kw):
        shown.append(("info", str(title), str(msg)))
cd.InfoBar = FakeInfoBar

report = {
    "title": "Minecraft 出现错误",
    "detail": "Exit code 1",
    "direct_file": "/nonexistent/latest.log",
    "output_tail": "some tail",
}
dlg = CrashDialog(report)

# --- 日志文件已不存在：点「查看输出」必须说明，不能装死 ---
cd.open_path = lambda p: False
dlg._view()
check(shown and shown[-1][0] == "error" and "latest.log" in shown[-1][2],
      f"view must name the missing file, got {shown!r}")

# --- 导出失败：报原因 ---
def boom(_report):
    raise OSError("disk full")
cd.export_report = boom
dlg._export()
check(shown[-1][0] == "error" and "disk full" in shown[-1][2],
      f"export failure must surface the reason, got {shown[-1]!r}")

# --- 导出成功但打不开文件管理器：至少告诉人文件在哪 ---
cd.export_report = lambda _r: "/tmp/错误报告-x.zip"
cd.open_path = lambda p: False
dlg._export()
check(shown[-1][0] == "success" and "错误报告-x.zip" in shown[-1][2],
      f"export must reveal where the file went, got {shown[-1]!r}")

print("SCENARIO-OK", flush=True)
os._exit(0)
"""


class CrashDialogButtonsSpeakTest(unittest.TestCase):
    def test_view_and_export_report_failures(self):
        with tempfile.TemporaryDirectory(prefix="pymcl_test_") as home:
            env = dict(os.environ)
            env["PYMCL_HOME"] = home
            env["QT_QPA_PLATFORM"] = "offscreen"
            proc = subprocess.run(
                [sys.executable, "-u", "-c", _SCENARIO],
                cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=180,
            )
            self.assertEqual(
                proc.returncode, 0,
                f"scenario failed\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}")
            self.assertIn("SCENARIO-OK", proc.stdout)


if __name__ == "__main__":
    unittest.main()
