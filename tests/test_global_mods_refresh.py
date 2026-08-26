# -*- coding: utf-8 -*-
"""全局 Mod 对话框不许装死：空状态让用户「打开文件夹放入 jar」，
放完回来列表必须自己刷出来（对话框开着就轮询目录）；
内容没变时不许重建行——1.5 秒重造一遍开关会吃掉用户正要点的那一下。

全程 offscreen + 临时数据目录，只构造不 exec，不弹任何窗口。
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYMCL_HOME", tempfile.mkdtemp(prefix="pymcl_test_gm_"))

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

_app = QApplication.instance() or QApplication([])
_host = QWidget()

from app.backend import BackendAPI  # noqa: E402
from app.pages.global_mods_dialog import GlobalModsDialog  # noqa: E402


def _dialog(rows_holder):
    backend = BackendAPI(None)
    backend.list_global_mods = lambda: list(rows_holder["rows"])
    backend.set_global_mod_enabled = lambda name, on: name
    dlg = GlobalModsDialog(backend, _host)
    _app.processEvents()
    return dlg


def _row_widgets(dlg):
    out = []
    for i in range(dlg.host.count()):
        w = dlg.host.itemAt(i).widget()
        if w is not None:
            out.append(w)
    return out


class GlobalModsRefreshTests(unittest.TestCase):
    def test_watch_timer_running(self):
        holder = {"rows": []}
        dlg = _dialog(holder)
        try:
            self.assertTrue(dlg._watch.isActive(), "对话框开着就该轮询目录")
            self.assertLessEqual(dlg._watch.interval(), 3000)
        finally:
            dlg._watch.stop()
            dlg.deleteLater()

    def test_new_jar_appears_without_reopen(self):
        from qfluentwidgets import SwitchButton
        holder = {"rows": []}
        dlg = _dialog(holder)
        try:
            self.assertFalse(dlg.findChildren(SwitchButton),
                             "空目录不该有开关")
            holder["rows"] = [{"filename": "sodium.jar", "enabled": True}]
            dlg.reload()          # 模拟一次轮询 tick
            _app.processEvents()
            self.assertTrue(dlg.findChildren(SwitchButton),
                            "放入 jar 后列表应自己刷出来，不该要求关掉重开")
        finally:
            dlg._watch.stop()
            dlg.deleteLater()

    def test_unchanged_poll_keeps_rows(self):
        holder = {"rows": [{"filename": "a.jar", "enabled": True}]}
        dlg = _dialog(holder)
        try:
            before = _row_widgets(dlg)
            self.assertTrue(before)
            dlg.reload()
            _app.processEvents()
            after = _row_widgets(dlg)
            self.assertEqual([id(w) for w in before], [id(w) for w in after],
                             "内容没变不许重建行，否则会吃掉用户正要点的开关")
        finally:
            dlg._watch.stop()
            dlg.deleteLater()

    def test_toggle_then_poll_keeps_rows(self):
        from qfluentwidgets import SwitchButton
        holder = {"rows": [{"filename": "a.jar", "enabled": True}]}
        dlg = _dialog(holder)
        try:
            sw = dlg.findChildren(SwitchButton)[0]
            sw.setChecked(False)      # 触发 _toggle
            _app.processEvents()
            # 后端状态已翻转，下一次轮询看到的就是新状态
            holder["rows"] = [{"filename": "a.jar", "enabled": False}]
            before = _row_widgets(dlg)
            dlg.reload()
            _app.processEvents()
            after = _row_widgets(dlg)
            self.assertEqual([id(w) for w in before], [id(w) for w in after],
                             "刚点完开关的下一次轮询不该重建列表")
        finally:
            dlg._watch.stop()
            dlg.deleteLater()


if __name__ == "__main__":
    unittest.main(verbosity=2)
