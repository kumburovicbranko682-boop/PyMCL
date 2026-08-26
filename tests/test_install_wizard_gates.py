# -*- coding: utf-8 -*-
"""安装向导：注定失败的选项当场禁用，不让人装到一半才知道。

钉住的行为：
- LiteLoader 只支持 1.7–1.12：给 1.20.1 开向导时复选框禁用、
  未勾选，且文字说明原因（版本号 + 支持范围）；
- payload 不会带上 liteloader=True；
- 1.7.10 / 1.12.2 等支持的版本复选框保持可用、文字不变；
- 对话框标题走翻译（"安装 {0}"），不再是硬编码中文拼接。

全程 offscreen、不 show 不 exec，加载器版本列表打桩，不联网。
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

from mclauncher import feedback as _fb  # noqa: E402

_fb.start_heartbeat = lambda *a, **k: None
_fb.stop_heartbeat = lambda *a, **k: None

from mclauncher.i18n import tr  # noqa: E402

_app = None


def setUpModule():
    global _app
    from PySide6.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication([])


class InstallWizardGateTests(unittest.TestCase):
    def _dialog(self, mc_version):
        from unittest import mock
        from PySide6.QtWidgets import QWidget
        from app.backend import BackendAPI
        p = mock.patch.object(
            BackendAPI, "list_loader_versions", lambda self, *a, **k: [])
        p.start()
        self.addCleanup(p.stop)
        from app.pages.install_wizard import InstallWizardDialog
        host = QWidget()
        host.resize(900, 700)
        self.addCleanup(host.deleteLater)
        dlg = InstallWizardDialog(BackendAPI(), mc_version, "default", host)
        _app.processEvents()
        return dlg

    def test_liteloader_disabled_on_modern_version(self):
        dlg = self._dialog("1.20.1")
        self.assertFalse(dlg.liteloader.isEnabled(),
                         "1.20.1 不该让人勾 LiteLoader，装到一半才失败")
        self.assertFalse(dlg.liteloader.isChecked())
        text = dlg.liteloader.text()
        self.assertIn("1.20.1", text, "禁用理由里应写明当前版本")
        self.assertIn("1.7", text, "禁用理由里应写明支持范围")
        self.assertFalse(dlg.payload()["extra"]["liteloader"],
                         "payload 不得带 liteloader=True")

    def test_liteloader_enabled_on_supported_versions(self):
        for ver in ("1.7.10", "1.12.2", "1.8"):
            dlg = self._dialog(ver)
            self.assertTrue(dlg.liteloader.isEnabled(),
                            f"{ver} 在 LiteLoader 支持范围内，复选框应可用")
            self.assertEqual(dlg.liteloader.text(),
                             tr("同时安装 LiteLoader（1.7–1.12）"))
            dlg.liteloader.setChecked(True)
            self.assertTrue(dlg.payload()["extra"]["liteloader"])

    def test_supported_range_matches_backend(self):
        # 1.13+ 和快照都不在 liteloader.py 的支持列表里
        from app.pages.install_wizard import liteloader_supported
        self.assertTrue(liteloader_supported("1.7.2"))
        self.assertTrue(liteloader_supported("1.12.2"))
        self.assertFalse(liteloader_supported("1.13"))
        self.assertFalse(liteloader_supported("1.6.4"))
        self.assertFalse(liteloader_supported("24w14a"))
        self.assertFalse(liteloader_supported(""))

    def test_title_is_translated(self):
        dlg = self._dialog("1.20.1")
        title = dlg.viewLayout.itemAt(0).widget().text()
        self.assertEqual(title, tr("安装 {0}").format("1.20.1"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
