# -*- coding: utf-8 -*-
"""版本设置的「优先级」必须说人话，不能摆内部 token。

以前：整个对话框都是中文，唯独优先级下拉直接列出
low / normal / high 三个内部值——不懂英文的玩家只能瞎选。

现在：界面显示「低（给其他程序让路）/ 正常 / 高（游戏优先占用 CPU）」，
落盘仍是内部 token，读回旧配置也能对上。

场景在子进程里跑：PYMCL_HOME 指临时目录、offscreen、后端打桩。
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

from PySide6.QtWidgets import QApplication, QWidget
app = QApplication([])

from app.backend import BackendAPI
from app.pages.version_setup import VersionSetupDialog

def check(cond, msg):
    if not cond:
        print("FAIL:", msg, flush=True)
        raise SystemExit(1)

backend = BackendAPI()
backend.get_version_settings = lambda inst, ver: {"process_priority": "high"}
backend.java_combo_options = lambda inst, scan: [{"label": "自动选择", "value": "自动选择"}]
backend.call_async = lambda *a, **kw: None
backend.get_accounts = lambda: []

host = QWidget()
host.resize(900, 700)
dlg = VersionSetupDialog(backend, "default", "1.20.1", parent=host)

items = [dlg.priority.itemText(i) for i in range(dlg.priority.count())]
for token in ("low", "normal", "high"):
    check(token not in items,
          f"raw token {token!r} must not appear in the UI, got {items!r}")
check(any("低" in t for t in items) and any("高" in t for t in items),
      f"priority items must speak Chinese, got {items!r}")

# 读回旧配置：high -> 对应「高」档
check("高" in dlg.priority.currentText(),
      f"stored 'high' must select the high label, got {dlg.priority.currentText()!r}")

# 落盘仍是内部 token
low_label = next(t for t in items if "低" in t)
dlg.priority.setCurrentText(low_label)
check(dlg.payload()["process_priority"] == "low",
      f"payload must keep internal tokens, got {dlg.payload()['process_priority']!r}")

print("SCENARIO-OK", flush=True)
os._exit(0)
"""


class PrioritySpeaksHumanTest(unittest.TestCase):
    def test_priority_labels_round_trip(self):
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
