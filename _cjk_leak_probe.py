# -*- coding: utf-8 -*-
"""英文界面下逐页扫可见控件文本，揪出漏翻的中文（含后端常量）。

静态 AST 审计只能看到 app/ 里的字面量；后端 mclauncher 返回的中文状态串
要等真跑起来才落到控件上，所以在 _shots_probe 的同一套单次 app.exec()
遍历骨架上，把「截图」换成「扫文本」。
"""

import os
import re
import sys
import traceback

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from mclauncher import feedback as _fb  # noqa: E402
_fb.start_heartbeat = lambda *a, **k: None
_fb.stop_heartbeat = lambda *a, **k: None

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

CJK = re.compile(r"[\u4e00-\u9fff]")

app = QApplication([])

from mclauncher import i18n  # noqa: E402
i18n._ensure()
i18n._current_lang = "en"  # 不落盘

from app.main_window import MainWindow  # noqa: E402
from mclauncher.config import CONFIG  # noqa: E402

win = MainWindow()
win.resize(1280, 800)
win.show()

KEYS = ["launch", "version", "mod", "modpack", "datapack", "resource", "shader",
        "world", "java", "instance", "mods", "account", "multiplayer", "servers",
        "playtime", "feedback", "settings", "ai", "tasks"]

found: dict[str, list[str]] = {}


def scan(page_key: str):
    hits = found.setdefault(page_key, [])
    seen = set()
    for w in win.findChildren(QWidget):
        if not w.isVisible():
            continue
        texts = []
        for attr in ("text", "title", "placeholderText", "currentText", "toolTip"):
            fn = getattr(w, attr, None)
            if callable(fn):
                try:
                    t = fn()
                except Exception:
                    continue
                if isinstance(t, str) and CJK.search(t):
                    texts.append((attr, t))
        # ComboBox 除当前项外把所有条目也扫一遍
        cnt = getattr(w, "count", None)
        item_text = getattr(w, "itemText", None)
        if callable(cnt) and callable(item_text):
            try:
                for i in range(cnt()):
                    t = item_text(i)
                    if isinstance(t, str) and CJK.search(t):
                        texts.append(("item", t))
            except Exception:
                pass
        for attr, t in texts:
            key = (type(w).__name__, attr, t)
            if key in seen:
                continue
            seen.add(key)
            hits.append(f"{type(w).__name__}.{attr}: {t[:80]}")


steps = []
for k in KEYS:
    steps.append(("page", k))
    steps.append(("scan", k))

idx = 0


def step():
    global idx
    if idx >= len(steps):
        CONFIG.set("ui_dark", False)
        app.quit()
        return
    s = steps[idx]
    idx += 1
    try:
        if s[0] == "page":
            win.switchTo(s[1])
        else:
            scan(s[1])
    except Exception:
        traceback.print_exc()
    QTimer.singleShot(600, step)


QTimer.singleShot(900, step)
app.exec()

total = 0
for k in KEYS:
    hits = found.get(k) or []
    if hits:
        print(f"== {k} ({len(hits)}) ==", flush=True)
        for h in hits:
            print("  " + h, flush=True)
        total += len(hits)
print(f"done total={total}", flush=True)
