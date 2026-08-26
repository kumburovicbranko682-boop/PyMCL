# -*- coding: utf-8 -*-
"""临时探针：离屏抓各主要页面截图，审计视觉问题用。

单次 event loop 内串行执行（QTimer 链），窗口全程保持可见——
中途 app.exec() 退出会把顶层窗口隐藏，showEvent/isVisible 相关
逻辑全部失真，抓出来的截图不代表真实运行状态。
"""
import os
import sys
import traceback

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from mclauncher import feedback as _fb  # noqa: E402
_fb.start_heartbeat = lambda *a, **k: None
_fb.stop_heartbeat = lambda *a, **k: None

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

OUT = "_shots"
os.makedirs(OUT, exist_ok=True)

app = QApplication([])

from app.main_window import MainWindow  # noqa: E402
from mclauncher.config import CONFIG  # noqa: E402

win = MainWindow()
win.resize(1280, 800)
win.show()

_steps = []


def step(fn=None, *, shot=None, settle=700):
    _steps.append((fn, shot, settle))


step(settle=800)  # 等首页装配完
step(shot="01-launch")
step(lambda: win.side.set_current("download", emit=True))
step(shot="02-download-version")
step(lambda: win.switchTo("mod"))
step(shot="03-mod")
step(lambda: win.switchTo("java"))
step(shot="04-java")
step(lambda: win.side.set_current("ai", emit=True))
step(shot="05-ai")
step(lambda: win.side.set_current("more", emit=True))
step(shot="06-more-instance")
step(lambda: win.switchTo("account"))
step(shot="07-account")
step(lambda: win.switchTo("settings"))
step(shot="08-settings")
step(lambda: win.switchTo("multiplayer"))
step(shot="09-multiplayer")
step(lambda: win.switchTo("feedback"))
step(shot="10-feedback")
step(lambda: win.switchTo("playtime"))
step(shot="11-playtime")
step(lambda: win.side.set_current("tasks", emit=True))
step(shot="12-tasks")

step(lambda: win.side.set_current("launch", emit=True))
step(lambda: win.launch_page.canvas.set_edit_mode(True))
step(shot="13-edit-mode")
step(lambda: win.launch_page.canvas.set_edit_mode(False))


def _dark(on):
    CONFIG.set("ui_dark", on)
    CONFIG.save()
    win.apply_theme()


step(lambda: _dark(True))
step(shot="14-launch-dark")
step(lambda: win.switchTo("settings"))
step(shot="15-settings-dark")
step(lambda: _dark(False))

step(lambda: win.resize(900, 620))
step(lambda: win.side.set_current("launch", emit=True))
step(shot="16-launch-narrow")


def _run_next():
    if not _steps:
        print("DONE")
        app.quit()
        return
    fn, shot, settle = _steps.pop(0)
    try:
        if fn is not None:
            fn()
        if shot is not None:
            ok = win.grab().save(f"{OUT}/{shot}.png")
            print(("saved " if ok else "FAILED ") + shot)
    except Exception:
        traceback.print_exc()
        print("FAIL")
        app.quit()
        sys.exit(1)
    # 动作步之后等 settle 让动画/布局走完；纯截图步之间只留 60ms
    QTimer.singleShot(settle if shot is None else 60, _run_next)


QTimer.singleShot(0, _run_next)
app.exec()
