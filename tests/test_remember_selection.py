# -*- coding: utf-8 -*-
"""启动页要记住上次启动的实例和版本。

钉住的行为：
- 点「启动游戏」时把实例/版本写进 last_instance / last_version；
- 重建启动页（等价重启启动器）后，版本框选中上次玩的版本，
  而不是目录序的第一个。

全程 offscreen，launch_game / preflight 打桩，不真的拉起游戏。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYMCL_HOME", tempfile.mkdtemp(prefix="pymcl_test_sel_"))

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mclauncher import feedback as _fb  # noqa: E402

_fb.start_heartbeat = lambda *a, **k: None
_fb.stop_heartbeat = lambda *a, **k: None

from mclauncher.config import CONFIG  # noqa: E402

_app = None


def setUpModule():
    global _app
    CONFIG.set("first_run", False)
    CONFIG.set("feedback_consent", False)
    CONFIG.set("auto_check_update", False)
    CONFIG.save()
    from PySide6.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication([])


def _fake_version(vid: str):
    from mclauncher.instances import Instance
    vdir = Instance("default").versions_dir() / vid
    vdir.mkdir(parents=True, exist_ok=True)
    (vdir / f"{vid}.json").write_text(
        json.dumps({"id": vid, "type": "release"}), encoding="utf-8")


class RememberSelectionTests(unittest.TestCase):
    def test_launch_saves_and_new_page_restores_version(self):
        from unittest import mock
        from app.backend import BackendAPI

        # 目录序在前的 1.19.2 是默认选中项；上次玩的是 1.20.1
        _fake_version("1.19.2")
        _fake_version("1.20.1")

        launched = []
        patches = [
            mock.patch.object(BackendAPI, "fetch_news", lambda self: []),
            mock.patch.object(BackendAPI, "cached_news", lambda self: []),
            mock.patch.object(
                BackendAPI, "preflight_launch",
                lambda self, **kw: {"ok": True, "items": []}),
            mock.patch.object(
                BackendAPI, "launch_game",
                lambda self, **kw: launched.append(kw) or "task-1"),
        ]
        for p in patches:
            p.start()
        try:
            from app.pages.launch_page import LaunchPage
            backend = BackendAPI()
            page = LaunchPage(backend)
            _app.processEvents()
            page.version_box.setCurrentText("1.20.1")
            page._on_launch()
            _app.processEvents()
            self.assertEqual(len(launched), 1)
            self.assertEqual(CONFIG.get("last_instance"), "default")
            self.assertEqual(CONFIG.get("last_version"), "1.20.1")

            page2 = LaunchPage(backend)
            _app.processEvents()
            self.assertEqual(page2.version_box.currentText(), "1.20.1",
                             "重建启动页后应回到上次启动的版本")
        finally:
            for p in patches:
                p.stop()


if __name__ == "__main__":
    unittest.main(verbosity=2)
