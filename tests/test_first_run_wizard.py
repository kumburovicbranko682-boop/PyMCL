# -*- coding: utf-8 -*-
"""首次运行向导：只问真正需要确认的两件事，并且答案真的生效。

钉住的行为：
- 向导只有 游戏目录 + 下载源 两个输入，和它自己的提示文案一致；
  不再让还没装过游戏的人回答「新版本默认隔离」这种无法回答的问题；
- 选「仅 BMCLAPI」并确认后，download_source 真的写成 bmclapi；
- 改游戏目录后，实例目录真的指向新路径；
- first_run 被清掉，下次不再弹。

全程 offscreen、不 show 不 exec，不弹任何窗口。
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYMCL_HOME", tempfile.mkdtemp(prefix="pymcl_test_wizard_"))

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mclauncher import feedback as _fb  # noqa: E402

_fb.start_heartbeat = lambda *a, **k: None
_fb.stop_heartbeat = lambda *a, **k: None

from mclauncher.config import CONFIG  # noqa: E402
from mclauncher.i18n import tr  # noqa: E402

_app = None


def setUpModule():
    global _app
    from PySide6.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication([])


class FirstRunWizardTests(unittest.TestCase):
    def _make(self):
        from PySide6.QtWidgets import QWidget
        from app.backend import BackendAPI
        from app.pages.first_run import FirstRunDialog
        self._host = QWidget()
        self._host.resize(800, 600)  # 不 show：仅作 MessageBoxBase 的父级
        backend = BackendAPI()
        return FirstRunDialog(backend, self._host), backend

    def test_only_two_questions(self):
        dlg, _ = self._make()
        # 向导承诺「先选好游戏目录和下载源」，就不该再冒出内存/隔离字段
        self.assertFalse(hasattr(dlg, "memory"),
                         "首次向导不应再问默认内存（设置页可改）")
        self.assertFalse(hasattr(dlg, "iso"),
                         "首次向导不应再问「新版本默认隔离」（新用户无法回答）")
        self.assertTrue(hasattr(dlg, "game_dir"))
        self.assertTrue(hasattr(dlg, "src"))

    def test_apply_writes_real_settings(self):
        dlg, _ = self._make()
        new_dir = tempfile.mkdtemp(prefix="pymcl_test_gamedir_")
        dlg.game_dir.setText(new_dir)
        dlg.src.setCurrentText(tr("仅 BMCLAPI"))
        dlg.apply()
        self.assertEqual(CONFIG.get("download_source"), "bmclapi")
        self.assertFalse(CONFIG.get("first_run"))
        self.assertEqual(str(CONFIG.instances_dir), new_dir)

    def test_defaults_preserved_without_wizard_fields(self):
        # 裁掉字段后，内存/隔离仍是配置默认值，没有被 apply 抹掉
        before_mem = CONFIG.get("memory_mb", 4096)
        before_iso = CONFIG.get("default_isolation") or "none"
        dlg, _ = self._make()
        dlg.apply()
        self.assertEqual(CONFIG.get("memory_mb", 4096), before_mem)
        self.assertEqual(CONFIG.get("default_isolation") or "none", before_iso)


if __name__ == "__main__":
    unittest.main(verbosity=2)
