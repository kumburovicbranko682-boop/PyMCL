# -*- coding: utf-8 -*-
"""回归：backend.py:200 类级 Signal 访问导致的 'no attribute emit' 崩溃。"""
import os, sys, traceback
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
app = QApplication([])

from app.backend import BackendAPI

b = BackendAPI()
hits = []
b.ui_changed.connect(lambda: hits.append(1))

# 直接走崩溃函数（此前在这里 AttributeError）
b._emit_ui_changed()
assert hits == [1], f"emit 未送达: {hits}"

# 复现截图路径：_on_worker_finished -> _emit_ui_changed（success=True 分支）
b._on_worker_finished("probe-task", True, "ok")
assert len(hits) == 2, f"任务完成回调未广播: {hits}"
assert b._task_results["probe-task"] == (True, "ok")

print("PASS signal-crash regression (emit delivered x2, no AttributeError)")
