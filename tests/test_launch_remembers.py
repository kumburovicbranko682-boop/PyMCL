# -*- coding: utf-8 -*-
"""重开启动器要记住上次的选择：版本、离线用户名。

以前实例记得住（default_instance），但版本每次落回字母序第一个、
用户名永远变回 Player——上次的选择明明还在，却要人重选重打。

场景在子进程里跑：PYMCL_HOME 指临时目录、offscreen、无窗口。
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest

_SCENARIO = r"""
import json, os, sys
from pathlib import Path

home = Path(os.environ["PYMCL_HOME"])
(home / "config.json").write_text(json.dumps({
    "first_run": False,
    "feedback_consent": False,
    "auto_check_update": False,
    "default_instance": "default",
    "last_version": "b-fabric",       # 上次启动的是它，不是字母序第一
    "offline_username": "Steve",
}), encoding="utf-8")

# 预置两个假版本：字母序上 a-vanilla 在前，能暴露「落回第一个」的旧行为
for vid in ("a-vanilla", "b-fabric"):
    vdir = home / ".minecraft" / "default" / "versions" / vid
    vdir.mkdir(parents=True)
    (vdir / f"{vid}.json").write_text(json.dumps({"id": vid}), encoding="utf-8")

from mclauncher import feedback as _fb
_fb.start_heartbeat = lambda *a, **k: None
_fb.stop_heartbeat = lambda *a, **k: None

from PySide6.QtWidgets import QApplication
app = QApplication([])

from app.main_window import MainWindow
win = MainWindow()
win.show()
app.processEvents()
lp = win.launch_page
lp._boot_load()
app.processEvents()

def check(cond, msg):
    if not cond:
        print("FAIL:", msg)
        raise SystemExit(1)

check(lp.version_box.currentText() == "b-fabric",
      f"version box should restore last launched, got {lp.version_box.currentText()!r}")
check(lp.username_edit.text() == "Steve",
      f"username should restore last used, got {lp.username_edit.text()!r}")

# 写入端：启动时的记忆入口真的落盘
win.backend.remember_launch_choices("default", "a-vanilla", "Alex")
data = json.loads((home / "config.json").read_text(encoding="utf-8"))
check(data.get("last_version") == "a-vanilla", "last_version should persist")
check(data.get("offline_username") == "Alex", "offline_username should persist")
check(data.get("default_instance") == "default", "default_instance should persist")

# 空用户名 / 空版本不能把记忆冲掉
win.backend.remember_launch_choices("default", "", "   ")
data = json.loads((home / "config.json").read_text(encoding="utf-8"))
check(data.get("last_version") == "a-vanilla", "empty version must not wipe memory")
check(data.get("offline_username") == "Alex", "blank username must not wipe memory")

win.close()
app.processEvents()
del win
app.quit()
print("SCENARIO-OK")
"""


class LaunchRemembersTest(unittest.TestCase):
    def test_restores_last_version_and_username(self):
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
