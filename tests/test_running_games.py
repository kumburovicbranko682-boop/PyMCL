# -*- coding: utf-8 -*-
"""多开进程管理测试（对标 PCL2 同时运行多实例并结束指定游戏）。

backend / bridge.api 共用同一套注册表逻辑，这里用轻量 fake self
调未绑定方法，覆盖两侧而不用构造完整后端（避免 Qt / 磁盘副作用）。
"""
import sys
import threading
import time
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class FakeProc:
    def __init__(self, pid=1234, alive=True):
        self.pid = pid
        self._alive = alive
        self.killed = False

    def poll(self):
        return None if self._alive else 0

    def kill(self):
        self.killed = True
        self._alive = False


def _fake_self(entries=(), latest=None, launch_task_id=None):
    o = types.SimpleNamespace()
    o._game_lock = threading.Lock()
    o._lock = threading.Lock()
    o._game_procs = list(entries)
    o._game_proc = latest
    o._launch_task_id = launch_task_id
    o._workers = {}
    return o


def _entry(proc, task_id="task-1", instance="default", version="1.20.1",
           account="Steve", started=None):
    return {"proc": proc, "task_id": task_id, "instance": instance,
            "version": version, "account": account,
            "started_at": started if started is not None else time.time() - 65}


def _backends():
    """两个门面类；导不进来的跳过（比如无 PySide6 环境跳过 app.backend）。"""
    out = []
    try:
        from bridge.api import BackendAPI as BridgeAPI
        out.append(("bridge", BridgeAPI))
    except Exception:
        pass
    try:
        from app.backend import BackendAPI as QtAPI
        out.append(("app", QtAPI))
    except Exception:
        pass
    return out


class RunningGamesTest(unittest.TestCase):
    def setUp(self):
        self.apis = _backends()
        if not self.apis:
            self.skipTest("backend/bridge 均不可导入")

    def test_list_fields_and_prunes_dead(self):
        for name, api in self.apis:
            with self.subTest(api=name):
                alive = FakeProc(pid=111)
                dead = FakeProc(pid=222, alive=False)
                o = _fake_self([_entry(alive, task_id="t1"),
                                _entry(dead, task_id="t2", version="1.19")])
                rows = api.get_running_games(o)
                self.assertEqual(len(rows), 1)
                r = rows[0]
                self.assertEqual(r["pid"], 111)
                self.assertEqual(r["task_id"], "t1")
                self.assertEqual(r["instance"], "default")
                self.assertEqual(r["version"], "1.20.1")
                self.assertEqual(r["account"], "Steve")
                self.assertGreaterEqual(r["uptime"], 60)

    def test_stop_game_by_pid(self):
        for name, api in self.apis:
            with self.subTest(api=name):
                a, b = FakeProc(pid=11), FakeProc(pid=22)
                o = _fake_self([_entry(a), _entry(b)])
                n = api.stop_game(o, pid=22)
                self.assertEqual(n, 1)
                self.assertFalse(a.killed)
                self.assertTrue(b.killed)

    def test_stop_game_all(self):
        for name, api in self.apis:
            with self.subTest(api=name):
                a, b = FakeProc(pid=11), FakeProc(pid=22)
                dead = FakeProc(pid=33, alive=False)
                o = _fake_self([_entry(a), _entry(b), _entry(dead)])
                n = api.stop_game(o)
                self.assertEqual(n, 2)   # 已退出的不算
                self.assertTrue(a.killed and b.killed)
                self.assertFalse(dead.killed)

    def test_is_game_running_any_alive(self):
        for name, api in self.apis:
            with self.subTest(api=name):
                o = _fake_self([_entry(FakeProc(alive=False))])
                self.assertFalse(api.is_game_running(o))
                o2 = _fake_self([_entry(FakeProc(alive=False)),
                                 _entry(FakeProc(alive=True))])
                self.assertTrue(api.is_game_running(o2))
                # 注册表为空时回退看 _game_proc（兼容旧路径）
                o3 = _fake_self([], latest=FakeProc(alive=True))
                self.assertTrue(api.is_game_running(o3))

    def test_cancel_task_kills_matching_game(self):
        for name, api in self.apis:
            with self.subTest(api=name):
                a = FakeProc(pid=11)
                b = FakeProc(pid=22)
                o = _fake_self([_entry(a, task_id="t1"), _entry(b, task_id="t2")],
                               launch_task_id="t2")
                api.cancel_task(o, "t1")
                self.assertTrue(a.killed)
                self.assertFalse(b.killed)

    def test_cancel_task_legacy_fallback(self):
        # 注册表里没有对应任务时，退回旧行为：任务是最近一次启动就杀 _game_proc
        for name, api in self.apis:
            with self.subTest(api=name):
                latest = FakeProc(pid=99)
                o = _fake_self([], latest=latest, launch_task_id="t9")
                api.cancel_task(o, "t9")
                self.assertTrue(latest.killed)
                other = FakeProc(pid=98)
                o2 = _fake_self([], latest=other, launch_task_id="t9")
                api.cancel_task(o2, "t-unrelated")
                self.assertFalse(other.killed)


class GameProcessPidTest(unittest.TestCase):
    def test_pid_property(self):
        from mclauncher.launcher import GameProcess
        gp = GameProcess.__new__(GameProcess)   # 不真启进程
        gp.proc = types.SimpleNamespace(pid=4321)
        self.assertEqual(gp.pid, 4321)


if __name__ == "__main__":
    unittest.main()
