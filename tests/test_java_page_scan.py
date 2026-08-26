# -*- coding: utf-8 -*-
"""Java 页首次打开必须先扫本机再下结论，空状态要给「重新检测」按钮。

以前：初次进入只查启动器自己装的 Java（scan_system=False），
本机装着系统 Java 也会宣布「未检测到 Java，请从下方下载」——
没扫过就下结论，是假状态；而且空状态没有任何可点的下一步。

场景在子进程里跑：PYMCL_HOME 指临时目录、offscreen、
get_java_list 打桩（不真扫盘、不联网）。
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

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication
app = QApplication([])

from app.backend import BackendAPI
from app.pages.java_page import JavaPage, JavaCard
from app.widgets import EmptyState

def check(cond, msg):
    if not cond:
        print("FAIL:", msg)
        raise SystemExit(1)

def wait(ms=400):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()

def env_widgets(page):
    out = []
    for i in range(page.env_layout.count()):
        w = page.env_layout.itemAt(i).widget()
        if w is not None:
            out.append(w)
    return out

# --- 场景 1：本机有系统 Java，启动器没装过 -> 首次打开应自动扫出来 ---
backend = BackendAPI()
scans = []
def fake_java_list(scan_system=False):
    scans.append(scan_system)
    if scan_system:
        return [{"major": 17, "path": "/usr/bin/java", "name": "system"}]
    return []
backend.get_java_list = fake_java_list

page = JavaPage(backend)
wait()
cards = [w for w in env_widgets(page) if isinstance(w, JavaCard)]
check(True in scans, "first open must trigger a system scan when nothing is managed")
check(len(cards) == 1,
      f"system Java must appear without any click, got {len(cards)} cards")

# --- 场景 2：真的一台 Java 都没有 -> 空状态给「重新检测」按钮 ---
backend2 = BackendAPI()
scans2 = []
def fake_none(scan_system=False):
    scans2.append(scan_system)
    return []
backend2.get_java_list = fake_none

page2 = JavaPage(backend2)
wait()
empties = [w for w in env_widgets(page2) if isinstance(w, EmptyState)]
check(len(empties) == 1, "no Java anywhere must land on the empty state")
check("下载" in empties[0]._text_label.text(),
      f"empty copy must point at the download tiles, got {empties[0]._text_label.text()!r}")
check(empties[0].action_btn is not None
      and empties[0].action_btn.text() == "重新检测",
      "empty state must offer a recheck button")

before = len(scans2)
empties[0].action_btn.click()
wait()
check(len(scans2) > before and scans2[-1] is True,
      "recheck button must actually re-scan the system")

print("SCENARIO-OK")
os._exit(0)
"""


class JavaPageScanTest(unittest.TestCase):
    def test_auto_scan_and_recheck(self):
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
