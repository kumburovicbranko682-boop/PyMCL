# -*- coding: utf-8 -*-
"""启动页空状态（还没装任何版本）必须给出真实可走的下一步。

钉住的行为：
- 没有已安装版本时，横幅不再写「点击『启动游戏』进入世界」，
  主按钮变成「安装游戏」；
- 点主按钮不弹「预检未通过」死胡同框，而是直接切到 下载 → 原版游戏；
- 装上版本后按钮恢复「启动游戏」，横幅显示版本号。

全程 offscreen，不弹任何窗口，数据目录用临时目录。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_TMP_HOME = tempfile.mkdtemp(prefix="pymcl_test_empty_")
os.environ["PYMCL_HOME"] = _TMP_HOME

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mclauncher import feedback as _fb  # noqa: E402

_fb.start_heartbeat = lambda *a, **k: None
_fb.stop_heartbeat = lambda *a, **k: None

from mclauncher.config import CONFIG  # noqa: E402
from mclauncher.i18n import tr  # noqa: E402

_app = None


def setUpModule():
    global _app
    # 关掉一切会弹对话框/联网的开机行为
    CONFIG.set("first_run", False)
    CONFIG.set("feedback_consent", False)
    CONFIG.set("auto_check_update", False)
    CONFIG.save()
    from PySide6.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication([])


def _make_window():
    from app.backend import BackendAPI
    from unittest import mock
    patches = [
        mock.patch.object(BackendAPI, "fetch_news", lambda self: []),
        mock.patch.object(BackendAPI, "cached_news", lambda self: []),
        mock.patch.object(BackendAPI, "fetch_version_list", lambda self: []),
        mock.patch.object(BackendAPI, "check_update", lambda self: {}),
    ]
    for p in patches:
        p.start()
    from app.main_window import MainWindow
    win = MainWindow()
    win._pymcl_test_patches = patches
    return win


def _close(win):
    for p in getattr(win, "_pymcl_test_patches", []):
        p.stop()
    win.close()
    _app.processEvents()


class LaunchEmptyStateTests(unittest.TestCase):
    def test_empty_then_installed(self):
        import qfluentwidgets

        win = _make_window()
        try:
            win.show()
            _app.processEvents()
            lp = win.launch_page

            # ---- 空状态：按钮和横幅说实话 ----
            self.assertEqual(lp.version_box.count(), 0)
            self.assertEqual(lp.launch_btn.text(), tr("安装游戏"))
            self.assertEqual(lp.banner.title.text(), tr("先安装一个游戏版本"))
            self.assertNotIn("启动游戏", lp.banner.subtitle.text())

            # ---- 点「安装游戏」：不弹框，直接到 下载 → 原版游戏 ----
            popped = []
            orig_exec = qfluentwidgets.MessageBox.exec
            qfluentwidgets.MessageBox.exec = (
                lambda self, *a, **k: popped.append(self) or 0)
            try:
                lp.launch_btn.click()
                _app.processEvents()
            finally:
                qfluentwidgets.MessageBox.exec = orig_exec
            self.assertEqual(popped, [], "空状态点主按钮不应弹任何 MessageBox")
            self.assertEqual(win._visible_key(), "version",
                             "空状态点主按钮应直接切到 下载 → 原版游戏")

            # ---- 装上一个版本后：按钮恢复「启动游戏」 ----
            from mclauncher.instances import Instance
            inst = Instance("default")
            vdir = inst.versions_dir() / "1.20.1"
            vdir.mkdir(parents=True, exist_ok=True)
            (vdir / "1.20.1.json").write_text(
                json.dumps({"id": "1.20.1", "type": "release"}),
                encoding="utf-8")
            lp.reload()
            _app.processEvents()
            self.assertIn("1.20.1", [lp.version_box.itemText(i)
                                     for i in range(lp.version_box.count())])
            self.assertEqual(lp.launch_btn.text(), tr("启动游戏"))
            self.assertEqual(lp.banner.title.text(), "1.20.1")
        finally:
            _close(win)


if __name__ == "__main__":
    unittest.main(verbosity=2)
