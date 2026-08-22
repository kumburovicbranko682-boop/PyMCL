# -*- coding: utf-8 -*-
"""随机拖手柄的模糊测试：找出还会造成卡片重叠的缩放路径。

真实 MainWindow + 真实 QMouseEvent 合成（走 _Grip 的完整事件链）。
每次松手后检查所有卡片两两相交；发现重叠即打印操作序列并停。
"""
import os
import random
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, ".")

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtCore import QEvent
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest

app = QApplication([])
from app.main_window import MainWindow

random.seed(20260822)
win = MainWindow()
win.resize(1400, 900)
win.show()
QTest.qWaitForWindowExposed(win)

canvas = win.launch_page.canvas
canvas.set_edit_mode(True)
QTest.qWait(80)

DIRS = ["e", "w", "s", "n", "se", "sw", "ne", "nw"]


def synth_drag(widget, dx, dy, steps=6):
    c = QPointF(widget.width() / 2, widget.height() / 2)
    gp = QPointF(widget.mapToGlobal(c.toPoint()))

    def send(t, lp, btns):
        QApplication.sendEvent(widget, QMouseEvent(
            t, lp, gp + (lp - c), Qt.LeftButton, btns, Qt.NoModifier))

    send(QEvent.MouseButtonPress, c, Qt.LeftButton)
    for i in range(1, steps + 1):
        send(QEvent.MouseMove, c + QPointF(dx * i / steps, dy * i / steps), Qt.LeftButton)
    send(QEvent.MouseButtonRelease, c + QPointF(dx, dy), Qt.NoButton)


def overlaps():
    bad = []
    cs = list(canvas.cards)
    for i in range(len(cs)):
        for j in range(i + 1, len(cs)):
            a, b = cs[i], cs[j]
            ox = min(a.x() + a.width(), b.x() + b.width()) - max(a.x(), b.x())
            oy = min(a.y() + a.height(), b.y() + b.height()) - max(a.y(), b.y())
            if ox > 2 and oy > 2:
                bad.append((a.item.type, b.item.type, int(ox), int(oy)))
    return bad


def snapshot():
    return {c.item.type: (c.x(), c.y(), c.width(), c.height()) for c in canvas.cards}


actions = []
found = []
for trial in range(120):
    card = random.choice(canvas.cards)
    d = random.choice(DIRS)
    dx = random.randint(-400, 400) if ("e" in d or "w" in d) else random.randint(-60, 60)
    dy = random.randint(-300, 300) if ("s" in d or "n" in d) else random.randint(-60, 60)
    grip = next(g for g in card.grips if g.direction == d)
    before = snapshot()
    synth_drag(grip, dx, dy)
    QTest.qWait(20)
    actions.append((card.item.type, d, dx, dy))
    bad = overlaps()
    if bad:
        found = bad
        break

print("cards:", snapshot())
if found:
    print("OVERLAP after:", actions[-1], "->", found)
    print("last 5 actions:", actions[-5:])
    sys.exit(1)
print(f"NO OVERLAP in {len(actions)} random grip drags")
