# -*- coding: utf-8 -*-
"""首次向导的取消按钮：文案必须与行为一致——跳过即用默认值且不再弹。

以前叫「以后再说」，但行为是 first_run 置 False、永不再问。
全程 offscreen + 临时数据目录，向导 exec 打桩，不弹任何窗口。
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYMCL_HOME", tempfile.mkdtemp(prefix="pymcl_test_skip_"))

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mclauncher import feedback as _fb  # noqa: E402

_fb.start_heartbeat = lambda *a, **k: None
_fb.stop_heartbeat = lambda *a, **k: None

from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

_app = QApplication.instance() or QApplication([])
_host = QWidget()
_host.resize(1000, 700)

from mclauncher.config import CONFIG  # noqa: E402
from mclauncher.i18n import tr  # noqa: E402
from app.backend import BackendAPI  # noqa: E402


class FirstRunSkipTests(unittest.TestCase):
    def test_cancel_button_tells_the_truth(self):
        from app.pages.first_run import FirstRunDialog

        dlg = FirstRunDialog(BackendAPI(None), _host)
        try:
            self.assertEqual(dlg.cancelButton.text(), tr("跳过（用默认设置）"))
        finally:
            dlg.deleteLater()
            _app.processEvents()

    def test_skip_marks_first_run_done(self):
        CONFIG.set("first_run", True)
        CONFIG.set("feedback_consent", False)   # 挡掉同意弹窗
        CONFIG.set("auto_check_update", False)  # 挡掉联网检查
        CONFIG.save()

        from app.pages import first_run as fr

        orig_exec = fr.FirstRunDialog.exec
        fr.FirstRunDialog.exec = lambda self: 0  # 模拟点「跳过」
        try:
            from app.main_window import MainWindow

            win = MainWindow()
            win._boot_extras()
            self.assertFalse(win.backend.get_setting("first_run", True),
                             "跳过后 first_run 必须落成 False，向导不再弹")
            win.close()
            _app.processEvents()
        finally:
            fr.FirstRunDialog.exec = orig_exec


if __name__ == "__main__":
    unittest.main(verbosity=2)
