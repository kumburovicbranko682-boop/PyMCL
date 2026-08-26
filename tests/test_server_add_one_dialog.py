# -*- coding: utf-8 -*-
"""添加/编辑服务器必须一个对话框搞定，不再连弹三个模态框。

以前：添加一台服务器要连过「名称 → 地址 → 端口」三个 InputDialog，
每个都要单独点确定；编辑也要过两个。名称本来可选、端口默认 25565，
唯一必填的只有地址。

场景在子进程里跑：PYMCL_HOME 指临时目录、offscreen、
对话框的 exec 被打桩（自动填值并接受），不进入模态事件循环。
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

from PySide6.QtWidgets import QApplication
app = QApplication([])

from app.backend import BackendAPI
from app.pages.servers_page import ServerPage
from app.widgets import InputDialog

def check(cond, msg):
    if not cond:
        print("FAIL:", msg)
        raise SystemExit(1)

backend = BackendAPI()
page = ServerPage(backend)

# 旧的单字段用法必须原样可用
# （注意：MessageBoxBase 没有父级会直接崩——旧版 _on_add 恰恰就是
#  不传 parent 构造的，也就是说旧的「添加服务器」按钮点了必崩。）
single = InputDialog("标题", "标签", text="预填", parent=page)
check(single.value() == "预填", "single-field dialog must keep working")
check(single.values() == ["预填"], "values() must cover the single field too")
single.deleteLater()
page.reload()
check(page._instance, "server page must pick an instance by itself")

execs = []
fills = []

def fake_exec(self):
    execs.append(self)
    for edit, val in zip(self.edits, fills.pop(0)):
        if val is not None:
            edit.setText(val)
    return 1

InputDialog.exec = fake_exec

# --- 添加：一个对话框（地址必填，名称可选，端口默认 25565） ---
fills.append(["mc.example.com", "我的服务器", None])
page._on_add()
check(len(execs) == 1,
      f"adding a server must take exactly one dialog, took {len(execs)}")
rows = backend.list_servers(page._instance)
check(len(rows) == 1, f"server must be saved, got {rows}")
check(rows[0]["ip"] == "mc.example.com" and rows[0]["name"] == "我的服务器"
      and rows[0]["port"] == 25565,
      f"saved row must match the single dialog's fields, got {rows[0]}")

# --- 编辑：也只一个对话框 ---
execs.clear()
fills.append(["mc2.example.com", None])
page._on_edit(0)
check(len(execs) == 1,
      f"editing a server must take exactly one dialog, took {len(execs)}")
rows = backend.list_servers(page._instance)
check(rows[0]["ip"] == "mc2.example.com" and rows[0]["name"] == "我的服务器",
      f"edit must update the address and keep the name, got {rows[0]}")

# --- 地址留空：不落盘，不崩 ---
execs.clear()
fills.append(["", "无地址", None])
page._on_add()
rows = backend.list_servers(page._instance)
check(len(rows) == 1, "empty address must not create a server")

print("SCENARIO-OK")
os._exit(0)
"""


class ServerAddOneDialogTest(unittest.TestCase):
    def test_add_and_edit_take_one_dialog(self):
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
