# -*- coding: utf-8 -*-
"""「检查并更新」动的必须是屏幕上正看着的 mods 目录。

以前：模组管理页可以切到「某版本 · 独立 mods」目录看列表，
「下载 → Mod → 已安装」也能按版本目录筛选；但更新动作只接收实例名，
永远去改实例共享 mods 目录——人看着 A 文件夹，按钮动的是 B 文件夹。

现在：两个入口都把当前版本目标传给后端，确认框和任务标题
写明「实例 / 版本」，后端把隔离目录一路传到 check/apply。

场景在子进程里跑：PYMCL_HOME 指临时目录、offscreen、
确认框/任务/目录列表全打桩（不联网、不弹窗、不动文件）。
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

# --- 后端：传了版本就把该版本的隔离目录交给 check/apply ---
# （mods_dir 的隔离/共享判定是 version_settings 自己的事，这里只验证
#   版本参数有没有一路传到 check_updates 的 mods_path）
import mclauncher.mod_update as mu
import mclauncher.version_settings as vs
vs.mods_dir = lambda inst, vid: Path("/isolated") / vid / "mods"
seen_paths = []
def fake_check(inst, dm=None, mods_path=None, **kw):
    seen_paths.append(mods_path)
    return []
mu.check_updates = fake_check
backend._mod_update_impl(lambda *a: None, lambda *a: None, "default", "1.20.1")
check(seen_paths and seen_paths[-1] is not None
      and "1.20.1" in str(seen_paths[-1]),
      f"backend must check the isolated dir, got {seen_paths!r}")
backend._mod_update_impl(lambda *a: None, lambda *a: None, "default", "")
check(seen_paths[-1] is None,
      "no version selected must still mean the shared mods dir")

# --- 任务标题写明目标目录 ---
titles = []
backend.start_task = lambda title, *a, **kw: (titles.append(title), "task-x")[1]
backend.start_mod_updates("default", "1.20.1")
check("default / 1.20.1" in titles[-1],
      f"task title must name the version dir, got {titles[-1]!r}")

# --- 模组管理页：切到独立目录后，确认框和任务都对准它 ---
backend.get_mods_targets = lambda inst: [
    {"label": "实例共享 mods 目录", "value": ""},
    {"label": "1.20.1 · 独立 mods", "value": "1.20.1"},
]
backend.get_installed_mod_entries = lambda inst, ver="": []
calls = []
backend.start_mod_updates = lambda inst, ver="": (calls.append((inst, ver)), "task-1")[1]

asked = []
def fake_confirm(parent, target):
    asked.append(target)
    return True
mod_page_mod.confirm_mod_update = fake_confirm
widgets.confirm_mod_update = fake_confirm

page = ModManagerPage(backend)
page.target_box.setCurrentIndex(1)
page.update_btn.click()
check(calls and calls[-1] == ("default", "1.20.1"),
      f"mod page must update the dir on screen, got {calls!r}")
check(asked and "1.20.1" in asked[-1],
      f"confirm must name the version dir, got {asked!r}")

page.target_box.setCurrentIndex(0)
page.update_btn.click()
check(calls[-1] == ("default", ""),
      f"shared dir view must update the shared dir, got {calls!r}")

# --- 「下载 → Mod → 已安装」：版本筛选框同样生效 ---
backend.get_installed_versions = lambda inst, include_hidden=False: ["1.20.1"]
cat = PclCatalogPage(backend, MOD_SPEC)
cat.reload_installed()
cat.installed_ver_box.setCurrentText("1.20.1")
cat.update_btn.click()
check(calls[-1] == ("default", "1.20.1"),
      f"catalog entry must pass the selected version dir, got {calls!r}")

print("SCENARIO-OK", flush=True)
os._exit(0)
"""


class ModUpdateTargetsVisibleDirTest(unittest.TestCase):
    def test_update_hits_the_dir_on_screen(self):
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
