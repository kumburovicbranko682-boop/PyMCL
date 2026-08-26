# -*- coding: utf-8 -*-
"""模组管理页空状态：说清去哪装，并给一个真按钮。

以前写「到『下载』页安装」——下载分区里装 Mod 的标签叫「Mod」，
而且没有任何可点的东西。

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
from app.widgets import EmptyState
win = MainWindow()
win.resize(1180, 760)
win.show()
app.processEvents()
win.backend.fetch_version_list = lambda *a, **k: []

def check(cond, msg):
    if not cond:
        print("FAIL:", msg)
        raise SystemExit(1)

win.switchTo("mods")
app.processEvents()
page = win.mods_page

empty = None
for i in range(page.list_layout.count()):
    w = page.list_layout.itemAt(i).widget()
    if isinstance(w, EmptyState):
        empty = w
        break
check(empty is not None, "fresh install should show the empty state")
text = empty._text_label.text()
check("下载 → Mod" in text, f"copy should name the real tab, got {text!r}")
check(empty.action_btn is not None and empty.action_btn.text() == "去下载 Mod",
      "empty state should offer a shortcut to the mod catalog")

empty.action_btn.click()
app.processEvents()
check(win._visible_key() == "mod",
      f"button should land on Download -> Mod, got {win._visible_key()!r}")

win.close()
app.processEvents()
del win
app.quit()
print("SCENARIO-OK")
"""


class ModsEmptyStateTest(unittest.TestCase):
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
