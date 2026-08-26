# -*- coding: utf-8 -*-
"""「隔离」档位必须当场解释清楚，而且三处界面用同一份解释。

以前首次向导直接甩出「隔离 Mod 与配置」四个档位不带任何说明——
第一次装 Minecraft 的人根本不知道选了会发生什么。

场景在子进程里跑：PYMCL_HOME 指临时目录、offscreen、无窗口、不 exec 对话框。
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

from mclauncher.version_settings import ISOLATION_HINTS, ISOLATION_LABELS
from app.main_window import MainWindow

win = MainWindow()
win.show()
app.processEvents()

def check(cond, msg):
    if not cond:
        print("FAIL:", msg)
        raise SystemExit(1)

# --- 首次向导：每一档都有人话解释，随选项联动 ---
from app.pages.first_run import FirstRunDialog
dlg = FirstRunDialog(win.backend, win)
check(dlg.iso_hint.text() == ISOLATION_HINTS["none"],
      f"wizard hint should explain the default, got {dlg.iso_hint.text()!r}")
dlg.iso.setCurrentText(ISOLATION_LABELS["all"])
app.processEvents()
check(dlg.iso_hint.text() == ISOLATION_HINTS["all"],
      "wizard hint should follow the selected level")
# 选择映射还得是对的（重构后不能存错档位）
dlg.game_dir.setText("")   # 不动游戏目录
dlg.apply()
from mclauncher.config import CONFIG
check(CONFIG.get("default_isolation") == "all",
      f"apply should store the picked level, got {CONFIG.get('default_isolation')!r}")

# --- 版本设置对话框：同一份解释 ---
vdir = home / ".minecraft" / "default" / "versions" / "1.21.1"
vdir.mkdir(parents=True)
(vdir / "1.21.1.json").write_text(json.dumps({"id": "1.21.1"}), encoding="utf-8")
from app.pages.version_setup import VersionSetupDialog
vs = VersionSetupDialog(win.backend, "default", "1.21.1", win)
app.processEvents()
check(vs.iso_hint.text() in ISOLATION_HINTS.values(),
      f"version setup hint should come from the shared table, got {vs.iso_hint.text()!r}")
vs.iso.setCurrentText(ISOLATION_LABELS["saves"])
app.processEvents()
check(vs.iso_hint.text() == ISOLATION_HINTS["saves"],
      "version setup hint should follow the selected level")

# --- 设置页：卡片描述联动同一份解释 ---
sp = win.settings_page
app.processEvents()
check(sp.iso_card.contentLabel.text() in ISOLATION_HINTS.values(),
      f"settings card should explain current level, got {sp.iso_card.contentLabel.text()!r}")
sp.iso_box.setCurrentText(ISOLATION_LABELS["mods"])
app.processEvents()
check(sp.iso_card.contentLabel.text() == ISOLATION_HINTS["mods"],
      "settings card description should follow the selected level")

win.close()
app.processEvents()
del win
app.quit()
print("SCENARIO-OK")
"""


class IsolationHintsTest(unittest.TestCase):
    def test_isolation_levels_are_explained_everywhere(self):
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
