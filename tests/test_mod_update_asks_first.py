# -*- coding: utf-8 -*-
"""「检查更新」按钮必须说实话，并在替换文件前问一声。

以前：模组管理页和「下载 → Mod」里的按钮都叫「检查更新」，
点下去后端却直接下载新 jar 替换、删掉旧文件，全程不确认——
人以为只是看一眼有没有更新，mods 文件夹已经被改写了。

现在：按钮叫「检查并更新」，动手前弹确认（两个入口共用同一份文案），
任务标题也承认会替换文件。

场景在子进程里跑：PYMCL_HOME 指临时目录、offscreen、
确认框与后端任务都打桩（不真联网、不弹窗、不动文件）。
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
import app.widgets as widgets
import app.pages.mod_page as mod_page_mod
from app.pages.mod_page import ModManagerPage
from app.pages.catalog_page import MOD_SPEC, PclCatalogPage

def check(cond, msg):
    if not cond:
        print("FAIL:", msg, flush=True)
        raise SystemExit(1)

backend = BackendAPI()

# --- 任务标题不许再装成只读「检查」，且仍计入下载任务（底部条 + 角标）---
titles = []
backend.start_task = lambda title, *a, **kw: (titles.append(title), "task-x")[1]
backend.start_mod_updates("default")
check(titles and "检查并更新" in titles[0],
      f"task title must admit it updates, got {titles!r}")
check(BackendAPI.is_download_title(titles[0]),
      "update task must still show in the download dock and badge")

# --- 模组管理页：按钮文案 + 先确认再动手 ---
page = ModManagerPage(backend)
check(page.update_btn.text() == "检查并更新",
      f"button must not pretend to be read-only, got {page.update_btn.text()!r}")
check("替换" in page.update_btn.toolTip(),
      "tooltip must say files get replaced")

calls = []
backend.start_mod_updates = lambda inst, ver="": (calls.append(inst), "task-1")[1]

def fake_confirm(parent, inst):
    fake_confirm.asked.append(inst)
    return fake_confirm.answer
fake_confirm.asked = []
mod_page_mod.confirm_mod_update = fake_confirm

fake_confirm.answer = False
page.update_btn.click()
check(fake_confirm.asked, "clicking must ask before touching files")
check(calls == [], "declining the confirm must not start the update task")

fake_confirm.answer = True
page.update_btn.click()
check(len(calls) == 1, "confirming must start the update task")

# --- 「下载 → Mod → 已安装」入口：同一文案、同一确认 ---
widgets.confirm_mod_update = fake_confirm
cat = PclCatalogPage(backend, MOD_SPEC)
check(cat.update_btn.text() == "检查并更新",
      f"catalog entry must use the same honest label, got {cat.update_btn.text()!r}")

fake_confirm.answer = False
before = len(calls)
cat.update_btn.click()
check(len(calls) == before, "catalog entry must honor a declined confirm too")

fake_confirm.answer = True
cat.update_btn.click()
check(len(calls) == before + 1, "catalog entry must start the task after confirm")

print("SCENARIO-OK", flush=True)
os._exit(0)
"""


class ModUpdateAsksFirstTest(unittest.TestCase):
    def test_update_button_is_honest_and_confirmed(self):
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

    def test_both_entries_share_one_confirm(self):
        """两个入口必须共用 widgets.confirm_mod_update，不许各写一份文案。"""
        for rel in ("app/pages/mod_page.py", "app/pages/catalog_page.py"):
            src = (ROOT / rel).read_text(encoding="utf-8")
            self.assertIn("confirm_mod_update", src,
                          f"{rel} 应使用共享的更新确认框")


if __name__ == "__main__":
    unittest.main()
