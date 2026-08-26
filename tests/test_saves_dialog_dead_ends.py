# -*- coding: utf-8 -*-
"""存档管理对话框：空列表要指路，按钮不许装死。

以前：五类列表（存档/备份/截图/崩溃报告/日志）空了就是一片空白，
不说为什么空、下一步去哪；而且 6 个动作按钮里 5 个在没选中条目时
点了毫无反应（静默 return）——看起来全是坏按钮。

现在：空列表里有一条不可选中的占位说明（写明下一步），
按钮可用态跟随「当前类别 + 是否选中」实时变化。

场景在子进程里跑：PYMCL_HOME 指临时目录、offscreen、
列表数据全打桩（不动真实文件、不弹窗）。
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

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QWidget
app = QApplication([])

from app.backend import BackendAPI
from app.pages.saves_dialog import SavesDialog

def check(cond, msg):
    if not cond:
        print("FAIL:", msg, flush=True)
        raise SystemExit(1)

backend = BackendAPI()
saves, backups = [], []
backend.list_saves = lambda *a, **kw: saves[:]
backend.list_save_backups = lambda *a, **kw: backups[:]
backend.list_media = lambda *a, **kw: []

host = QWidget()
host.resize(900, 700)
dlg = SavesDialog(backend, "default", "", parent=host)

ACTIONS = ("open_btn", "del_btn", "dp_btn", "backup_btn", "restore_btn", "export_btn")

# --- 空的「存档」列表：占位说明 + 全部按钮灰掉 ---
check(dlg.list.count() == 1, "empty saves view must show a hint row")
hint = dlg.list.item(0)
check("还没有存档" in hint.text() and "世界" in hint.text(),
      f"hint must explain the next step, got {hint.text()!r}")
check(hint.flags() == Qt.NoItemFlags, "hint row must not be selectable")
for name in ACTIONS:
    check(not getattr(dlg, name).isEnabled(),
          f"{name} must be greyed out with nothing to act on")

# --- 有存档但没选中：按钮仍然灰；选中后立刻亮 ---
saves.append({"name": "MyWorld", "bytes": 1024})
dlg.reload()
check(dlg.list.count() == 1 and "MyWorld" in dlg.list.item(0).text(),
      "real save must replace the hint row")
for name in ACTIONS:
    check(not getattr(dlg, name).isEnabled(),
          f"{name} must stay greyed out until a row is selected")
dlg.list.setCurrentRow(0)
for name in ("open_btn", "del_btn", "dp_btn", "backup_btn", "export_btn"):
    check(getattr(dlg, name).isEnabled(),
          f"{name} must light up once a save is selected")
check(not dlg.restore_btn.isEnabled(),
      "restore only applies to backups, must stay greyed out")

# --- 备份视图：还原亮、备份/导出灰，删除按钮改名 ---
backups.append({"name": "MyWorld-2024", "bytes": 2048})
dlg.kind.setCurrentText("备份")
dlg.list.setCurrentRow(0)
check(dlg.restore_btn.isEnabled(), "restore must work on a selected backup")
check(dlg.del_btn.isEnabled() and dlg.del_btn.text() == "删除备份",
      f"delete must rename itself for backups, got {dlg.del_btn.text()!r}")
for name in ("dp_btn", "backup_btn", "export_btn"):
    check(not getattr(dlg, name).isEnabled(),
          f"{name} is a saves-only action, must be greyed out for backups")

# --- 空的「截图」视图：占位说明教人按 F2 ---
dlg.kind.setCurrentText("截图")
check(dlg.list.count() == 1 and "F2" in dlg.list.item(0).text(),
      f"screenshot hint must mention F2, got {dlg.list.item(0).text()!r}")
for name in ACTIONS:
    check(not getattr(dlg, name).isEnabled(),
          f"{name} must be greyed out on the empty screenshots view")

print("SCENARIO-OK", flush=True)
os._exit(0)
"""


class SavesDialogDeadEndsTest(unittest.TestCase):
    def test_empty_hints_and_honest_buttons(self):
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
