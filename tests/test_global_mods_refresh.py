# -*- coding: utf-8 -*-
"""全局 Mod 对话框：照着空状态的指示放了 jar，界面必须能跟上。

以前：空状态让人「点『打开文件夹』放入 jar」，但对话框没有任何
刷新手段——照做完回来列表还是空的，只能关掉重开。

现在：有「刷新」按钮，空状态文案也告诉人放完 jar 回来点它。

场景在子进程里跑：PYMCL_HOME 指临时目录、offscreen、
列表数据打桩（不动真实文件、不弹窗）。
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

from PySide6.QtWidgets import QApplication, QLabel, QWidget
from qfluentwidgets import BodyLabel, SwitchButton, TransparentPushButton
app = QApplication([])

from app.backend import BackendAPI
from app.pages.global_mods_dialog import GlobalModsDialog

def check(cond, msg):
    if not cond:
        print("FAIL:", msg, flush=True)
        raise SystemExit(1)

backend = BackendAPI()
rows = []
backend.list_global_mods = lambda: rows[:]

host = QWidget()
host.resize(900, 700)
dlg = GlobalModsDialog(backend, parent=host)

def host_widgets():
    out = []
    for i in range(dlg.host.count()):
        w = dlg.host.itemAt(i).widget()
        if w is not None:
            out.append(w)
    return out

def find_refresh():
    for b in dlg.findChildren(TransparentPushButton):
        if b.text() == "刷新":
            return b
    return None

# --- 空状态：必须有「刷新」按钮，文案要提到它 ---
refresh = find_refresh()
check(refresh is not None, "dialog must offer a refresh button")
labels = [w for w in host_widgets() if isinstance(w, BodyLabel)]
check(labels and "刷新" in labels[0].text(),
      f"empty copy must mention the refresh button, got "
      f"{labels[0].text() if labels else None!r}")

# --- 按指示放入 jar 后点「刷新」：列表要跟上，不用关掉重开 ---
rows.append({"filename": "sodium.jar", "enabled": True})
refresh.click()
names = [w.findChild(QLabel).text() for w in host_widgets()
         if w.findChild(SwitchButton) is not None]
check(names == ["sodium.jar"],
      f"refresh must pick up the new jar, got {names!r}")

print("SCENARIO-OK", flush=True)
os._exit(0)
"""


class GlobalModsRefreshTest(unittest.TestCase):
    def test_refresh_picks_up_new_jars(self):
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
