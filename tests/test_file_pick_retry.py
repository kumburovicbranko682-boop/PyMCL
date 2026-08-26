# -*- coding: utf-8 -*-
"""目录文件选择框加载失败必须能重试，不能只剩一行裸错误。

钉住的行为：
- 拉取文件列表失败后，状态行给出翻译过的「加载失败」并露出「重试」按钮；
- 点「重试」真的再发一次请求，成功后列表填充、按钮收回；
- 状态行的计数文案走 tr()，不再是硬编码中文 f-string。

全程 offscreen，后端打桩，不联网、不弹窗。
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYMCL_HOME", tempfile.mkdtemp(prefix="pymcl_test_filepick_"))

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mclauncher import feedback as _fb  # noqa: E402

_fb.start_heartbeat = lambda *a, **k: None
_fb.stop_heartbeat = lambda *a, **k: None

from mclauncher.config import CONFIG  # noqa: E402
from mclauncher.i18n import tr  # noqa: E402

_app = None


def setUpModule():
    global _app
    CONFIG.set("first_run", False)
    CONFIG.set("feedback_consent", False)
    CONFIG.save()
    from PySide6.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication([])


class _FakeBackend:
    """无 call_async 属性 → FilePickDialog 走同步路径，便于断言。"""

    def __init__(self):
        self.calls = 0
        self.fail = True

    def list_catalog_files(self, extra):
        self.calls += 1
        if self.fail:
            raise RuntimeError("network down")
        return [{
            "version_number": "1.0.0",
            "game_versions": ["1.20.1"],
            "loaders": ["fabric"],
            "date": "2024-01-01",
            "downloads": 5,
            "filename": "a.jar",
            "id": "x",
            "source": "modrinth",
        }]

    def get_instances(self):
        return []

    def get_mods_targets(self, inst):
        return [{"label": tr("实例共享 mods 目录"), "value": ""}]


class FilePickRetryTests(unittest.TestCase):
    def _dialog(self, backend):
        from PySide6.QtWidgets import QWidget
        from app.pages.file_pick import FilePickDialog
        self._host = QWidget()
        self._host.resize(900, 700)
        item = {"name": "Some Mod", "slug": "some-mod", "source": "modrinth"}
        return FilePickDialog(backend, item, "mod", parent=self._host)

    def test_fetch_error_shows_retry_and_retry_works(self):
        backend = _FakeBackend()
        dlg = self._dialog(backend)
        _app.processEvents()

        self.assertEqual(backend.calls, 1)
        self.assertFalse(dlg.retry_btn.isHidden(), "加载失败后应露出「重试」按钮")
        self.assertIn(tr("加载失败"), dlg.status.text())
        self.assertIn("network down", dlg.status.text())

        backend.fail = False
        dlg.retry_btn.click()
        _app.processEvents()

        self.assertEqual(backend.calls, 2, "点「重试」应真的再请求一次")
        self.assertTrue(dlg.retry_btn.isHidden(), "成功后重试按钮应收回")
        self.assertEqual(len(dlg._rows), 1)
        self.assertEqual(dlg.status.text(),
                         tr("{0} 个匹配 / 共 {1} 个文件").format(1, 1))

    def test_success_path_has_no_retry_button(self):
        backend = _FakeBackend()
        backend.fail = False
        dlg = self._dialog(backend)
        _app.processEvents()

        self.assertTrue(dlg.retry_btn.isHidden())
        self.assertEqual(len(dlg._rows), 1)
        self.assertEqual(dlg.status.text(),
                         tr("{0} 个匹配 / 共 {1} 个文件").format(1, 1),
                         "状态行计数应走 tr()，换语言不残留中文")


if __name__ == "__main__":
    unittest.main(verbosity=2)
