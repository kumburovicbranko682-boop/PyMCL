# -*- coding: utf-8 -*-
"""AI 页「删除对话」：有聊天记录必须先确认，空对话直接删。

以前：一键就把整段聊天记录抹掉且不可恢复——应用里删模组、删存档、
卸载版本全都先问一声，唯独这里不问。

现在：对话里有消息时弹确认框（写明对话名、不可恢复），
新建的空对话删了不损失什么，不多问一步。

场景在子进程里跑：PYMCL_HOME 指临时目录、offscreen、
确认框打桩（不弹窗、不联网）。
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

from PySide6.QtWidgets import QApplication
app = QApplication([])

from app.backend import BackendAPI
import app.pages.ai_page as ai_mod
from app.pages.ai_page import AiPage

def check(cond, msg):
    if not cond:
        print("FAIL:", msg, flush=True)
        raise SystemExit(1)

asked = []
class FakeBox:
    answer = False
    def __init__(self, title, content, parent=None):
        asked.append((title, content))
        self.yesButton = type("B", (), {"setText": staticmethod(lambda *_: None)})()
        self.cancelButton = type("B", (), {"setText": staticmethod(lambda *_: None)})()
    def exec(self):
        return FakeBox.answer
ai_mod.MessageBox = FakeBox

backend = BackendAPI()
page = AiPage(backend)

# --- 有聊天记录：先问；点取消什么都不删 ---
page._history = [{"role": "user", "content": "hi"},
                 {"role": "assistant", "content": "hello"}]
page._persist()
n_before = len(page._store.get("chats") or [])
cid_before = page._store.get("active_id")

FakeBox.answer = False
page._delete_chat()
check(asked, "deleting a chat with messages must ask first")
check("不可恢复" in asked[-1][1],
      f"confirm must say it cannot be undone, got {asked[-1][1]!r}")
check(page._store.get("active_id") == cid_before
      and len(page._store.get("chats") or []) == n_before,
      "cancelling the confirm must keep the chat")

# --- 确认后才真的删 ---
FakeBox.answer = True
page._delete_chat()
check(page._store.get("active_id") != cid_before,
      "confirming must delete the chat")

# --- 新建的空对话：删了不损失什么，不多问一步 ---
asked.clear()
check(not page._history, "fresh chat must start empty")
page._delete_chat()
check(not asked, "an empty chat must be deleted without an extra confirm")

print("SCENARIO-OK", flush=True)
os._exit(0)
"""


class AiDeleteChatConfirmTest(unittest.TestCase):
    def test_delete_asks_only_when_there_is_history(self):
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
