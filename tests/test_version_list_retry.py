# -*- coding: utf-8 -*-
"""版本列表加载失败不能是死胡同：空状态要给原因和「重试」。

以前失败后网格里只有一句「版本列表加载失败」，唯一的线索在 4 秒后
消失的 InfoBar 里，也没有任何按钮——用户只能换页再回来碰运气。

场景在子进程里跑：PYMCL_HOME 指临时目录、offscreen、无窗口、网络打桩。
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest

_SCENARIO = r"""
import json, os, time
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

from app.main_window import MainWindow
from app.widgets import EmptyState
win = MainWindow()
win.resize(1180, 760)
win.show()
app.processEvents()

def check(cond, msg):
    if not cond:
        print("FAIL:", msg)
        raise SystemExit(1)

def _boom(*a, **k):
    raise RuntimeError("模拟断网")

win.backend.fetch_version_list = _boom

# 进版本页触发拉取（打桩后必然失败）
win.side.set_current("download", emit=True)
app.processEvents()
page = win.version_page

def wait_for(pred, timeout=8.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        app.processEvents()
        if pred():
            return True
        time.sleep(0.02)
    return False

def find_empty():
    for i in range(page.grid.count()):
        w = page.grid.itemAt(i).widget()
        if isinstance(w, EmptyState):
            return w
    return None

check(wait_for(lambda: find_empty() is not None and find_empty().action_btn is not None),
      "failure empty state with retry button should appear")
empty = find_empty()
check("版本列表加载失败" in empty._text_label.text(), "empty state should say what failed")
check("模拟断网" in empty._text_label.text(), "empty state should keep the reason visible")
check(empty.action_btn.text() == "重试", "action should be a retry")

# 网络恢复后点重试：真的重新拉，网格出现版本卡片
win.backend.fetch_version_list = lambda *a, **k: [
    {"version": "1.21.1", "type": "release", "date": "2024-08-08"},
]
empty.action_btn.click()
check(wait_for(lambda: any("1.21.1" in str(getattr(page.grid.itemAt(i).widget(), "info", ""))
                           for i in range(page.grid.count()))),
      "retry should reload the list and show version cards")

win.close()
app.processEvents()
del win
app.quit()
print("SCENARIO-OK")
"""


class VersionListRetryTest(unittest.TestCase):
    def test_failed_load_offers_retry(self):
        with tempfile.TemporaryDirectory(prefix="pymcl_test_") as home:
            env = dict(os.environ)
            env["PYMCL_HOME"] = home
            env["QT_QPA_PLATFORM"] = "offscreen"
            proc = subprocess.run(
                [sys.executable, "-c", _SCENARIO],
                cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                env=env, capture_output=True, text=True, timeout=180,
            )
            self.assertEqual(
                proc.returncode, 0,
                f"scenario failed\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}")
            self.assertIn("SCENARIO-OK", proc.stdout)


if __name__ == "__main__":
    unittest.main()
