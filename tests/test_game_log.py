# -*- coding: utf-8 -*-
"""游戏日志窗口：级别解析、堆栈延续、增量 tail、门面 game_log、UI 冒烟。"""
import collections
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mclauncher import game_log
from mclauncher.launcher import GameProcess


class ParseLevelTests(unittest.TestCase):
    def test_bracket_levels(self):
        cases = {
            "[12:34:56] [Render thread/INFO]: Loaded 7 recipes": "info",
            "[12:34:56] [Server thread/WARN]: Can't keep up!": "warn",
            "[12:34:56] [main/ERROR]: Mixin apply failed": "error",
            "[12:34:56] [main/FATAL]: Unreported exception": "fatal",
            "[12:34:56] [Worker/DEBUG]: scanning": "debug",
            "[12:34:56] [IO/TRACE]: packet in": "trace",
        }
        for line, want in cases.items():
            self.assertEqual(game_log.parse_level(line), want, line)

    def test_warning_normalized(self):
        self.assertEqual(
            game_log.parse_level("[x] [main/WARNING]: legacy"), "warn")

    def test_stderr_and_exception_lines(self):
        self.assertEqual(game_log.parse_level(
            "[12:00:00] [STDERR]: something bad"), "error")
        self.assertEqual(game_log.parse_level(
            'Exception in thread "main" java.lang.RuntimeException: boom'), "error")
        self.assertEqual(game_log.parse_level(
            "java.lang.NullPointerException: Cannot invoke method"), "error")

    def test_stack_lines_inherit_previous(self):
        self.assertEqual(game_log.parse_level(
            "\tat net.minecraft.client.main.Main.main(Main.java:1)", "error"), "error")
        self.assertEqual(game_log.parse_level(
            "Caused by: java.lang.ClassNotFoundException: x", "fatal"), "fatal")
        self.assertEqual(game_log.parse_level("... 12 more", "warn"), "warn")

    def test_plain_lines_default_info(self):
        self.assertEqual(game_log.parse_level("Setting user: Steve"), "info")
        self.assertEqual(game_log.parse_level(""), "info")

    def test_annotate_and_counts(self):
        rows = game_log.annotate([
            "[1] [main/ERROR]: crash",
            "\tat a.b.c(D.java:1)",
            "[2] [main/INFO]: ok",
        ])
        self.assertEqual([r[0] for r in rows], ["error", "error", "info"])
        counts = game_log.count_levels(rows)
        self.assertEqual(counts["error"], 2)
        self.assertEqual(counts["info"], 1)

    def test_export_lines(self):
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "sub" / "out.log"
            path = game_log.export_lines(["a", "b"], dest)
            self.assertEqual(Path(path).read_text(encoding="utf-8"), "a\nb\n")


class TailTests(unittest.TestCase):
    def _proc(self, lines, total, maxlen=3):
        gp = GameProcess.__new__(GameProcess)
        gp.lines = collections.deque(lines, maxlen=maxlen)
        gp.total_lines = total
        return gp

    def test_full_tail(self):
        gp = self._proc(["l3", "l4", "l5"], 5)
        out = gp.tail(0)
        self.assertEqual(out, {"start": 2, "total": 5, "lines": ["l3", "l4", "l5"]})

    def test_incremental(self):
        gp = self._proc(["l3", "l4", "l5"], 5)
        out = gp.tail(4)
        self.assertEqual(out, {"start": 4, "total": 5, "lines": ["l5"]})

    def test_since_at_or_past_total(self):
        gp = self._proc(["l3", "l4", "l5"], 5)
        self.assertEqual(gp.tail(5)["lines"], [])
        self.assertEqual(gp.tail(99)["lines"], [])

    def test_real_process_capture(self):
        code = "print('hello'); import sys; print('oops', file=sys.stderr)"
        gp = GameProcess([sys.executable, "-u", "-c", code], cwd=".")
        gp.wait(timeout=15)
        lines = gp.last_lines()
        self.assertIn("hello", lines)
        self.assertIn("oops", lines)
        self.assertEqual(gp.total_lines, len(lines))
        out = gp.tail(0)
        self.assertEqual(out["total"], gp.total_lines)


class _FakeProc:
    def __init__(self, lines, total, running=True):
        self._out = {"start": total - len(lines), "total": total, "lines": lines}
        self._running = running

    def tail(self, since=0):
        return dict(self._out)

    def poll(self):
        return None if self._running else 0


class FacadeGameLogTests(unittest.TestCase):
    def _fake_self(self, procs, last=None):
        import threading
        import types
        ns = types.SimpleNamespace()
        ns._game_lock = threading.Lock()
        ns._game_procs = procs
        ns._game_proc = last
        return ns

    def test_qt_backend_game_log(self):
        from app.backend import BackendAPI as QtBackend
        proc = _FakeProc(["a", "b"], 2)
        fake = self._fake_self({"t1": {"proc": proc}}, last=proc)
        out = QtBackend.game_log(fake, "t1", 0)
        self.assertEqual(out["lines"], ["a", "b"])
        self.assertTrue(out["running"])
        # 空 task_id 走最近一次启动的游戏
        out = QtBackend.game_log(fake, "", 0)
        self.assertEqual(out["total"], 2)
        # 没有进程时给空结构
        empty = self._fake_self({}, last=None)
        out = QtBackend.game_log(empty, "", 0)
        self.assertEqual(out, {"start": 0, "total": 0, "lines": [], "running": False})

    def test_bridge_backend_game_log(self):
        from bridge.api import BackendAPI as BridgeBackend
        proc = _FakeProc(["x"], 1, running=False)
        fake = self._fake_self({"t9": {"proc": proc}}, last=proc)
        out = BridgeBackend.game_log(fake, "t9", 0)
        self.assertEqual(out["lines"], ["x"])
        self.assertFalse(out["running"])


class ShowLogResolveTests(unittest.TestCase):
    def test_version_override_beats_global(self):
        from unittest.mock import patch
        from mclauncher import launch_flow
        from mclauncher.config import CONFIG
        with patch.object(CONFIG, "get", return_value=True):
            self.assertFalse(launch_flow.resolve_show_log({"show_log": "off"}))
            self.assertTrue(launch_flow.resolve_show_log({"show_log": "on"}))
        with patch.object(CONFIG, "get", return_value=False):
            self.assertTrue(launch_flow.resolve_show_log({"show_log": "on"}))
            self.assertFalse(launch_flow.resolve_show_log({"show_log": ""}))
            self.assertFalse(launch_flow.resolve_show_log({}))
            self.assertFalse(launch_flow.resolve_show_log(None))

    def test_global_fallback(self):
        from unittest.mock import patch
        from mclauncher.config import CONFIG
        from mclauncher import launch_flow
        with patch.object(CONFIG, "get", return_value=True):
            self.assertTrue(launch_flow.resolve_show_log({}))

    def test_defaults_declared(self):
        from mclauncher.config import DEFAULT_CONFIG
        from mclauncher import version_settings
        self.assertIs(DEFAULT_CONFIG.get("show_log_window"), False)
        self.assertEqual(version_settings.DEFAULTS.get("show_log"), "")

    def test_facades_emit_log_request(self):
        root = Path(__file__).resolve().parents[1]
        qt_src = (root / "app" / "backend.py").read_text(encoding="utf-8")
        self.assertIn("game_log_requested = Signal(str, str)", qt_src)
        self.assertIn('prep.get("show_log")', qt_src)
        br_src = (root / "bridge" / "api.py").read_text(encoding="utf-8")
        self.assertIn('"game_log_requested"', br_src)
        self.assertIn('prep.get("show_log")', br_src)
        mw_src = (root / "app" / "main_window.py").read_text(encoding="utf-8")
        self.assertIn("game_log_requested.connect", mw_src)


class _LogBackend:
    """按 since 返回预置日志块的假后端，call_async 同步执行。"""

    def __init__(self, chunks):
        self.chunks = chunks  # since -> dict

    def game_log(self, task_id="", since=0):
        return self.chunks.get(int(since),
                               {"start": since, "total": since, "lines": [],
                                "running": True})

    def call_async(self, fn, ok=None, fail=None):
        try:
            result = fn()
        except Exception as e:  # noqa: BLE001
            if fail:
                fail(e)
            return
        if ok:
            ok(result)


class LogWindowUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def _window(self, chunks):
        from app.pages.game_log_window import GameLogWindow
        backend = _LogBackend(chunks)
        win = GameLogWindow(backend, "t1", "1.20.1")
        win._timer.stop()  # 测试里手动驱动轮询
        return win

    def test_appends_counts_and_stops_on_exit(self):
        win = self._window({
            0: {"start": 0, "total": 2, "running": True,
                "lines": ["[1] [main/INFO]: hi", "[2] [main/ERROR]: bad"]},
            2: {"start": 2, "total": 3, "running": False,
                "lines": ["\tat a.b(C.java:1)"]},
        })
        try:
            # 构造时已 poll 过一次（since=0）
            self.assertEqual(win._since, 2)
            self.assertEqual(win._counts["info"], 1)
            self.assertEqual(win._counts["error"], 1)
            win._poll()  # 拉到堆栈行，继承 error 级别；游戏退出后停表
            self.assertEqual(win._counts["error"], 2)
            self.assertFalse(win._timer.isActive())
            text = win.view.toPlainText()
            self.assertIn("hi", text)
            self.assertIn("bad", text)
            self.assertIn("ERROR (2)", win.filters["error"].text())
        finally:
            win.deleteLater()

    def test_filter_and_search(self):
        win = self._window({
            0: {"start": 0, "total": 3, "running": True,
                "lines": ["[1] [main/INFO]: alpha",
                          "[2] [main/WARN]: beta",
                          "[3] [main/ERROR]: gamma"]},
        })
        try:
            win.filters["info"].setChecked(False)
            text = win.view.toPlainText()
            self.assertNotIn("alpha", text)
            self.assertIn("beta", text)
            win.search.setText("gam")
            text = win.view.toPlainText()
            self.assertNotIn("beta", text)
            self.assertIn("gamma", text)
        finally:
            win.deleteLater()

    def test_export_writes_all_rows(self):
        import tempfile
        win = self._window({
            0: {"start": 0, "total": 1, "running": True,
                "lines": ["[1] [main/INFO]: keepme"]},
        })
        try:
            with tempfile.TemporaryDirectory() as td:
                dest = Path(td) / "game.log"
                with unittest.mock.patch(
                        "app.pages.game_log_window.QFileDialog.getSaveFileName",
                        return_value=(str(dest), "")), \
                     unittest.mock.patch(
                        "app.pages.game_log_window.InfoBar"):
                    win._export()
                self.assertIn("keepme", dest.read_text(encoding="utf-8"))
        finally:
            win.deleteLater()


if __name__ == "__main__":
    unittest.main()
