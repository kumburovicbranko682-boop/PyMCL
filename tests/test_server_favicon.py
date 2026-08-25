# -*- coding: utf-8 -*-
"""服务器 favicon：校验/规范化 + ping 后写回 servers.dat 的 icon 字段。"""
from __future__ import annotations

import base64
import struct
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest import mock

from mclauncher import server_ping, servers
from mclauncher.instances import Instance


def tiny_png() -> bytes:
    def chunk(tag, data):
        c = tag + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c))
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00\xff\x00\x00")
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", idat) + chunk(b"IEND", b""))


PNG = tiny_png()
PNG_B64 = base64.b64encode(PNG).decode("ascii")


class TestFaviconBase64(unittest.TestCase):
    def test_data_uri(self):
        self.assertEqual(
            server_ping.favicon_base64(f"data:image/png;base64,{PNG_B64}"), PNG_B64)

    def test_raw_base64_passthrough(self):
        self.assertEqual(server_ping.favicon_base64(PNG_B64), PNG_B64)

    def test_embedded_newlines(self):
        # 游戏返回的 favicon base64 里常混着 \n
        chopped = "\n".join(PNG_B64[i:i + 20] for i in range(0, len(PNG_B64), 20))
        self.assertEqual(
            server_ping.favicon_base64(f"data:image/png;base64,{chopped}"), PNG_B64)

    def test_invalid(self):
        self.assertEqual(server_ping.favicon_base64(""), "")
        self.assertEqual(server_ping.favicon_base64(None), "")
        self.assertEqual(server_ping.favicon_base64("not base64!!"), "")
        # 合法 base64 但不是 PNG
        jpeg = base64.b64encode(b"\xff\xd8\xff\xe0 fake jpeg").decode()
        self.assertEqual(server_ping.favicon_base64(jpeg), "")
        # data URI 缺逗号
        self.assertEqual(server_ping.favicon_base64("data:image/png;base64"), "")

    def test_oversize(self):
        big = PNG + b"\x00" * (512 * 1024)
        self.assertEqual(
            server_ping.favicon_base64(base64.b64encode(big).decode()), "")


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
        self.inst = Instance("favicon-test")
        self.inst.create()
        servers.add_server(self.inst, "小明的服", "mc.example.com", 25565)

    def api(self):
        from bridge.api import BackendAPI
        api = BackendAPI.__new__(BackendAPI)
        api._instance = lambda name: self.inst
        return api


class TestPingListedServer(Sandbox):
    def test_online_writes_icon_back(self):
        ok = {"online": True, "latency_ms": 20, "players_online": 3,
              "players_max": 20, "motd": "hi", "version": "1.21",
              "favicon": f"data:image/png;base64,{PNG_B64}"}
        with mock.patch("mclauncher.server_ping.ping", return_value=dict(ok)):
            result = self.api().ping_listed_server("favicon-test", 0)
        self.assertTrue(result["online"])
        self.assertEqual(result["index"], 0)
        self.assertEqual(result["icon"], PNG_B64)
        stored = servers.list_servers(self.inst)[0]
        self.assertEqual(stored["icon"], PNG_B64)
        # servers.dat 里其它字段不受影响
        self.assertEqual(stored["name"], "小明的服")
        self.assertEqual(stored["ip"], "mc.example.com")

    def test_same_icon_not_rewritten(self):
        servers.update_server(self.inst, 0, icon=PNG_B64)
        ok = {"online": True, "favicon": f"data:image/png;base64,{PNG_B64}"}
        with mock.patch("mclauncher.server_ping.ping", return_value=dict(ok)), \
             mock.patch("mclauncher.servers.update_server") as upd:
            self.api().ping_listed_server("favicon-test", 0)
        upd.assert_not_called()

    def test_offline_keeps_icon(self):
        servers.update_server(self.inst, 0, icon=PNG_B64)
        with mock.patch("mclauncher.server_ping.ping",
                        return_value={"online": False, "error": "连接超时"}):
            result = self.api().ping_listed_server("favicon-test", 0)
        self.assertFalse(result["online"])
        self.assertNotIn("icon", result)
        self.assertEqual(servers.list_servers(self.inst)[0]["icon"], PNG_B64)

    def test_invalid_favicon_not_written(self):
        ok = {"online": True, "favicon": "data:image/png;base64,%%%bad%%%"}
        with mock.patch("mclauncher.server_ping.ping", return_value=dict(ok)):
            result = self.api().ping_listed_server("favicon-test", 0)
        self.assertEqual(result["icon"], "")
        self.assertEqual(servers.list_servers(self.inst)[0]["icon"], "")

    def test_missing_index(self):
        result = self.api().ping_listed_server("favicon-test", 9)
        self.assertFalse(result["online"])
        self.assertEqual(result["index"], 9)
        self.assertTrue(result.get("error"))

    def test_game_written_icon_survives_roundtrip(self):
        """写回不应破坏游戏字段：icon 存的是纯 base64（无 data URI 前缀）。"""
        ok = {"online": True, "favicon": f"data:image/png;base64,{PNG_B64}"}
        with mock.patch("mclauncher.server_ping.ping", return_value=dict(ok)):
            self.api().ping_listed_server("favicon-test", 0)
        raw = servers.read_servers_dat(self.inst.path / "servers.dat")
        self.assertEqual(raw[0]["icon"], PNG_B64)
        self.assertNotIn("data:", raw[0]["icon"])
        decoded = base64.b64decode(raw[0]["icon"])
        self.assertTrue(decoded.startswith(b"\x89PNG"))


if __name__ == "__main__":
    unittest.main()
