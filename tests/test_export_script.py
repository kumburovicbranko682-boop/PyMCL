# -*- coding: utf-8 -*-
"""启动脚本导出：Windows .bat / POSIX .sh（引号、可执行位、后缀纠正）。"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mclauncher import version_ops as vops

CMD = ["/usr/bin/java", "-Xmx4G", "-Dfoo=a b", "net.minecraft.client.main.Main",
       "--username", "Player One"]


class TestExportSh(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_sh_content_and_exec_bit(self):
        dest = self.dir / "launch.sh"
        out = vops.export_launch_sh(dest, CMD, self.dir)
        text = Path(out).read_text("utf-8")
        self.assertTrue(text.startswith("#!/bin/sh\n"))
        self.assertIn("cd ", text)
        self.assertIn("exec ", text)
        self.assertIn("'-Dfoo=a b'", text)
        self.assertIn("'Player One'", text)
        if os.name != "nt":
            self.assertTrue(os.access(out, os.X_OK))

    def test_bat_suffix_corrected_to_sh(self):
        out = vops.export_launch_sh(self.dir / "launch.bat", CMD, self.dir)
        self.assertTrue(out.endswith(".sh"))

    def test_dispatch_on_current_os(self):
        out = vops.export_launch_script(self.dir / "a.bat", CMD, self.dir)
        if os.name == "nt":
            self.assertTrue(out.endswith(".bat"))
        else:
            self.assertTrue(out.endswith(".sh"))

    def test_bat_content(self):
        out = vops.export_launch_bat(self.dir / "b.bat", CMD, self.dir)
        text = Path(out).read_text("utf-8")
        self.assertIn("@echo off", text)
        self.assertIn('"-Dfoo=a b"', text)
        self.assertIn("pause", text)

    def test_sh_runs(self):
        """真的能跑：脚本 cd 到目录并 exec 命令。"""
        if os.name == "nt":
            self.skipTest("POSIX only")
        import subprocess
        marker = self.dir / "ran.txt"
        out = vops.export_launch_sh(
            self.dir / "run.sh", ["touch", str(marker)], self.dir)
        subprocess.run([out], check=True, timeout=10)
        self.assertTrue(marker.is_file())


if __name__ == "__main__":
    unittest.main()
