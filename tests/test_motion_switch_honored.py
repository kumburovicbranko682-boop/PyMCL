# -*- coding: utf-8 -*-
"""设置里的「界面动画」开关必须管住所有动效。

以前下载分类横条的指示器滑动、底部下载悬浮条的飞入/飞出绕开了
ui_motion 开关：用户明明关了动画，这两处还在播——开关名不副实。

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
    "ui_motion": False,
}), encoding="utf-8")

from mclauncher import feedback as _fb
_fb.start_heartbeat = lambda *a, **k: None
_fb.stop_heartbeat = lambda *a, **k: None

from PySide6.QtCore import QAbstractAnimation
from PySide6.QtWidgets import QApplication
app = QApplication([])

from app.main_window import MainWindow
win = MainWindow()
win.resize(1180, 760)
win.show()
app.processEvents()

def check(cond, msg):
    if not cond:
        print("FAIL:", msg)
        raise SystemExit(1)

# --- 分类横条指示器：动画关闭时必须瞬移到终态 ---
win.side.set_current("download", emit=True)
app.processEvents()
cat = win.download_section.cat
btn = cat._group.checkedButton()
check(btn is not None, "download bar should have a checked category")
cat._move_indicator(btn, animate=True)
check(cat._ind_anim.state() != QAbstractAnimation.Running,
      "indicator must not animate when ui_motion is off")
check(cat._indicator.geometry() == cat._indicator_rect(btn),
      "indicator must land exactly on the target when ui_motion is off")

# --- 下载悬浮条：动画关闭时直接就位，不建动画对象 ---
dock = win.download_dock
dock._active["fake-task"] = "测试任务"
dock.hide()
win._dock_anim = None
win._place_download_dock()
app.processEvents()
check(dock.isVisible(), "dock should still appear when ui_motion is off")
check(win._dock_anim is None,
      "dock must not fly in when ui_motion is off")

# --- 打开动画后：悬浮条恢复飞入 ---
from mclauncher.config import CONFIG
CONFIG.set("ui_motion", True)
dock.hide()
win._place_download_dock()
check(win._dock_anim is not None,
      "dock should animate again once ui_motion is back on")

# --- 设置页的两个动效开关必须即时生效，不能等「保存设置」 ---
sp = win.settings_page
app.processEvents()
sp.motion_sw.setChecked(False)
app.processEvents()
check(CONFIG.get("ui_motion") is False,
      "flipping the motion switch must apply immediately")
sp.fly_sw.setChecked(False)
app.processEvents()
check(CONFIG.get("ui_fly_animation") is False,
      "flipping the fly-animation switch must apply immediately")
sp.motion_sw.setChecked(True)
app.processEvents()
check(CONFIG.get("ui_motion") is True,
      "turning the motion switch back on must apply immediately")

win.close()
app.processEvents()
del win
app.quit()
print("SCENARIO-OK")
"""


class MotionSwitchTest(unittest.TestCase):
    def test_ui_motion_off_means_instant_everywhere(self):
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
