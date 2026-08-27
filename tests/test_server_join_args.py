# -*- coding: utf-8 -*-
"""进服参数换算测试（对标 PCL2「直接进入服务器」/ HMCL 服务器地址）。

23w14a (1.20) 起原版移除了 --server/--port，只认
--quickPlayMultiplayer host:port。启动页直连、版本设置、陶瓦联机
统一传 --server，由 launcher.adapt_server_args 按版本 JSON 换算。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mclauncher.launcher import adapt_server_args, _supports_quick_play  # noqa: E402
from mclauncher import launch_flow  # noqa: E402

# 1.20+ 官方 JSON 里 Quick Play 参数藏在 feature 规则后面
MODERN = {
    "arguments": {
        "game": [
            "--username", "${auth_player_name}",
            {
                "rules": [{"action": "allow",
                           "features": {"is_quick_play_multiplayer": True}}],
                "value": ["--quickPlayMultiplayer", "${quickPlayMultiplayer}"],
            },
        ]
    }
}
OLD = {"arguments": {"game": ["--username", "${auth_player_name}"]}}
LEGACY = {"minecraftArguments": "--username ${auth_player_name}"}


class QuickPlayDetectTest(unittest.TestCase):
    def test_modern_json_detected(self):
        self.assertTrue(_supports_quick_play(MODERN))

    def test_old_json_not_detected(self):
        self.assertFalse(_supports_quick_play(OLD))
        self.assertFalse(_supports_quick_play(LEGACY))
        self.assertFalse(_supports_quick_play({}))


class AdaptServerArgsTest(unittest.TestCase):
    def test_modern_converts_to_quick_play(self):
        out = adapt_server_args(
            ["--server", "play.example.com", "--port", "25566"], MODERN)
        self.assertEqual(out, ["--quickPlayMultiplayer", "play.example.com:25566"])

    def test_modern_default_port(self):
        out = adapt_server_args(["--server", "play.example.com"], MODERN)
        self.assertEqual(out, ["--quickPlayMultiplayer", "play.example.com:25565"])

    def test_modern_host_with_colon_kept(self):
        out = adapt_server_args(
            ["--server", "play.example.com:7777", "--port", "25565"], MODERN)
        self.assertEqual(out, ["--quickPlayMultiplayer", "play.example.com:7777"])

    def test_old_version_keeps_server_args(self):
        out = adapt_server_args(
            ["--server", "play.example.com", "--port", "25566"], OLD)
        self.assertEqual(out, ["--server", "play.example.com", "--port", "25566"])

    def test_legacy_version_keeps_server_args(self):
        out = adapt_server_args(["--server", "mc.hypixel.net"], LEGACY)
        self.assertEqual(out, ["--server", "mc.hypixel.net", "--port", "25565"])

    def test_no_server_passthrough(self):
        extras = ["--demo", "--width", "854"]
        self.assertEqual(adapt_server_args(extras, MODERN), extras)
        self.assertEqual(adapt_server_args([], MODERN), [])
        self.assertEqual(adapt_server_args(None, MODERN), [])

    def test_other_extras_preserved(self):
        out = adapt_server_args(
            ["--demo", "--server", "h", "--port", "1"], MODERN)
        self.assertEqual(out, ["--demo", "--quickPlayMultiplayer", "h:1"])

    def test_port_before_server(self):
        out = adapt_server_args(
            ["--port", "26000", "--server", "h"], MODERN)
        self.assertEqual(out, ["--quickPlayMultiplayer", "h:26000"])

    def test_empty_host_dropped(self):
        out = adapt_server_args(["--server", "", "--port", "25565"], MODERN)
        self.assertEqual(out, [])

    def test_blank_port_falls_back(self):
        out = adapt_server_args(["--server", "h", "--port", " "], MODERN)
        self.assertEqual(out, ["--quickPlayMultiplayer", "h:25565"])


class LaunchFlowServerSplitTest(unittest.TestCase):
    """版本设置里填 host:port 时要拆开，不能生成 --server host:port。"""

    def _prepare_extras(self, server, port=None):
        settings = {"server": server, "memory_mb": 1024,
                    "pre_launch_wait": True}
        if port:
            settings["port"] = port

        class _FakeVS:
            FULLSCREEN_MODES = launch_flow.version_settings.FULLSCREEN_MODES

            @staticmethod
            def load(_inst, _vid):
                return dict(settings)

            @staticmethod
            def apply_isolation(inst, _vid, _s):
                return inst.path

        class _FakeInst:
            path = Path("/tmp/pymcl-test-inst")

        orig_vs = launch_flow.version_settings
        orig_gm = launch_flow.global_mods
        launch_flow.version_settings = _FakeVS

        class _FakeGM:
            @staticmethod
            def apply(_p):
                return 0
        launch_flow.global_mods = _FakeGM
        try:
            prep = launch_flow.prepare(_FakeInst(), "1.20.1")
        finally:
            launch_flow.version_settings = orig_vs
            launch_flow.global_mods = orig_gm
        return prep["extra_game_args"]

    def test_host_port_split(self):
        extras = self._prepare_extras("play.example.com:7777")
        self.assertEqual(extras[extras.index("--server") + 1], "play.example.com")
        self.assertEqual(extras[extras.index("--port") + 1], "7777")

    def test_plain_host_uses_port_setting(self):
        extras = self._prepare_extras("play.example.com", port=26000)
        self.assertEqual(extras[extras.index("--server") + 1], "play.example.com")
        self.assertEqual(extras[extras.index("--port") + 1], "26000")

    def test_plain_host_default_port(self):
        extras = self._prepare_extras("play.example.com")
        self.assertEqual(extras[extras.index("--port") + 1], "25565")


if __name__ == "__main__":
    unittest.main()
