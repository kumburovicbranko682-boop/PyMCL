# -*- coding: utf-8 -*-
"""启动页「游戏时长」卡片：玩完一局回来，数字必须跟上。

以前：卡片只在构造时刷新一次，之后永远不更新——玩了一下午，
启动页还挂着昨天的总时长，直到重启启动器或重排布局。

现在：卡片订阅 game_exited 信号，游戏一退出就重新拉取时长。

场景在子进程里跑：PYMCL_HOME 指临时目录、offscreen、
时长数据打桩（不启动游戏、不弹窗）。
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
import json, os, types
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
from app.pages.home_cards import PlaytimeBody

def check(cond, msg):
    if not cond:
        print("FAIL:", msg, flush=True)
        raise SystemExit(1)

backend = BackendAPI()
state = {"total": 60, "all": {"default": {"total": 60}}}
backend.get_total_playtime = lambda: state["total"]
backend.get_all_playtime = lambda: state["all"]

card = QWidget()
page = types.SimpleNamespace(backend=backend)
body = PlaytimeBody(page, card, None)
first = body.total.text()
check("—" not in first, f"card must show real numbers at build time, got {first!r}")

# 玩了一局：后台时长涨了，游戏退出信号一响，卡片要立刻跟上
state["total"] = 60 + 3600
state["all"] = {"default": {"total": 60 + 3600}}
backend.game_exited.emit(0)
second = body.total.text()
check(second != first,
      f"card must refresh after the game exits, still shows {second!r}")
check(backend.format_playtime(3660) in second,
      f"card must show the new total, got {second!r}")

print("SCENARIO-OK", flush=True)
os._exit(0)
"""


class PlaytimeCardFreshTest(unittest.TestCase):
    def test_card_refreshes_on_game_exit(self):
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
