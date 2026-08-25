# -*- coding: utf-8 -*-
"""包装器命令（wrapper）：设置 round-trip、prepare 兜底、启动命令前挂。"""
from __future__ import annotations

import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from mclauncher import launch_flow, version_settings
from mclauncher.config import CONFIG
from mclauncher.instances import Instance


def make_jar(marker: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("marker.txt", marker)
    return buf.getvalue()


class InstanceSandbox(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        patcher = mock.patch(
            "mclauncher.instances.get_instance_path",
            side_effect=lambda name: self.root / "instances" / name)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def make_instance(self, name="wrapper-test") -> Instance:
        inst = Instance(name)
        inst.create()
        return inst


class TestWrapperSetting(InstanceSandbox):
    def test_defaults_contain_wrapper(self):
        self.assertIn("wrapper", version_settings.DEFAULTS)
        self.assertEqual(version_settings.DEFAULTS["wrapper"], "")

    def test_roundtrip(self):
        inst = self.make_instance()
        version_settings.save(inst, "1.20.1", {"wrapper": "gamemoderun"})
        data = version_settings.load(inst, "1.20.1")
        self.assertEqual(data["wrapper"], "gamemoderun")

    def test_partial_save_keeps_wrapper(self):
        """其他前端只提交部分键时不能把 wrapper 抹掉。"""
        inst = self.make_instance()
        version_settings.save(inst, "1.20.1", {"wrapper": "optirun"})
        version_settings.save(inst, "1.20.1", {"memory_mb": 2048})
        data = version_settings.load(inst, "1.20.1")
        self.assertEqual(data["wrapper"], "optirun")
        self.assertEqual(data["memory_mb"], 2048)


class TestPrepareWrapper(InstanceSandbox):
    def test_version_setting_wins(self):
        inst = self.make_instance()
        version_settings.save(inst, "1.20.1", {"wrapper": "gamemoderun --flag"})
        with mock.patch.dict(CONFIG.data, {"wrapper_command": "optirun"}):
            prep = launch_flow.prepare(inst, "1.20.1")
        self.assertEqual(prep["wrapper"], ["gamemoderun", "--flag"])

    def test_global_fallback(self):
        inst = self.make_instance()
        with mock.patch.dict(CONFIG.data, {"wrapper_command": "mangohud"}):
            prep = launch_flow.prepare(inst, "1.20.1")
        self.assertEqual(prep["wrapper"], ["mangohud"])

    def test_empty_by_default(self):
        inst = self.make_instance()
        with mock.patch.dict(CONFIG.data, {"wrapper_command": ""}):
            prep = launch_flow.prepare(inst, "1.20.1")
        self.assertEqual(prep["wrapper"], [])

    def test_quoted_wrapper_splits_like_shell(self):
        inst = self.make_instance()
        version_settings.save(
            inst, "1.20.1", {"wrapper": '"/opt/my tools/wrap" -v'})
        prep = launch_flow.prepare(inst, "1.20.1")
        self.assertEqual(prep["wrapper"], ["/opt/my tools/wrap", "-v"])


class TestBuildCommandWrapper(InstanceSandbox):
    VID = "1.7.10-test"

    def _install_fake_version(self, inst: Instance):
        vdir = inst.versions_dir() / self.VID
        vdir.mkdir(parents=True, exist_ok=True)
        vjson = {
            "id": self.VID,
            "mainClass": "net.minecraft.client.main.Main",
            "minecraftArguments": "--username ${auth_player_name}",
            "type": "release",
            "libraries": [],
        }
        (vdir / f"{self.VID}.json").write_text(json.dumps(vjson), encoding="utf-8")
        (vdir / f"{self.VID}.jar").write_bytes(make_jar("client"))

    def _build(self, wrapper):
        from mclauncher import launcher
        inst = self.make_instance()
        self._install_fake_version(inst)
        with mock.patch("mclauncher.java.java_usable_for", return_value=True), \
             mock.patch("mclauncher.java.get_java_major", return_value=17):
            return launcher.build_launch_command(
                inst, self.VID, {"name": "Tester"}, "/usr/bin/java",
                memory_mb=1024, wrapper=wrapper)

    def test_wrapper_list_prepended(self):
        cmd, _n, _v, _g = self._build(["gamemoderun", "--flag"])
        self.assertEqual(cmd[:2], ["gamemoderun", "--flag"])
        self.assertEqual(cmd[2], "/usr/bin/java")
        self.assertIn("net.minecraft.client.main.Main", cmd)

    def test_wrapper_string_split(self):
        cmd, _n, _v, _g = self._build('"/opt/my tools/wrap" -v')
        self.assertEqual(cmd[:2], ["/opt/my tools/wrap", "-v"])
        self.assertEqual(cmd[2], "/usr/bin/java")

    def test_no_wrapper_java_first(self):
        cmd, _n, _v, _g = self._build(None)
        self.assertEqual(cmd[0], "/usr/bin/java")

    def test_empty_wrapper_java_first(self):
        cmd, _n, _v, _g = self._build([])
        self.assertEqual(cmd[0], "/usr/bin/java")


if __name__ == "__main__":
    unittest.main()
