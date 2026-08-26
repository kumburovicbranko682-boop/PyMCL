# -*- coding: utf-8 -*-
"""启动页零版本空状态：横幅不再指向不存在的「启动」，按钮直接带去安装。

场景在子进程里跑：保证 PYMCL_HOME 指向临时目录（不碰真机数据）、
QT_QPA_PLATFORM=offscreen（不弹任何窗口）、Qt 状态不泄漏进测试进程。
"""
from __future__ import annotations

import json
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
    "first_run": False,          # 不弹首次向导（模态）
    "feedback_consent": False,   # 不弹诊断数据询问（模态）
    "auto_check_update": False,  # 不联网查更新
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
lp = win.launch_page
lp._boot_load()
app.processEvents()

# 版本页会异步拉版本清单：打桩，测试不出网
win.backend.fetch_version_list = lambda *a, **k: []

def check(cond, msg):
    if not cond:
        print("FAIL:", msg)
        raise SystemExit(1)

# --- 空状态：没有任何已安装版本 ---
check(lp.version_box.count() == 0, "expected fresh home with zero versions")
check(lp.launch_btn.text() == "安装游戏",
      f"empty state button should read 安装游戏, got {lp.launch_btn.text()!r}")
check(lp.banner.title.text() == "还没有安装游戏",
      f"banner should say nothing is installed, got {lp.banner.title.text()!r}")
check("启动游戏" not in lp.banner.subtitle.text(),
      "banner must not tell user to click a launch button that cannot work")

# --- 点按钮：直接到「下载 → 原版游戏」，不弹模态框 ---
lp.launch_btn.click()
app.processEvents()
check(QApplication.activeModalWidget() is None,
      "clicking install must not pop a modal dialog")
check(win._visible_key() == "version",
      f"click should land on Download -> Vanilla, got {win._visible_key()!r}")

# --- 装好一个版本后：按钮和横幅回到启动语义 ---
vdir = home / ".minecraft" / "default" / "versions" / "1.21.1"
vdir.mkdir(parents=True)
(vdir / "1.21.1.json").write_text(json.dumps({"id": "1.21.1"}), encoding="utf-8")
lp.reload()
app.processEvents()
check(lp.version_box.count() == 1, "installed version should appear in the box")
check(lp.launch_btn.text() == "启动游戏",
      f"button should flip back to 启动游戏, got {lp.launch_btn.text()!r}")
check(lp.banner.title.text() == "1.21.1",
      f"banner should show the version, got {lp.banner.title.text()!r}")

win.close()
app.processEvents()
del win
app.quit()
print("SCENARIO-OK")
"""


class LaunchEmptyStateTest(unittest.TestCase):
    def test_empty_state_guides_to_install(self):
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
