# -*- coding: utf-8 -*-
"""用户名字段必须跟着后端事实走：只在「离线模式」下可编辑。

以前：选中微软/皮肤站账号后用户名输入框照样能改，
但后端只在 account == 离线模式 时才用它——改了个寂寞。

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
from app.pages.launch_page import LaunchPage
from mclauncher.i18n import tr

def check(cond, msg):
    if not cond:
        print("FAIL:", msg)
        raise SystemExit(1)

backend = BackendAPI()
page = LaunchPage(backend)

# 初始：离线模式，可编辑
check(page.account_box.currentText() in ("", tr("离线模式")),
      f"fresh install defaults to offline, got {page.account_box.currentText()!r}")
check(page.username_edit.isEnabled(), "offline mode must keep the field editable")

# 加一个「正版」账号并选中：字段禁用 + 有解释
# （不带 access_token：带令牌会走系统钥匙串，无头环境下会挂在 D-Bus 上）
backend.accounts.add_account({
    "name": "SteveMS", "type": "microsoft", "uuid": "u-1",
})
page.reload()
check("SteveMS" in [page.account_box.itemText(i)
                    for i in range(page.account_box.count())],
      "new account must appear in the combo")
page.account_box.setCurrentText("SteveMS")
app.processEvents()
check(not page.username_edit.isEnabled(),
      "username must be disabled for a real account - the backend ignores it")
check(page.username_edit.toolTip() != "", "disabled field must explain why")

# 切回离线模式：恢复可编辑
page.account_box.setCurrentText(tr("离线模式"))
app.processEvents()
check(page.username_edit.isEnabled(), "back to offline re-enables the field")
check(page.username_edit.toolTip() == "", "no stale tooltip in offline mode")

print("SCENARIO-OK")
os._exit(0)
"""


class UsernameFieldHonestTest(unittest.TestCase):
    def test_username_follows_account_type(self):
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
