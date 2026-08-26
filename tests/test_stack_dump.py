# -*- coding: utf-8 -*-
"""游戏运行栈导出（HMCL「导出游戏运行栈」同款）：

1. 诊断工具发现：启动 Java 旁边的 jstack 优先，JRE 回退到已知 JDK；
2. 转储回退链：jstack -e -l → jstack -l（旧版不认 -e）→ jcmd Thread.print；
3. 门面 dump_game_stack：定位运行中的游戏、写盘、HMCL 同款文件名。
"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mclauncher import stack_dump
from mclauncher.stack_dump import (
    StackDumpError, dump_threads, export_dump, find_dump_tools,
)

DUMP_TEXT = (
    '2026-08-26 02:00:00\n'
    'Full thread dump OpenJDK 64-Bit Server VM (17.0.8+7 mixed mode):\n\n'
    '"main" #1 prio=5 os_prio=0 tid=0x0 nid=0x1 runnable\n'
    '   java.lang.Thread.State: RUNNABLE\n'
)

POSIX_ONLY = unittest.skipIf(os.name == "nt", "shell 脚本假工具仅限 POSIX")


def _make_java_home(root: Path, name: str, tools: dict[str, str]) -> Path:
    """建一个假的 Java 目录：bin/java + 指定的诊断工具脚本。返回 java 路径。"""
    bin_dir = root / name / "bin"
    bin_dir.mkdir(parents=True)
    java = bin_dir / "java"
    java.write_text("#!/bin/sh\nexit 0\n", "utf-8")
    java.chmod(0o755)
    for tool, script in tools.items():
        p = bin_dir / tool
        p.write_text("#!/bin/sh\n" + script, "utf-8")
        p.chmod(0o755)
    return java


class ToolDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        self.addCleanup(self._td.cleanup)

    def test_launch_java_tools_come_first(self):
        a = _make_java_home(self.root, "jdk-a", {"jstack": "", "jcmd": ""})
        b = _make_java_home(self.root, "jdk-b", {"jstack": ""})
        tools = find_dump_tools(java_exe=str(a), javas=[{"exe": str(b)}])
        self.assertEqual(
            [(Path(p).parent.parent.name, kind) for p, kind in tools],
            [("jdk-a", "jstack"), ("jdk-a", "jcmd"), ("jdk-b", "jstack")])

    def test_jre_without_tools_falls_back_to_known_jdk(self):
        jre = _make_java_home(self.root, "jre", {})
        jdk = _make_java_home(self.root, "jdk", {"jcmd": ""})
        tools = find_dump_tools(java_exe=str(jre), javas=[{"exe": str(jdk)}])
        self.assertEqual([kind for _p, kind in tools], ["jcmd"])

    def test_duplicate_java_entries_deduped(self):
        jdk = _make_java_home(self.root, "jdk", {"jstack": ""})
        tools = find_dump_tools(java_exe=str(jdk),
                                javas=[{"exe": str(jdk)}, {"exe": str(jdk)}])
        self.assertEqual(len(tools), 1)

    def test_no_tools_anywhere(self):
        jre = _make_java_home(self.root, "jre", {})
        self.assertEqual(find_dump_tools(java_exe=str(jre), javas=[]), [])


@POSIX_ONLY
class DumpThreadsTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        self.addCleanup(self._td.cleanup)

    def test_modern_jstack(self):
        java = _make_java_home(self.root, "jdk", {"jstack": ""})
        (java.parent / "jstack").write_text(
            '#!/bin/sh\n[ "$1" = "-e" ] && [ "$2" = "-l" ] || exit 9\n'
            'cat <<"EOF"\n' + DUMP_TEXT + 'EOF\n', "utf-8")
        out = dump_threads(1234, java_exe=str(java), javas=[])
        self.assertIn("Full thread dump", out)
        self.assertIn('"main"', out)

    def test_old_jstack_retries_without_e_flag(self):
        java = _make_java_home(self.root, "jdk", {"jstack": ""})
        (java.parent / "jstack").write_text(
            '#!/bin/sh\n'
            'if [ "$1" = "-e" ]; then echo "Unknown option: -e" >&2; exit 1; fi\n'
            '[ "$1" = "-l" ] || exit 9\n'
            'cat <<"EOF"\n' + DUMP_TEXT + 'EOF\n', "utf-8")
        out = dump_threads(1234, java_exe=str(java), javas=[])
        self.assertIn("Full thread dump", out)

    def test_jcmd_fallback_when_no_jstack(self):
        java = _make_java_home(self.root, "jdk", {"jcmd": ""})
        (java.parent / "jcmd").write_text(
            '#!/bin/sh\n'
            '[ "$2" = "Thread.print" ] && [ "$3" = "-l" ] || exit 9\n'
            'echo "$1:"\n'
            'cat <<"EOF"\n' + DUMP_TEXT + 'EOF\n', "utf-8")
        out = dump_threads(1234, java_exe=str(java), javas=[])
        self.assertIn("Full thread dump", out)

    def test_success_exit_without_dump_marker_is_a_failure(self):
        java = _make_java_home(self.root, "jdk",
                               {"jstack": 'echo "hello, not a dump"\n'})
        with self.assertRaises(StackDumpError):
            dump_threads(1234, java_exe=str(java), javas=[])

    def test_all_tools_fail_reports_reasons(self):
        java = _make_java_home(self.root, "jdk", {
            "jstack": 'echo "Unable to open socket file" >&2; exit 1\n'})
        with self.assertRaises(StackDumpError) as ctx:
            dump_threads(1234, java_exe=str(java), javas=[])
        self.assertIn("Unable to open socket file", str(ctx.exception))

    def test_no_tool_available_mentions_jdk(self):
        jre = _make_java_home(self.root, "jre", {})
        with self.assertRaises(StackDumpError) as ctx:
            dump_threads(1234, java_exe=str(jre), javas=[])
        self.assertIn("JDK", str(ctx.exception))

    def test_invalid_pid(self):
        with self.assertRaises(StackDumpError):
            dump_threads(0, javas=[])


@POSIX_ONLY
class ExportDumpTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        self.addCleanup(self._td.cleanup)
        self.java = _make_java_home(self.root, "jdk", {"jstack": ""})
        (self.java.parent / "jstack").write_text(
            '#!/bin/sh\ncat <<"EOF"\n' + DUMP_TEXT + 'EOF\n', "utf-8")

    def test_default_path_uses_hmcl_naming(self):
        from mclauncher import utils
        with patch.object(utils, "ROOT", self.root):
            path = export_dump(1234, java_exe=str(self.java), javas=[])
        p = Path(path)
        self.assertTrue(p.name.startswith("minecraft-exported-jstack-dump-"))
        self.assertTrue(p.name.endswith(".log"))
        self.assertEqual(p.parent, self.root)
        self.assertIn("Full thread dump", p.read_text("utf-8"))

    def test_dest_override(self):
        dest = self.root / "out" / "dump.txt"
        path = export_dump(1234, java_exe=str(self.java), javas=[],
                           dest=str(dest))
        self.assertEqual(Path(path), dest.resolve())
        self.assertTrue(dest.is_file())


class _FakePopen:
    def __init__(self, pid):
        self.pid = pid


class _FakeGameProcess:
    def __init__(self, pid=4321, running=True):
        self.proc = _FakePopen(pid)
        self._running = running

    def poll(self):
        return None if self._running else 0


class FacadeDumpTests(unittest.TestCase):
    """bridge.api.dump_game_stack：选进程 → 传 pid/java → 返回文件路径。"""

    def setUp(self):
        from mclauncher import utils
        from mclauncher.config import CONFIG, DEFAULT_CONFIG

        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        self.addCleanup(self._td.cleanup)
        data = {k: (list(v) if isinstance(v, list) else dict(v) if isinstance(v, dict) else v)
                for k, v in DEFAULT_CONFIG.items()}
        for p in (patch.object(utils, "ROOT", self.root),
                  patch.object(CONFIG, "data", data),
                  patch.object(CONFIG, "save", lambda: None)):
            p.start()
            self.addCleanup(p.stop)

        from bridge.api import BackendAPI

        class _Bus:
            def emit(self, *a, **k):
                pass

        self.api = BackendAPI(_Bus())

    def test_no_running_game_raises(self):
        from mclauncher.launcher import LaunchError
        with self.assertRaises(LaunchError):
            self.api.dump_game_stack()

    def test_exited_game_raises(self):
        from mclauncher.launcher import LaunchError
        self.api._game_procs["t1"] = {
            "proc": _FakeGameProcess(running=False), "java": "/x/java"}
        with self.assertRaises(LaunchError):
            self.api.dump_game_stack("t1")

    def test_dump_latest_running_game_writes_file(self):
        self.api._game_procs["t1"] = {
            "proc": _FakeGameProcess(pid=1111), "java": "/jdk-old/bin/java"}
        self.api._game_procs["t2"] = {
            "proc": _FakeGameProcess(pid=2222), "java": "/jdk-new/bin/java"}
        seen = {}

        def fake_dump(pid, java_exe=None, javas=None, timeout=30):
            seen["pid"], seen["java"] = pid, java_exe
            return DUMP_TEXT

        with patch.object(stack_dump, "dump_threads", fake_dump):
            path = self.api.dump_game_stack()
        # task_id 留空 → 最近启动（最后插入）的游戏
        self.assertEqual(seen["pid"], 2222)
        self.assertEqual(seen["java"], "/jdk-new/bin/java")
        p = Path(path)
        self.assertTrue(p.name.startswith("minecraft-exported-jstack-dump-"))
        self.assertEqual(p.parent, self.root)
        self.assertEqual(p.read_text("utf-8"), DUMP_TEXT)

    def test_dump_specific_task_id(self):
        self.api._game_procs["t1"] = {
            "proc": _FakeGameProcess(pid=1111), "java": "/jdk-a/bin/java"}
        self.api._game_procs["t2"] = {
            "proc": _FakeGameProcess(pid=2222), "java": "/jdk-b/bin/java"}
        seen = {}

        def fake_dump(pid, java_exe=None, javas=None, timeout=30):
            seen["pid"], seen["java"] = pid, java_exe
            return DUMP_TEXT

        with patch.object(stack_dump, "dump_threads", fake_dump):
            self.api.dump_game_stack("t1", dest=str(self.root / "d.log"))
        self.assertEqual(seen["pid"], 1111)
        self.assertEqual(seen["java"], "/jdk-a/bin/java")
        self.assertTrue((self.root / "d.log").is_file())

    def test_tool_error_becomes_launch_error(self):
        from mclauncher.launcher import LaunchError
        self.api._game_procs["t1"] = {"proc": _FakeGameProcess(), "java": ""}

        def boom(pid, java_exe=None, javas=None, timeout=30):
            raise StackDumpError("没有找到 jstack / jcmd")

        with patch.object(stack_dump, "dump_threads", boom):
            with self.assertRaises(LaunchError) as ctx:
                self.api.dump_game_stack()
        self.assertIn("jstack", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
