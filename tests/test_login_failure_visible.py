# -*- coding: utf-8 -*-
"""微软登录失败必须显示在设备码对话框里，而不是弹在遮罩后面。

以前：任务失败时对话框继续开着，卡在「正在获取登录代码…」，
错误 InfoBar 弹在被模态遮罩挡住的账号页顶部——人只能干等。

场景在子进程里跑：PYMCL_HOME 指临时目录、offscreen、无窗口弹出
（对话框只构造不 exec，不会进入模态事件循环）。
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest

_SCENARIO = r"""
import json, os
from pathlib import Path

home = Path(os.environ["PYMCL_HOME"])
(home / "config.json").write_text(json.dumps({
    "first_run": False,
    "feedback_consent": False,
    "auto_check_update": False,
}), encoding="utf-8")

from mclauncher import feedback as _fb
_fb.start_heartbeat = lambda *a, **k: None
_fb.stop_heartbeat = lambda *a, **k: None

from PySide6.QtWidgets import QApplication
app = QApplication([])

from app.backend import BackendAPI
from app.pages.account_page import AccountPage
from app.widgets import DeviceCodeDialog

def check(cond, msg):
    if not cond:
        print("FAIL:", msg)
        raise SystemExit(1)

backend = BackendAPI()
page = AccountPage(backend)

# --- 失败路径：对话框开着，任务失败 ---
page._login_dlg = DeviceCodeDialog(page)
page._login_task = "task-1"
backend.finished.emit("task-1", False, "无法连接 login.microsoftonline.com")
app.processEvents()

dlg = page._login_dlg
check(dlg is not None and dlg.result() == 0,
      "failure must not silently close the dialog")
hint = dlg.hint.text()
check("登录失败" in hint and "login.microsoftonline.com" in hint,
      f"failure reason must appear inside the dialog, got {hint!r}")
check(not dlg.yesButton.isEnabled(),
      "open-browser button must be disabled after failure (no URI to open)")
check(dlg.code.text() == "" and dlg.uri.text() == "",
      "stale code/uri must be cleared on failure")
dlg.deleteLater()
page._login_dlg = None

# --- 成功路径不受影响：对话框应被 accept ---
page._login_dlg = DeviceCodeDialog(page)
page._login_task = "task-2"
accepted = []
page._login_dlg.accepted.connect(lambda: accepted.append(True))
backend.finished.emit("task-2", True, "已登录 Steve")
# MaskDialogBase.done() 先播 100ms 淡出动画再落结果，等它跑完
from PySide6.QtCore import QEventLoop, QTimer
loop = QEventLoop()
QTimer.singleShot(500, loop.quit)
loop.exec()
check(bool(accepted), "success must still auto-close the dialog")
page._login_dlg = None

# --- 账号页：关掉设备码框必须取消后台登录任务（与启动页同款） ---
backend.start_microsoft_login = lambda: "task-3"
cancelled = []
backend.cancel_task = lambda tid: cancelled.append(tid)
QTimer.singleShot(150, lambda: page._login_dlg and page._login_dlg.reject())
page._ms()
check(cancelled == ["task-3"],
      f"dismissing the dialog must cancel the polling task, got {cancelled}")

# --- 启动页的同一个对话框：失败呈现必须与账号页一致 ---
from app.pages.launch_page import LaunchPage
lp = LaunchPage(backend)
lp._login_dlg = DeviceCodeDialog(lp)
lp._login_task_id = "task-4"
backend.finished.emit("task-4", False, "设备码已过期")
app.processEvents()
hint = lp._login_dlg.hint.text()
check("登录失败" in hint and "设备码已过期" in hint,
      f"launch page dialog must mark failures too, got {hint!r}")
check(not lp._login_dlg.yesButton.isEnabled(),
      "launch page dialog must disable the dead browser button on failure")

print("SCENARIO-OK")
os._exit(0)
"""


class LoginFailureVisibleTest(unittest.TestCase):
    def test_failure_shows_in_dialog(self):
        with tempfile.TemporaryDirectory(prefix="pymcl_test_") as home:
            env = dict(os.environ)
            env["PYMCL_HOME"] = home
            env["QT_QPA_PLATFORM"] = "offscreen"
            proc = subprocess.run(
                [sys.executable, "-u", "-c", _SCENARIO],
                cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                env=env, capture_output=True, text=True, timeout=180,
            )
            self.assertEqual(
                proc.returncode, 0,
                f"scenario failed\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}")
            self.assertIn("SCENARIO-OK", proc.stdout)


if __name__ == "__main__":
    unittest.main()
