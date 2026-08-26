# -*- coding: utf-8 -*-
"""UI 审计截图：单次 app.exec() 内遍历全部页面，浅色+深色各抓一张。"""

import os
import sys
import traceback

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from mclauncher import feedback as _fb  # noqa: E402
_fb.start_heartbeat = lambda *a, **k: None
_fb.stop_heartbeat = lambda *a, **k: None

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

OUT = sys.argv[1] if len(sys.argv) > 1 else "_shots/audit"
LANG = sys.argv[2] if len(sys.argv) > 2 else ""
os.makedirs(OUT, exist_ok=True)

app = QApplication([])

if LANG:
    from mclauncher import i18n
    i18n._ensure()
    i18n._current_lang = LANG  # 不落盘

from app.main_window import MainWindow  # noqa: E402
from mclauncher.config import CONFIG  # noqa: E402

win = MainWindow()
win.resize(1280, 800)
win.show()

KEYS = ["launch", "version", "mod", "modpack", "datapack", "resource", "shader",
        "world", "java", "instance", "mods", "account", "multiplayer", "servers",
        "playtime", "feedback", "settings", "ai", "tasks"]

# 导航与截图放在不同的事件循环轮次：同一回调里 deleteLater 的旧控件
# 还没销毁、会一起画进截图，看起来像“控件重影”的假 bug。
steps = []
for mode in ("light", "dark"):
    steps.append(("theme", mode))
    for k in KEYS:
        steps.append(("page", k))
        steps.append(("grab", f"{mode}-{k}"))

idx = 0


def step():
    global idx
    if idx >= len(steps):
        # 复位浅色，不污染 config
        CONFIG.set("ui_dark", False)
        app.quit()
        return
    s = steps[idx]
    idx += 1
    try:
        if s[0] == "theme":
            CONFIG.set("ui_dark", s[1] == "dark")
            win.apply_theme()
        elif s[0] == "page":
            win.switchTo(s[1])
        else:
            ok = win.grab().save(f"{OUT}/{s[1]}.png")
            print(("saved " if ok else "FAILED ") + s[1], flush=True)
    except Exception:
        traceback.print_exc()
    # SlideHStack 动画 260ms：至少等它跑完再抓，否则截到中间帧当假 bug
    QTimer.singleShot(600, step)


QTimer.singleShot(900, step)
app.exec()
print("done", flush=True)
