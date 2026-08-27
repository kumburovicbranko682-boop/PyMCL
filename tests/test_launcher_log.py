# -*- coding: utf-8 -*-
"""启动器运行日志文件（PCL2 Log1~5 / HMCL latest.log 同款）。

覆盖：文件创建与会话头、启动轮转、幂等、tail 截取、两个门面。
"""
import logging
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mclauncher import utils


class _Isolated(unittest.TestCase):
    """独立 ROOT + 干净的模块态；结束时摘掉文件 handler，别污染其他用例。"""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.root = Path(self._td.name)
        for p in (patch.object(utils, "ROOT", self.root),
                  patch.object(utils, "_file_log_path", None)):
            p.start()
            self.addCleanup(p.stop)
        self._before = list(utils.log.handlers)
        self.addCleanup(self._drop_new_handlers)

    def _drop_new_handlers(self):
        for h in list(utils.log.handlers):
            if h not in self._before:
                utils.log.removeHandler(h)
                try:
                    h.close()
                except Exception:
                    pass

    def _log_file(self) -> Path:
        return self.root / "logs" / utils.LAUNCHER_LOG_NAME


class SetupTests(_Isolated):
    def test_creates_file_with_session_header(self):
        path = utils.setup_file_logging()
        self.assertEqual(Path(path), self._log_file())
        utils.log.info("hello file")
        text = self._log_file().read_text(encoding="utf-8")
        self.assertIn("Python", text)          # 会话头
        self.assertIn("启动器主目录", text)
        self.assertIn("hello file", text)

    def test_idempotent(self):
        p1 = utils.setup_file_logging()
        n_handlers = len(utils.log.handlers)
        p2 = utils.setup_file_logging()
        self.assertEqual(p1, p2)
        self.assertEqual(len(utils.log.handlers), n_handlers)

    def test_rotation_shifts_and_drops_oldest(self):
        folder = self.root / "logs"
        folder.mkdir(parents=True)
        (folder / utils.LAUNCHER_LOG_NAME).write_text("run0", encoding="utf-8")
        for i in range(1, 6):
            (folder / f"launcher-{i}.log").write_text(f"run{i}", encoding="utf-8")
        utils.setup_file_logging()
        # 上一轮 launcher.log → launcher-1.log，其余顺移，最老的 run5 被删
        self.assertEqual((folder / "launcher-1.log").read_text(encoding="utf-8"), "run0")
        self.assertEqual((folder / "launcher-2.log").read_text(encoding="utf-8"), "run1")
        self.assertEqual((folder / "launcher-5.log").read_text(encoding="utf-8"), "run4")
        contents = {p.read_text(encoding="utf-8")
                    for p in folder.glob("launcher-*.log")}
        self.assertNotIn("run5", contents)
        # 新一轮 launcher.log 是新会话
        self.assertIn("Python", (folder / utils.LAUNCHER_LOG_NAME)
                      .read_text(encoding="utf-8"))

    def test_tail(self):
        utils.setup_file_logging()
        utils.log.info("marker-%s", "x" * 50)
        tail = utils.launcher_log_tail(40)
        self.assertLessEqual(len(tail), 40)
        self.assertIn("xxx", tail)
        self.assertEqual(utils.launcher_log_tail(0), "")

    def test_tail_without_setup_reads_last_run(self):
        folder = self.root / "logs"
        folder.mkdir(parents=True)
        (folder / utils.LAUNCHER_LOG_NAME).write_text("previous run", encoding="utf-8")
        self.assertEqual(utils.launcher_log_tail(), "previous run")

    def test_unwritable_dir_returns_none(self):
        # logs 位置被一个同名文件占住 → mkdir 失败 → 返回 None 不炸
        (self.root / "logs").write_text("not a dir", encoding="utf-8")
        self.assertIsNone(utils.setup_file_logging())
        self.assertIsNone(utils.launcher_log_path())


class FacadeTests(_Isolated):
    def test_bridge(self):
        from bridge.api import BackendAPI

        class _Bus:
            def emit(self, *a, **k):
                pass

        api = BackendAPI(_Bus())
        utils.setup_file_logging()
        utils.log.info("bridge marker")
        self.assertIn("bridge marker", api.launcher_log_tail(2000))
        with patch("bridge.api.open_path", lambda p: True) as _:
            folder = api.open_launcher_logs()
        self.assertEqual(Path(folder), self.root / "logs")

    def test_qt_backend_static(self):
        from app.backend import BackendAPI as QtBackend
        utils.setup_file_logging()
        utils.log.info("qt marker")
        self.assertIn("qt marker", QtBackend.launcher_log_tail(None, 2000))
        with patch("app.backend.open_path", lambda p: True):
            folder = QtBackend.open_launcher_logs(None)
        self.assertEqual(Path(folder), self.root / "logs")


if __name__ == "__main__":
    unittest.main()
