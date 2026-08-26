# -*- coding: utf-8 -*-
"""下载任务页空状态：名字要和下载横条一致，还要给一条路。

以前写「去下载板块里的版本 / 模组 … 发起」——横条里那两个页叫
「原版游戏」和「Mod」，而且整句话没有任何可点的东西。

场景在子进程里跑：PYMCL_HOME 指临时目录、offscreen、无窗口。
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

from app.main_window import MainWindow
win = MainWindow()
win.resize(1180, 760)
win.show()
app.processEvents()
win.backend.fetch_version_list = lambda *a, **k: []

def check(cond, msg):
    if not cond:
        print("FAIL:", msg)
        raise SystemExit(1)

win.side.set_current("tasks", emit=True)
app.processEvents()
empty = win.tasks_page.empty
check(empty.isVisible(), "empty state should show when there are no tasks")
text = empty._text_label.text()
check("原版游戏" in text, f"copy should use the real tab name, got {text!r}")
check("版本 /" not in text, "copy must not call the tab 版本 anymore")
check(empty.action_btn is not None and empty.action_btn.text() == "去下载",
      "empty state should offer a way to start a download")

empty.action_btn.click()
app.processEvents()
check(win._visible_key() == "version",
      f"button should land on Download -> Vanilla, got {win._visible_key()!r}")

win.close()
app.processEvents()
del win
app.quit()
print("SCENARIO-OK")
"""


class TasksEmptyStateTest(unittest.TestCase):
    def test_empty_state_names_and_navigates(self):
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
