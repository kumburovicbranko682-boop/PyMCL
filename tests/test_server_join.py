# -*- coding: utf-8 -*-
"""直连服务器：1.20+ 用 quickPlayMultiplayer，旧版用 --server/--port。"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mclauncher import launch_flow, version_settings
from mclauncher.instances import Instance

NEW_JSON = {
    "id": "1.20.1",
    "arguments": {"game": [
        "--username", "${auth_player_name}",
        {"rules": [{"action": "allow",
                    "features": {"is_quick_play_multiplayer": True}}],
         "value": ["--quickPlayMultiplayer", "${quickPlayMultiplayer}"]},
    ]},
}
OLD_JSON = {
    "id": "1.19.4",
    "arguments": {"game": ["--username", "${auth_player_name}"]},
}
FABRIC_JSON = {
    "id": "fabric-loader-0.15-1.20.1",
    "inheritsFrom": "1.20.1",
    "arguments": {"game": []},
}


class Sandbox(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        patcher = mock.patch(
            "mclauncher.instances.get_instance_path",
            side_effect=lambda name: self.root / "instances" / name)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def make_instance(self, name="join-test") -> Instance:
        inst = Instance(name)
        inst.create()
        return inst

    def add_version(self, inst: Instance, vjson: dict):
        vid = vjson["id"]
        vdir = inst.versions_dir() / vid
        vdir.mkdir(parents=True, exist_ok=True)
        (vdir / f"{vid}.json").write_text(json.dumps(vjson), "utf-8")


class TestSupportsQuickplay(Sandbox):
    def test_new_old_and_missing(self):
        inst = self.make_instance()
        self.add_version(inst, NEW_JSON)
        self.add_version(inst, OLD_JSON)
        self.assertTrue(launch_flow.supports_quickplay_multiplayer(inst, "1.20.1"))
        self.assertFalse(launch_flow.supports_quickplay_multiplayer(inst, "1.19.4"))
        self.assertFalse(launch_flow.supports_quickplay_multiplayer(inst, "ghost"))

    def test_inherits(self):
        inst = self.make_instance()
        self.add_version(inst, NEW_JSON)
        self.add_version(inst, FABRIC_JSON)
        self.assertTrue(launch_flow.supports_quickplay_multiplayer(
            inst, "fabric-loader-0.15-1.20.1"))

    def test_duck_typed_instance_without_version_json(self):
        class Stub:
            pass
        self.assertFalse(launch_flow.supports_quickplay_multiplayer(Stub(), "1.20.1"))


class TestServerJoinArgs(Sandbox):
    def test_legacy_version(self):
        inst = self.make_instance()
        self.add_version(inst, OLD_JSON)
        args = launch_flow.server_join_args(inst, "1.19.4", "play.example.com")
        self.assertEqual(args, ["--server", "play.example.com", "--port", "25565"])

    def test_new_version(self):
        inst = self.make_instance()
        self.add_version(inst, NEW_JSON)
        args = launch_flow.server_join_args(inst, "1.20.1", "play.example.com", "25566")
        self.assertEqual(args, ["--quickPlayMultiplayer", "play.example.com:25566"])

    def test_host_port_combined(self):
        inst = self.make_instance()
        self.add_version(inst, OLD_JSON)
        args = launch_flow.server_join_args(inst, "1.19.4", "play.example.com:25566")
        self.assertEqual(args, ["--server", "play.example.com", "--port", "25566"])

    def test_explicit_port_wins(self):
        inst = self.make_instance()
        self.add_version(inst, NEW_JSON)
        args = launch_flow.server_join_args(inst, "1.20.1", "h:1111", "2222")
        self.assertEqual(args, ["--quickPlayMultiplayer", "h:2222"])

    def test_new_version_no_port(self):
        inst = self.make_instance()
        self.add_version(inst, NEW_JSON)
        args = launch_flow.server_join_args(inst, "1.20.1", "play.example.com")
        self.assertEqual(args, ["--quickPlayMultiplayer", "play.example.com"])

    def test_empty_host(self):
        inst = self.make_instance()
        self.assertEqual(launch_flow.server_join_args(inst, "1.19.4", ""), [])


class TestPrepareRewrite(Sandbox):
    def test_settings_server_old_version(self):
        inst = self.make_instance()
        self.add_version(inst, OLD_JSON)
        version_settings.save(inst, "1.19.4", {"server": "play.example.com"})
        prep = launch_flow.prepare(inst, "1.19.4")
        extras = prep["extra_game_args"]
        self.assertIn("--server", extras)
        self.assertIn("play.example.com", extras)
        self.assertNotIn("--quickPlayMultiplayer", extras)

    def test_settings_server_new_version(self):
        inst = self.make_instance()
        self.add_version(inst, NEW_JSON)
        version_settings.save(inst, "1.20.1",
                              {"server": "play.example.com", "port": "25566"})
        prep = launch_flow.prepare(inst, "1.20.1")
        extras = prep["extra_game_args"]
        self.assertNotIn("--server", extras)
        idx = extras.index("--quickPlayMultiplayer")
        self.assertEqual(extras[idx + 1], "play.example.com:25566")

    def test_caller_extras_rewritten_on_new_version(self):
        """陶瓦加入 / 启动页服务器框传的 --server 在 1.20+ 被翻译。"""
        inst = self.make_instance()
        self.add_version(inst, NEW_JSON)
        prep = launch_flow.prepare(
            inst, "1.20.1",
            extra_game_args=["--server", "127.0.0.1", "--port", "40325"])
        extras = prep["extra_game_args"]
        self.assertNotIn("--server", extras)
        self.assertNotIn("--port", extras)
        idx = extras.index("--quickPlayMultiplayer")
        self.assertEqual(extras[idx + 1], "127.0.0.1:40325")

    def test_caller_extras_kept_on_old_version(self):
        inst = self.make_instance()
        self.add_version(inst, OLD_JSON)
        prep = launch_flow.prepare(
            inst, "1.19.4",
            extra_game_args=["--server", "127.0.0.1", "--port", "40325"])
        extras = prep["extra_game_args"]
        idx = extras.index("--server")
        self.assertEqual(extras[idx + 1], "127.0.0.1")
        idx = extras.index("--port")
        self.assertEqual(extras[idx + 1], "40325")

    def test_caller_wins_over_settings(self):
        inst = self.make_instance()
        self.add_version(inst, OLD_JSON)
        version_settings.save(inst, "1.19.4", {"server": "settings.example.com"})
        prep = launch_flow.prepare(
            inst, "1.19.4", extra_game_args=["--server", "caller.example.com"])
        extras = prep["extra_game_args"]
        self.assertIn("caller.example.com", extras)
        self.assertNotIn("settings.example.com", extras)

    def test_no_server_no_args(self):
        inst = self.make_instance()
        self.add_version(inst, OLD_JSON)
        prep = launch_flow.prepare(inst, "1.19.4", extra_game_args=["--demo"])
        extras = prep["extra_game_args"]
        self.assertNotIn("--server", extras)
        self.assertNotIn("--quickPlayMultiplayer", extras)
        self.assertIn("--demo", extras)


if __name__ == "__main__":
    unittest.main()
