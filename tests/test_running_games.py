# -*- coding: utf-8 -*-
"""运行中游戏进程注册表：多开时逐个可见、可结束（对标 HMCL 游戏管理）。"""

import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class _FakePopen:
    def __init__(self, pid):
        self.pid = pid


class _FakeGameProc:
    """poll()/kill() 兼容 GameProcess 的假进程。"""

    def __init__(self, pid=1000, alive=True):
        self.proc = _FakePopen(pid)
        self._alive = alive
        self.killed = False
        self.started_at = time.time() - 65

    def poll(self):
        return None if self._alive else 0

    def kill(self):
        self.killed = True
        self._alive = False


def _make_api():
    from bridge.api import BackendAPI

    class _Bus:
        def emit(self, *a, **k):
            pass

    return BackendAPI(_Bus())


def _register(api, task_id, proc, instance="default", version="1.20.1", account="Steve"):
    with api._game_lock:
        api._game_procs[task_id] = {
            "proc": proc, "instance": instance, "version": version,
            "account": account, "started_at": proc.started_at,
        }


class RunningGamesTests(unittest.TestCase):
    def setUp(self):
        self.api = _make_api()

    def test_empty_by_default(self):
        self.assertEqual(self.api.list_running_games(), [])
        self.assertFalse(self.api.is_game_running())

    def test_lists_alive_processes_sorted(self):
        p1 = _FakeGameProc(pid=101)
        p1.started_at = time.time() - 300
        p2 = _FakeGameProc(pid=102)
        _register(self.api, "task-2", p2, version="1.21")
        _register(self.api, "task-1", p1, version="1.20.1", instance="生存服")
        rows = self.api.list_running_games()
        self.assertEqual([r["task_id"] for r in rows], ["task-1", "task-2"])
        first = rows[0]
        self.assertEqual(first["version"], "1.20.1")
        self.assertEqual(first["instance"], "生存服")
        self.assertEqual(first["account"], "Steve")
        self.assertEqual(first["pid"], 101)
        self.assertGreaterEqual(first["uptime"], 299)
        self.assertTrue(self.api.is_game_running())

    def test_dead_processes_filtered_out(self):
        _register(self.api, "task-1", _FakeGameProc(alive=False))
        self.assertEqual(self.api.list_running_games(), [])
        self.assertFalse(self.api.is_game_running())

    def test_kill_specific_game(self):
        p1 = _FakeGameProc(pid=101)
        p2 = _FakeGameProc(pid=102)
        _register(self.api, "task-1", p1)
        _register(self.api, "task-2", p2)
        self.assertEqual(self.api.kill_game("task-1"), 1)
        self.assertTrue(p1.killed)
        self.assertFalse(p2.killed)
        rows = self.api.list_running_games()
        self.assertEqual([r["task_id"] for r in rows], ["task-2"])

    def test_kill_all_games(self):
        p1 = _FakeGameProc(pid=101)
        p2 = _FakeGameProc(pid=102)
        _register(self.api, "task-1", p1)
        _register(self.api, "task-2", p2)
        self.assertEqual(self.api.kill_game(), 2)
        self.assertTrue(p1.killed and p2.killed)
        self.assertFalse(self.api.is_game_running())

    def test_kill_unknown_id_is_noop(self):
        self.assertEqual(self.api.kill_game("task-404"), 0)

    def test_cancel_task_kills_matching_game_not_latest(self):
        """取消第一个启动任务必须杀第一个游戏——以前只有最后一个能被杀。"""
        p1 = _FakeGameProc(pid=101)
        p2 = _FakeGameProc(pid=102)
        _register(self.api, "task-1", p1)
        _register(self.api, "task-2", p2)
        self.api._launch_task_id = "task-2"
        self.api._game_proc = p2
        self.api.cancel_task("task-1")
        self.assertTrue(p1.killed)
        self.assertFalse(p2.killed)

    def test_account_label(self):
        api = self.api
        self.assertEqual(api._account_label("Steve", ""), "Steve")
        self.assertEqual(api._account_label("离线模式", "Alex"), "Alex")
        self.assertEqual(api._account_label("", ""), "Player")


class QtBackendParityTests(unittest.TestCase):
    """Qt 门面必须有同名方法（不起 QApplication，只查类属性）。"""

    def test_methods_exist(self):
        from app.backend import BackendAPI as QtBackend
        for name in ("list_running_games", "kill_game", "is_game_running",
                     "_account_label"):
            self.assertTrue(callable(getattr(QtBackend, name, None)), name)


if __name__ == "__main__":
    unittest.main()
