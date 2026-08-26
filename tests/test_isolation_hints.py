# -*- coding: utf-8 -*-
"""隔离档位必须有人话解释，且三个入口（首次向导 / 版本设置 / 设置页）同源。

钉住的行为：
1. 四个隔离档各自有一句非空且互不相同的解释；
2. 首次向导里切换隔离档位，解释标签实时更新；
3. 版本设置对话框同样带解释标签。

全程 offscreen + 临时数据目录，对话框只构造不 exec，不弹任何窗口。
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYMCL_HOME", tempfile.mkdtemp(prefix="pymcl_test_iso_"))

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

_app = QApplication.instance() or QApplication([])
_host = QWidget()  # MessageBoxBase 需要带尺寸的父控件；不 show，不弹窗
_host.resize(1000, 700)

from mclauncher.i18n import tr  # noqa: E402
from mclauncher.version_settings import ISOLATION_HINTS, ISOLATION_LABELS  # noqa: E402
from app.widgets import isolation_hint  # noqa: E402


class HintTableTests(unittest.TestCase):
    def test_every_tier_has_distinct_hint(self):
        hints = []
        for key, label in ISOLATION_LABELS.items():
            hint = isolation_hint(tr(label))
            self.assertTrue(hint, f"档位 {key} 缺解释")
            hints.append(hint)
        self.assertEqual(len(set(hints)), len(hints), "档位解释必须互不相同")

    def test_unknown_label_returns_empty(self):
        self.assertEqual(isolation_hint("并不存在的档位"), "")

    def test_hints_cover_all_keys(self):
        self.assertEqual(set(ISOLATION_HINTS), set(ISOLATION_LABELS))


class FirstRunDialogTests(unittest.TestCase):
    def test_hint_follows_selection(self):
        from app.backend import BackendAPI
        from app.pages.first_run import FirstRunDialog

        backend = BackendAPI(None)
        dlg = FirstRunDialog(backend, _host)  # 只构造，不 exec
        try:
            for label in ISOLATION_LABELS.values():
                dlg.iso.setCurrentText(tr(label))
                _app.processEvents()
                self.assertEqual(dlg.iso_hint.text(), isolation_hint(tr(label)))
        finally:
            dlg.deleteLater()
            _app.processEvents()


class VersionSetupDialogTests(unittest.TestCase):
    def test_dialog_has_live_hint(self):
        from app.backend import BackendAPI
        from app.pages.version_setup import VersionSetupDialog

        backend = BackendAPI(None)
        dlg = VersionSetupDialog(backend, "default", "1.21.1", _host)
        try:
            self.assertTrue(dlg.iso_hint.text(), "初始就该有解释")
            dlg.iso.setCurrentText(tr(ISOLATION_LABELS["all"]))
            _app.processEvents()
            self.assertEqual(dlg.iso_hint.text(),
                             isolation_hint(tr(ISOLATION_LABELS["all"])))
        finally:
            dlg.deleteLater()
            _app.processEvents()


if __name__ == "__main__":
    unittest.main(verbosity=2)
