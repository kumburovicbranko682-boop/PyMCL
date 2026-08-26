# -*- coding: utf-8 -*-
"""「隔离」档位必须解释会发生什么。

钉住的行为：
- 每个隔离档位都有说明文字，且与 apply_isolation 的真实行为一致
 （说明来源于 ISOLATION_HINTS 单一出处）；
- 版本设置对话框里切换档位，说明实时更新；
- 设置页「新版本默认隔离」卡片的说明也随选择更新。

全程 offscreen、不 show 不 exec，不弹任何窗口。
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

from mclauncher import feedback as _fb  # noqa: E402

_fb.start_heartbeat = lambda *a, **k: None
_fb.stop_heartbeat = lambda *a, **k: None

from mclauncher.i18n import tr  # noqa: E402
from mclauncher.version_settings import ISOLATION_HINTS, ISOLATION_LABELS  # noqa: E402

_app = None


def setUpModule():
    global _app
    from PySide6.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication([])


class IsolationHintTests(unittest.TestCase):
    def test_every_mode_has_hint(self):
        for mode in ISOLATION_LABELS:
            self.assertTrue(ISOLATION_HINTS.get(mode),
                            f"隔离档位 {mode} 缺少解释文字")

    def test_version_setup_hint_follows_selection(self):
        from PySide6.QtWidgets import QWidget
        from app.backend import BackendAPI
        from app.pages.version_setup import VersionSetupDialog
        host = QWidget()
        host.resize(900, 700)
        backend = BackendAPI()
        dlg = VersionSetupDialog(backend, "default", "1.20.1", host)
        for mode, label in ISOLATION_LABELS.items():
            dlg.iso.setCurrentText(tr(label))
            _app.processEvents()
            self.assertEqual(dlg.iso_hint.text(), tr(ISOLATION_HINTS[mode]),
                             f"档位 {mode} 的说明没跟上选择")

    def test_settings_page_desc_follows_selection(self):
        from PySide6.QtWidgets import QWidget
        from app.backend import BackendAPI
        from app.pages.settings_page import SettingsPage
        host = QWidget()
        host.resize(1100, 760)
        backend = BackendAPI()
        page = SettingsPage(backend, host)
        for mode, label in ISOLATION_LABELS.items():
            page.iso_box.setCurrentText(tr(label))
            _app.processEvents()
            content = page.iso_card.contentLabel.text()
            self.assertIn(tr(ISOLATION_HINTS[mode]), content,
                          f"设置页档位 {mode} 的说明没跟上选择")


if __name__ == "__main__":
    unittest.main(verbosity=2)
