# -*- coding: utf-8 -*-
"""任务分类不能因界面语言而变：启动/登录不算下载任务。

钉住的行为：
- 「启动游戏」任务标题走 tr()：英文界面下 is_download_title 依然
  能认出它不是下载（此前标题是硬编码中文、判定用 tr() 前缀，
  切英文后启动游戏会误入下载条和侧栏红点）；
- 「统一通行证登录」和微软登录 / 皮肤站登录一样被排除；
- 「下载 Java」任务在两种语言下都算下载任务。

全程 offscreen，start_task 打桩成只记录标题，不跑任何任务。
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYMCL_HOME", tempfile.mkdtemp(prefix="pymcl_test_cls_"))

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mclauncher import feedback as _fb  # noqa: E402

_fb.start_heartbeat = lambda *a, **k: None
_fb.stop_heartbeat = lambda *a, **k: None

from mclauncher import i18n  # noqa: E402
from mclauncher.i18n import tr  # noqa: E402

_app = None


def setUpModule():
    global _app
    from PySide6.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication([])


class TaskClassificationTests(unittest.TestCase):
    def tearDown(self):
        i18n.set_language("zh_CN")

    def _titles(self, lang: str) -> dict:
        from unittest import mock
        from app.backend import BackendAPI
        i18n.set_language(lang)
        captured = []

        def fake_start(self, title, fn, *a, **k):
            captured.append(title)
            return f"task-{len(captured)}"

        with mock.patch.object(BackendAPI, "start_task", fake_start):
            b = BackendAPI()
            b.launch_game("default", "1.20.1", "acc", "Player", 4096, 854, 480,
                          java=tr("自动选择"))
            b.start_nide8_login("srv", "user", "pw")
            b.download_java("17", vendor="adoptium")
        return {"launch": captured[0], "nide8": captured[1], "java": captured[2]}

    def test_launch_and_logins_excluded_in_both_languages(self):
        from app.backend import BackendAPI
        for lang in ("zh_CN", "en"):
            titles = self._titles(lang)
            self.assertFalse(BackendAPI.is_download_title(titles["launch"]),
                             f"[{lang}] 启动游戏不能被当成下载任务：{titles['launch']}")
            self.assertFalse(BackendAPI.is_download_title(titles["nide8"]),
                             f"[{lang}] 统一通行证登录不能被当成下载任务：{titles['nide8']}")
            self.assertTrue(BackendAPI.is_download_title(titles["java"]),
                            f"[{lang}] 下载 Java 应算下载任务：{titles['java']}")

    def test_launch_title_follows_language(self):
        titles = self._titles("en")
        self.assertTrue(titles["launch"].startswith(tr("启动游戏")),
                        "英文界面下启动任务标题应是英文前缀")
        self.assertNotIn("启动游戏", titles["launch"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
