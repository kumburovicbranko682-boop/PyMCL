# -*- coding: utf-8 -*-
"""账号页零账号状态必须诚实：不冒名「Steve」、空态文案与列表实际内容一致。

以前：没有任何账号时皮肤面板写着「Steve」像已登录；
列表空态说「还没有正版或皮肤站账号」，但这个列表其实也收录离线账号，
而且一句下一步都没有。

场景在子进程里跑：PYMCL_HOME 指临时目录、offscreen、无窗口。
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
from app.pages.account_page import AccountPage
from qfluentwidgets import CaptionLabel

def check(cond, msg):
    if not cond:
        print("FAIL:", msg)
        raise SystemExit(1)

backend = BackendAPI()
page = AccountPage(backend)

check(backend.get_account_rows() == [], "fresh home must start with no accounts")
check(page.skin_name.text() != "Steve",
      "no accounts must not impersonate 'Steve'")
check("未登录" in page.skin_name.text(),
      f"skin panel should say not-signed-in, got {page.skin_name.text()!r}")

empty = None
for i in range(page.list_box.count()):
    w = page.list_box.itemAt(i).widget()
    if isinstance(w, CaptionLabel):
        empty = w.text()
check(empty is not None, "empty account list must show a caption")
check("离线" in empty and "微软" in empty,
      f"empty copy must mention every way to add an account, got {empty!r}")

# 保存离线账号后：出现在列表里、皮肤面板显示真名
backend.add_offline_account("TestKid", "steve")
page.reload()
names = [r["name"] for r in backend.get_account_rows()]
check(names == ["TestKid"], f"offline account must land in the list, got {names}")
check(page.skin_name.text() == "TestKid",
      f"skin panel must show the real active account, got {page.skin_name.text()!r}")

print("SCENARIO-OK")
os._exit(0)
"""


class AccountEmptyStateTest(unittest.TestCase):
    def test_no_fake_identity_and_honest_copy(self):
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
