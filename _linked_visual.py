# -*- coding: utf-8 -*-
"""联动缩放可视化演示：真实窗口上把左卡拉宽，右侧邻居应同步让位。

截图输出到 _shots/linked_before.png / linked_after.png。
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
sys.path.insert(0, str(Path(__file__).parent))
os.chdir(Path(__file__).parent)

from mclauncher import feedback as _fb
_fb.start_heartbeat = lambda *a, **k: None

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

app = QApplication(sys.argv)
from app.main_window import MainWindow

win = MainWindow()
win.resize(1400, 900)
win.show()

SHOTS = Path("_shots")
SHOTS.mkdir(exist_ok=True)
state = {"step": 0}


def canvas_of(win):
    page = getattr(win, "launch_page", None)
    return page, getattr(page, "canvas", None)


def step():
    page, canvas = canvas_of(win)
    if canvas is None:
        print("NO CANVAS")
        app.quit()
        return
    s = state["step"]
    if s == 0:
        # 三栏：config / log / news。找 config（最左）与 log（中栏）
        cards = sorted(canvas.cards, key=lambda c: c.x())
        names = [c.item.type for c in cards]
        print("cards:", names)
        state.update({"cards": cards, "step": 1})
        canvas.set_edit_mode(True)
        QTimer.singleShot(400, step)
    elif s == 1:
        cards = state["cards"]
        left = next(c for c in cards if c.item.type == "config")
        right = next(c for c in cards if c.item.type == "log")
        print("before: left", (left.x(), left.width()), "log", (right.x(), right.width()))
        state.update({"left": left, "right": right,
                      "gap0": right.x() - (left.x() + left.width())})
        win.grab().save(str(SHOTS / "linked_before.png"))
        # 拖左卡 e 手柄向右 180px（分 12 帧模拟真实拖拽）
        state.update({"frames": 12, "i": 0, "x0": left.x(), "y0": left.y(),
                      "w0": left.width(), "h0": left.height(),
                      "right_w0": right.width(), "step": 2})
        QTimer.singleShot(60, step)
    elif s == 2:
        st = state
        i = st["i"]
        dx = int(180 * (i + 1) / st["frames"])
        st["left"]._resize_by("e", st["x0"], st["y0"], st["w0"], st["h0"], dx, 0)
        st["i"] += 1
        if st["i"] < st["frames"]:
            QTimer.singleShot(30, step)
        else:
            st["left"]._commit_geometry()
            print("after:  left", (st["left"].x(), st["left"].width()),
                  "log", (st["right"].x(), st["right"].width()))
            ok = abs((st["left"].x() + st["left"].width() + st["gap0"]) - st["right"].x()) <= 1
            print(f"gap {st['gap0']}px kept:", ok,
                  "| log shrank by", st["right_w0"] - st["right"].width(), "px")
            win.grab().save(str(SHOTS / "linked_after.png"))
            state["step"] = 3
            QTimer.singleShot(1200, step)
    else:
        canvas_of(win)[1].set_edit_mode(False)
        print("DONE")
        app.quit()


QTimer.singleShot(900, step)
QTimer.singleShot(25000, app.quit)  # 兜底退出
app.exec()
