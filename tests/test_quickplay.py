# -*- coding: utf-8 -*-
"""服务器直连：1.20+ Quick Play 与老版本 --server 的版本感知处理。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mclauncher import launcher


class ExtractServerTests(unittest.TestCase):
    def test_extracts_server_and_port(self):
        out, host, port = launcher._extract_server(
            ["--demo", "--server", "play.example.com", "--port", "25566", "--x"])
        self.assertEqual(out, ["--demo", "--x"])
        self.assertEqual(host, "play.example.com")
        self.assertEqual(port, 25566)

    def test_no_server(self):
        out, host, port = launcher._extract_server(["--demo"])
        self.assertEqual(out, ["--demo"])
        self.assertEqual(host, "")
        self.assertEqual(port, 0)

    def test_bad_port_ignored(self):
        _out, host, port = launcher._extract_server(["--server", "a", "--port", "xx"])
        self.assertEqual(host, "a")
        self.assertEqual(port, 0)

    def test_empty(self):
        self.assertEqual(launcher._extract_server([]), ([], "", 0))


class ExtractWorldTests(unittest.TestCase):
    def test_extracts_world(self):
        out, world = launcher._extract_world(
            ["--demo", "--quickPlaySingleplayer", "My World", "--x"])
        self.assertEqual(out, ["--demo", "--x"])
        self.assertEqual(world, "My World")

    def test_no_world(self):
        self.assertEqual(launcher._extract_world(["--demo"]), (["--demo"], ""))
        self.assertEqual(launcher._extract_world([]), ([], ""))


class ServerGameArgsTests(unittest.TestCase):
    def test_no_server_no_args(self):
        self.assertEqual(launcher._server_game_args(["--username", "x"], [], "", 0), [])

    def test_quickplay_present_skips_legacy(self):
        args = ["--quickPlayMultiplayer", "h:25565"]
        self.assertEqual(launcher._server_game_args(args, [], "h", 25565), [])

    def test_legacy_fallback(self):
        self.assertEqual(
            launcher._server_game_args(["--username", "x"], [], "h", 0),
            ["--server", "h", "--port", "25565"])
        self.assertEqual(
            launcher._server_game_args([], [], "h", 25566),
            ["--server", "h", "--port", "25566"])


class _FakeInstance:
    def __init__(self, root: Path):
        self.path = root
        self.name = "t"

    def version_json(self, vid):
        p = self.path / "versions" / vid / f"{vid}.json"
        if not p.is_file():
            return None
        return json.loads(p.read_text(encoding="utf-8"))

    def versions_dir(self):
        return self.path / "versions"

    def assets_dir(self):
        return self.path / "assets"

    def libraries_dir(self):
        return self.path / "libraries"

    def natives_dir(self, version_id, _resolved=None):
        return self.path / "versions" / version_id / "natives"


MODERN_JSON = {
    "id": "1.20.1",
    "mainClass": "net.minecraft.client.main.Main",
    "assetIndex": {"id": "5"},
    "arguments": {
        "jvm": [],
        "game": [
            "--username", "${auth_player_name}",
            {
                "rules": [{"action": "allow",
                           "features": {"is_quick_play_multiplayer": True}}],
                "value": ["--quickPlayMultiplayer", "${quickPlayMultiplayer}"],
            },
            {
                "rules": [{"action": "allow",
                           "features": {"is_quick_play_singleplayer": True}}],
                "value": ["--quickPlaySingleplayer", "${quickPlaySingleplayer}"],
            },
        ],
    },
}

MID_JSON = {
    # 1.13–1.19 风格：新参数格式但没有 Quick Play 条目
    "id": "1.18.2",
    "mainClass": "net.minecraft.client.main.Main",
    "assetIndex": {"id": "1.18"},
    "arguments": {"jvm": [], "game": ["--username", "${auth_player_name}"]},
}

LEGACY_JSON = {
    "id": "1.12.2",
    "mainClass": "net.minecraft.client.main.Main",
    "assetIndex": {"id": "1.12"},
    "minecraftArguments": "--username ${auth_player_name} --version ${version_name}",
}


def _build_cmd(vjson, **kwargs):
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        vid = vjson["id"]
        vdir = root / "versions" / vid
        vdir.mkdir(parents=True)
        (vdir / f"{vid}.json").write_text(
            json.dumps(vjson), encoding="utf-8")
        (vdir / f"{vid}.jar").write_bytes(b"PK\x03\x04fakejar")
        inst = _FakeInstance(root)
        props = {"name": "Steve", "uuid": "a" * 32, "token": "0",
                 "user_type": "legacy", "xuid": ""}
        with patch.object(launcher.java_mod, "java_usable_for", return_value=True), \
             patch.object(launcher.java_mod, "get_java_major", return_value=17):
            cmd, _n, _v, _g = launcher.build_launch_command(
                inst, vid, props, sys.executable, memory_mb=1024, **kwargs)
        return cmd


class BuildCommandServerTests(unittest.TestCase):
    def _build(self, vjson, **kwargs):
        return _build_cmd(vjson, **kwargs)

    def test_modern_uses_quickplay(self):
        cmd = self._build(MODERN_JSON, server="play.example.com", server_port=25566)
        self.assertIn("--quickPlayMultiplayer", cmd)
        idx = cmd.index("--quickPlayMultiplayer")
        self.assertEqual(cmd[idx + 1], "play.example.com:25566")
        self.assertNotIn("--server", cmd)
        self.assertNotIn("--port", cmd)

    def test_modern_extracts_legacy_extras(self):
        # 调用方仍按旧习惯塞 --server：1.20+ 必须转成 Quick Play
        cmd = self._build(MODERN_JSON,
                          extra_game_args=["--server", "mc.hypixel.net", "--port", "25565"])
        self.assertIn("--quickPlayMultiplayer", cmd)
        idx = cmd.index("--quickPlayMultiplayer")
        self.assertEqual(cmd[idx + 1], "mc.hypixel.net:25565")
        self.assertNotIn("--server", cmd)

    def test_modern_without_server_has_neither(self):
        cmd = self._build(MODERN_JSON)
        self.assertNotIn("--quickPlayMultiplayer", cmd)
        self.assertNotIn("--server", cmd)

    def test_mid_version_falls_back_to_server(self):
        cmd = self._build(MID_JSON, server="play.example.com")
        self.assertNotIn("--quickPlayMultiplayer", cmd)
        self.assertIn("--server", cmd)
        self.assertEqual(cmd[cmd.index("--server") + 1], "play.example.com")
        self.assertEqual(cmd[cmd.index("--port") + 1], "25565")

    def test_legacy_minecraft_arguments_falls_back(self):
        cmd = self._build(LEGACY_JSON,
                          extra_game_args=["--server", "old.example.com", "--port", "25567"])
        self.assertIn("--server", cmd)
        self.assertEqual(cmd[cmd.index("--server") + 1], "old.example.com")
        self.assertEqual(cmd[cmd.index("--port") + 1], "25567")

    def test_default_port_defaults_to_25565(self):
        cmd = self._build(MODERN_JSON, server="h.example.com")
        idx = cmd.index("--quickPlayMultiplayer")
        self.assertEqual(cmd[idx + 1], "h.example.com:25565")

    def test_other_extras_preserved(self):
        cmd = self._build(MODERN_JSON,
                          extra_game_args=["--demo", "--server", "s.example.com"])
        self.assertIn("--demo", cmd)
        self.assertIn("--quickPlayMultiplayer", cmd)


class BuildCommandWorldTests(unittest.TestCase):
    """快速进入单人存档（--quickPlaySingleplayer）。"""

    def _build(self, vjson, **kwargs):
        return _build_cmd(vjson, **kwargs)

    def test_modern_joins_world(self):
        cmd = self._build(MODERN_JSON,
                          extra_game_args=["--quickPlaySingleplayer", "My World"])
        idx = cmd.index("--quickPlaySingleplayer")
        self.assertEqual(cmd[idx + 1], "My World")
        self.assertNotIn("--quickPlayMultiplayer", cmd)

    def test_modern_without_world_has_no_arg(self):
        cmd = self._build(MODERN_JSON)
        self.assertNotIn("--quickPlaySingleplayer", cmd)

    def test_mid_version_raises_readable_error(self):
        with self.assertRaises(launcher.LaunchError) as ctx:
            self._build(MID_JSON, extra_game_args=["--quickPlaySingleplayer", "W"])
        self.assertIn("1.20", str(ctx.exception))

    def test_legacy_version_raises_readable_error(self):
        with self.assertRaises(launcher.LaunchError):
            self._build(LEGACY_JSON, extra_game_args=["--quickPlaySingleplayer", "W"])


if __name__ == "__main__":
    unittest.main()
