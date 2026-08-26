# -*- coding: utf-8 -*-
"""启动页空状态：没装任何版本时不许骗人、不许弹死胡同模态框。

钉住的行为：
1. 没有已安装版本 → 横幅写「还没有安装游戏」，主按钮变「安装游戏」；
2. 此时点主按钮 → 直接切到「下载 → 原版游戏」页，不弹 MessageBox；
3. 装上一个版本后 → 横幅回到版本号，主按钮回到「启动游戏」。

全程 offscreen + 临时数据目录，不碰用户真机 .minecraft，不弹任何窗口。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_TMP = tempfile.mkdtemp(prefix="pymcl_test_empty_")
os.environ["PYMCL_HOME"] = _TMP

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mclauncher import feedback as _fb  # noqa: E402

_fb.start_heartbeat = lambda *a, **k: None
_fb.stop_heartbeat = lambda *a, **k: None

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from app.main_window import MainWindow  # noqa: E402
from mclauncher.i18n import tr  # noqa: E402


def _make_window():
    # _boot_extras 会 exec 首次向导（模态框），测试里必须掐掉
    win = MainWindow.__new__(MainWindow)
    win._boot_extras = lambda: None
    MainWindow.__init__(win)
    return win


class _NoModal:
    """在作用域内 MessageBox.exec 一旦被调用就直接测试失败。"""

    def __init__(self, case):
        self.case = case

    def __enter__(self):
        import qfluentwidgets

        self._orig = qfluentwidgets.MessageBox.exec
        case = self.case

        def _fail(_self, *a, **k):
            case.fail("空状态点启动不应弹 MessageBox")

        qfluentwidgets.MessageBox.exec = _fail
        return self

    def __exit__(self, *exc):
        import qfluentwidgets

        qfluentwidgets.MessageBox.exec = self._orig
        return False


class LaunchEmptyStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.win = _make_window()
        cls.win.resize(1180, 760)
        cls.win.show()
        _app.processEvents()

    @classmethod
    def tearDownClass(cls):
        cls.win.close()
        _app.processEvents()

    def test_1_empty_state_is_honest(self):
        lp = self.win.launch_page
        self.assertEqual(lp.version_box.count(), 0, "前提：干净目录里没有版本")
        self.assertEqual(lp.banner.title.text(), tr("还没有安装游戏"))
        self.assertEqual(lp.launch_btn.text(), tr("安装游戏"))
        self.assertTrue(lp.launch_btn.isEnabled())

    def test_2_click_goes_to_version_page_without_modal(self):
        lp = self.win.launch_page
        with _NoModal(self):
            lp.launch_btn.click()
            _app.processEvents()
        self.assertEqual(self.win._visible_key(), "version",
                         "空状态点主按钮应直接落到「下载 → 原版游戏」页")

    def test_3_with_installed_version_button_says_launch(self):
        win = self.win
        lp = win.launch_page
        # 造一个最小的已安装版本（只要 versions/<id>/<id>.json 存在即可）
        from mclauncher.config import CONFIG

        vdir = Path(CONFIG.instances_dir) / "default" / "versions" / "1.21.1"
        vdir.mkdir(parents=True, exist_ok=True)
        (vdir / "1.21.1.json").write_text(json.dumps({"id": "1.21.1"}), "utf-8")
        win.backend._inst_cache = None  # 失效实例快照缓存
        lp.reload()
        _app.processEvents()

        self.assertIn("1.21.1", [lp.version_box.itemText(i)
                                 for i in range(lp.version_box.count())])
        self.assertEqual(lp.launch_btn.text(), tr("启动游戏"))
        self.assertEqual(lp.banner.title.text(), "1.21.1")


if __name__ == "__main__":
    unittest.main(verbosity=2)
