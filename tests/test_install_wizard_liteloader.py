# -*- coding: utf-8 -*-
"""安装向导的 LiteLoader 复选框标签写着 1.7–1.12，就不许在别的版本上还能勾——
勾了也装不上，是假控件。

全程 offscreen + 临时数据目录，只构造不 exec，不弹任何窗口、不联网。
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYMCL_HOME", tempfile.mkdtemp(prefix="pymcl_test_wiz_"))

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

_app = QApplication.instance() or QApplication([])
_host = QWidget()

from app.backend import BackendAPI  # noqa: E402
from app.pages.install_wizard import (  # noqa: E402
    InstallWizardDialog, liteloader_supported,
)


def _dialog(mc_version):
    backend = BackendAPI(None)
    backend.call_async = lambda fn, ok, err=None: None   # 不联网拉加载器列表
    dlg = InstallWizardDialog(backend, mc_version, "default", _host)
    _app.processEvents()
    return dlg


class LiteLoaderGateTests(unittest.TestCase):
    def test_supported_range(self):
        for v, expect in [("1.7.10", True), ("1.12.2", True), ("1.8", True),
                          ("1.21.1", False), ("1.6.4", False), ("1.13", False),
                          ("24w14a", False), ("", False)]:
            self.assertEqual(liteloader_supported(v), expect, v)

    def test_modern_version_disables_checkbox(self):
        dlg = _dialog("1.21.1")
        try:
            self.assertFalse(dlg.liteloader.isEnabled(),
                             "1.21.1 没有 LiteLoader，复选框不该能勾")
            self.assertTrue(dlg.liteloader.toolTip(),
                            "禁用时应有 tooltip 解释原因")
            self.assertFalse(dlg.payload()["extra"]["liteloader"])
        finally:
            dlg.deleteLater()
            _app.processEvents()

    def test_legacy_version_keeps_checkbox(self):
        dlg = _dialog("1.12.2")
        try:
            self.assertTrue(dlg.liteloader.isEnabled(),
                            "1.12.2 有 LiteLoader，复选框应可用")
            dlg.liteloader.setChecked(True)
            self.assertTrue(dlg.payload()["extra"]["liteloader"])
        finally:
            dlg.deleteLater()
            _app.processEvents()


if __name__ == "__main__":
    unittest.main(verbosity=2)
