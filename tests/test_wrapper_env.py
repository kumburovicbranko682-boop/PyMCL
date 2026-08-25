# -*- coding: utf-8 -*-
"""包裹命令与环境变量：解析、prepare 输出、真实进程注入、bat 导出。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mclauncher import launch_flow


class ParseEnvVarsTests(unittest.TestCase):
    def test_basic_lines(self):
        env = launch_flow.parse_env_vars("FOO=1\nBAR=hello world")
        self.assertEqual(env, {"FOO": "1", "BAR": "hello world"})

    def test_skips_blank_and_comments(self):
        env = launch_flow.parse_env_vars("\n# 注释\n  \nKEY=v\n#X=1")
        self.assertEqual(env, {"KEY": "v"})

    def test_value_may_contain_equals(self):
        env = launch_flow.parse_env_vars("JAVA_OPTS=-Da=b -Dc=d")
        self.assertEqual(env["JAVA_OPTS"], "-Da=b -Dc=d")

    def test_line_without_equals_ignored(self):
        env = launch_flow.parse_env_vars("NOTAVAR\nOK=1")
        self.assertEqual(env, {"OK": "1"})

    def test_whitespace_trimmed(self):
        env = launch_flow.parse_env_vars("  KEY  =  value  ")
        self.assertEqual(env, {"KEY": "value"})

    def test_empty_input(self):
        self.assertEqual(launch_flow.parse_env_vars(""), {})
        self.assertEqual(launch_flow.parse_env_vars(None), {})


class WrapCommandTests(unittest.TestCase):
    def test_no_wrapper_returns_copy(self):
        cmd = ["java", "-jar", "x.jar"]
        out = launch_flow.wrap_command(cmd, [])
        self.assertEqual(out, cmd)
        self.assertIsNot(out, cmd)

    def test_wrapper_prepended(self):
        out = launch_flow.wrap_command(["java", "Main"], ["mangohud", "--no-display"])
        self.assertEqual(out, ["mangohud", "--no-display", "java", "Main"])

    def test_blank_tokens_filtered(self):
        out = launch_flow.wrap_command(["java"], ["", "  ", "optirun"])
        self.assertEqual(out, ["optirun", "java"])


class BuildEnvTests(unittest.TestCase):
    def test_none_when_empty(self):
        self.assertIsNone(launch_flow.build_env({}))
        self.assertIsNone(launch_flow.build_env(None))

    def test_merges_with_process_env(self):
        env = launch_flow.build_env({"PYMCL_X": "1"})
        self.assertEqual(env["PYMCL_X"], "1")
        # 原有环境仍在（PATH 几乎总是存在）
        self.assertIn("PATH", env)

    def test_override_wins(self):
        with mock.patch.dict(os.environ, {"PYMCL_Y": "old"}):
            env = launch_flow.build_env({"PYMCL_Y": "new"})
        self.assertEqual(env["PYMCL_Y"], "new")


class PrepareWrapperEnvTests(unittest.TestCase):
    def test_prepare_exposes_wrapper_and_env(self):
        class FakeInstance:
            name = "t"

        with mock.patch("mclauncher.launch_flow.version_settings") as vs, \
             mock.patch("mclauncher.launch_flow.global_mods") as gm:
            vs.load.return_value = {
                "memory_mb": 2048,
                "wrapper": 'mangohud --dlsym',
                "env_vars": "MESA_GL_VERSION_OVERRIDE=4.6\n# c\n__GL_THREADED_OPTIMIZATIONS=1",
            }
            vs.apply_isolation.return_value = Path("/tmp")
            vs.FULLSCREEN_MODES = ()
            gm.apply.return_value = 0
            prep = launch_flow.prepare(FakeInstance(), "1.20.1", memory_mb=4096)
        self.assertEqual(prep["wrapper"], ["mangohud", "--dlsym"])
        self.assertEqual(prep["env"], {
            "MESA_GL_VERSION_OVERRIDE": "4.6",
            "__GL_THREADED_OPTIMIZATIONS": "1",
        })

    def test_prepare_defaults_empty(self):
        class FakeInstance:
            name = "t"

        with mock.patch("mclauncher.launch_flow.version_settings") as vs, \
             mock.patch("mclauncher.launch_flow.global_mods") as gm:
            vs.load.return_value = {"memory_mb": 2048}
            vs.apply_isolation.return_value = Path("/tmp")
            vs.FULLSCREEN_MODES = ()
            gm.apply.return_value = 0
            prep = launch_flow.prepare(FakeInstance(), "1.20.1", memory_mb=4096)
        self.assertEqual(prep["wrapper"], [])
        self.assertEqual(prep["env"], {})


@unittest.skipUnless(os.name == "posix", "真实进程注入测试只在 POSIX 跑")
class GameProcessInjectionTests(unittest.TestCase):
    def test_env_injected_into_real_process(self):
        from mclauncher.launcher import GameProcess
        env = launch_flow.build_env({"PYMCL_INJECT": "works"})
        lines = []
        proc = GameProcess(["/bin/sh", "-c", 'echo "VAL=$PYMCL_INJECT"'],
                           cwd="/tmp", on_line=lines.append, env=env)
        code = proc.wait()
        self.assertEqual(code, 0)
        self.assertIn("VAL=works", "\n".join(proc.last_lines()))

    def test_wrapper_actually_wraps(self):
        from mclauncher.launcher import GameProcess
        cmd = launch_flow.wrap_command(["echo", "wrapped-run"], ["/usr/bin/env"])
        self.assertEqual(cmd[0], "/usr/bin/env")
        proc = GameProcess(cmd, cwd="/tmp", on_line=None)
        code = proc.wait()
        self.assertEqual(code, 0)
        self.assertIn("wrapped-run", "\n".join(proc.last_lines()))


class ExportBatEnvTests(unittest.TestCase):
    def test_env_written_as_set_lines(self):
        from mclauncher import version_ops as vops
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "launch.bat"
            vops.export_launch_bat(dest, ["java", "-jar", "a b.jar"], td,
                                   env={"FOO": "1", "BAR": "x=y"})
            text = dest.read_text(encoding="utf-8")
        self.assertIn('set "FOO=1"', text)
        self.assertIn('set "BAR=x=y"', text)
        # set 行必须在启动命令之前
        self.assertLess(text.index('set "FOO=1"'), text.index("java"))

    def test_no_env_no_set_lines(self):
        from mclauncher import version_ops as vops
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "launch.bat"
            vops.export_launch_bat(dest, ["java"], td)
            text = dest.read_text(encoding="utf-8")
        self.assertNotIn('set "', text)


if __name__ == "__main__":
    unittest.main()
