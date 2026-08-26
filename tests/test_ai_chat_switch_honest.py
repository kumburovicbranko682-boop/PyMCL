# -*- coding: utf-8 -*-
"""AI 页回复中点别的对话：高亮不能先跳过去。

以前：回复进行中点侧栏其它对话，处理函数静默 return，
但 QListWidget 的高亮已经移到新对话上——列表选中 B、
内容区显示 A，选中态是假的，而且没有任何提示。

现在：忙碌时高亮弹回当前对话，并提示先停止或等回复结束；
空闲时切换照常工作。

场景在子进程里跑：PYMCL_HOME 指临时目录、offscreen、不联网不弹窗。
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
from PySide6.QtWidgets import QApplication
app = QApplication([])

from app.backend import BackendAPI
from app.pages.ai_page import AiPage
from mclauncher.ai import store as chat_store

def check(cond, msg):
    if not cond:
        print("FAIL:", msg, flush=True)
        raise SystemExit(1)

backend = BackendAPI()
page = AiPage(backend)

# 造出两个对话：第一个有内容，然后新建一个空的（成为 active）
page._history = [{"role": "user", "content": "hi"}]
page._persist()
chat_store.new_chat(page._store)
page._load_active()
page._reload_list()
check(page.chat_list.count() >= 2, "need at least two chats for the scenario")
active = page._store.get("active_id")

def row_of(cid):
    for i in range(page.chat_list.count()):
        if page.chat_list.item(i).data(Qt.UserRole) == cid:
            return i
    return -1

other_row = next(i for i in range(page.chat_list.count())
                 if page.chat_list.item(i).data(Qt.UserRole) != active)

# --- 回复进行中：点别的对话，高亮必须弹回当前对话，内容不切走 ---
page._worker = object()
page.chat_list.setCurrentRow(other_row)
app.processEvents()
check(page._store.get("active_id") == active,
      "busy page must not switch the active chat")
cur = page.chat_list.currentItem()
check(cur is not None and cur.data(Qt.UserRole) == active,
      "highlight must snap back to the chat that is actually shown")

# --- 空闲时：切换照常 ---
page._worker = None
page.chat_list.setCurrentRow(other_row)
app.processEvents()
check(page._store.get("active_id") != active,
      "idle page must switch chats normally")

print("SCENARIO-OK", flush=True)
os._exit(0)
"""


class AiChatSwitchHonestTest(unittest.TestCase):
    def test_highlight_follows_reality(self):
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
