# -*- coding: utf-8 -*-
"""存档对话框：指路对得上侧栏、文案走翻译、装数据包有回音。

钉住的行为：
- 「没有数据包」的提示指向真实入口「下载 → 数据包」，不再是含糊的「下载页」；
- 标题与各确认/完成提示走 tr()（英文界面不夹生中文）；
- install_datapack_into_save 调用有 try/except 和完成提示，不再静默成功、
  裸抛失败。

全程 offscreen，后端打桩，不弹窗（对话框只构造不 exec）。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYMCL_HOME", tempfile.mkdtemp(prefix="pymcl_test_saves_"))

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mclauncher import feedback as _fb  # noqa: E402

_fb.start_heartbeat = lambda *a, **k: None
_fb.stop_heartbeat = lambda *a, **k: None

from mclauncher.i18n import tr  # noqa: E402

_app = None


def setUpModule():
    global _app
    from PySide6.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication([])


class SavesDialogCopyTests(unittest.TestCase):
    def test_datapack_hint_points_to_real_page(self):
        src = (ROOT / "app" / "pages" / "saves_dialog.py").read_text(encoding="utf-8")
        self.assertNotIn("先到下载页安装数据包", src,
                         "「下载页」在侧栏不存在，必须写「下载 → 数据包」")
        self.assertIn("下载 → 数据包", src)

    def test_datapack_install_has_feedback(self):
        src = (ROOT / "app" / "pages" / "saves_dialog.py").read_text(encoding="utf-8")
        idx = src.find("install_datapack_into_save(")
        self.assertGreater(idx, 0)
        tail = src[idx:idx + 600]
        self.assertIn("except Exception", src[idx - 200:idx + 600],
                      "装数据包失败必须有错误提示，不能裸抛")
        self.assertIn("安装完成", tail, "装数据包成功必须有回音")

    def test_locale_keys_exist(self):
        keys = [
            "存档 · {0}",
            "确定删除备份「{0}」？",
            "确定删除「{0}」？",
            "「{0}」正在打包，进度见「下载任务」。",
            "已还原为存档「{0}」。",
            "已导出到：\n{0}",
            "先到「下载 → 数据包」安装。",
            "数据包已装进存档「{0}」。",
            "安装失败",
        ]
        for loc in ("en.json", "zh_CN.json"):
            data = json.loads((ROOT / "mclauncher" / "locales" / loc).read_text(encoding="utf-8"))
            for k in keys:
                self.assertIn(k, data, f"{loc} 缺少键：{k}")

    def test_dialog_title_translated(self):
        from unittest import mock
        from PySide6.QtWidgets import QWidget
        from qfluentwidgets import SubtitleLabel
        from app.backend import BackendAPI
        patches = [
            mock.patch.object(BackendAPI, "list_saves", lambda self, i, v="": []),
            mock.patch.object(BackendAPI, "list_save_backups", lambda self, i, n="", v="": []),
            mock.patch.object(BackendAPI, "list_media", lambda self, i, k, v="": []),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        host = QWidget()
        host.resize(900, 700)
        self.addCleanup(host.deleteLater)
        from app.pages.saves_dialog import SavesDialog
        dlg = SavesDialog(BackendAPI(), "default", "", host)
        _app.processEvents()
        labels = [w.text() for w in dlg.findChildren(SubtitleLabel)]
        self.assertIn(tr("存档 · {0}").format("default"), labels,
                      "对话框标题应经 tr() 渲染")


if __name__ == "__main__":
    unittest.main(verbosity=2)
