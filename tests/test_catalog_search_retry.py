# -*- coding: utf-8 -*-
"""目录页搜索失败必须给「重试」按钮，而不是一句报错就完。

以前：网络抖一下，Mod/整合包等八个目录页只显示「搜索失败: xxx」
（前缀还没走翻译），没有任何可点的下一步。

场景在子进程里跑：PYMCL_HOME 指临时目录、offscreen、
搜索函数打桩（先抛错后返回结果），不发真实网络请求。
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
from app.pages.catalog_page import ModPage
from app.widgets import EmptyState

def check(cond, msg):
    if not cond:
        print("FAIL:", msg)
        raise SystemExit(1)

def wait(ms=500):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()

backend = BackendAPI()
attempts = []
def fake_search(query, source, extra=None):
    attempts.append(query)
    if len(attempts) == 1:
        raise RuntimeError("连接超时")
    return [{"name": "JEI", "title": "Just Enough Items", "id": "jei"}]
backend.search_mods = fake_search

page = ModPage(backend)
page.name_edit.setText("jei")
page._search()
wait()

def find_empty(p):
    for i in range(p.list_layout.count()):
        w = p.list_layout.itemAt(i).widget()
        if isinstance(w, EmptyState):
            return w
    return None

empty = find_empty(page)
check(empty is not None, "failed search must show an empty state")
text = empty._text_label.text()
check("搜索失败" in text and "连接超时" in text,
      f"failure copy must include the reason, got {text!r}")
check(empty.action_btn is not None and empty.action_btn.text() == "重试",
      "failed search must offer a retry button")

empty.action_btn.click()
wait()
check(len(attempts) == 2, f"retry must re-run the search, attempts={len(attempts)}")
check(find_empty(page) is None or "搜索失败" not in (
          find_empty(page)._text_label.text() if find_empty(page) else ""),
      "successful retry must replace the failure state")

print("SCENARIO-OK")
os._exit(0)
"""


class CatalogSearchRetryTest(unittest.TestCase):
    def test_failed_search_offers_retry(self):
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
