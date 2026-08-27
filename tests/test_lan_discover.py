# -*- coding: utf-8 -*-
"""局域网世界发现：广播解析 + 本地 UDP 端到端。"""
from __future__ import annotations

import socket
import threading
import time
import unittest
from unittest import mock

from mclauncher import lan


class TestParse(unittest.TestCase):
    def test_standard(self):
        out = lan.parse_lan_announcement(
            "[MOTD]Alice 的世界[/MOTD][AD]54321[/AD]".encode("utf-8"), "192.168.1.5")
        self.assertEqual(out, {
            "motd": "Alice 的世界", "ip": "192.168.1.5", "port": 54321,
            "address": "192.168.1.5:54321",
        })

    def test_ad_with_ip_port(self):
        # 部分版本 AD 是 ip:port，而且 ip 常是 0.0.0.0：端口取 AD、地址取发送者
        out = lan.parse_lan_announcement(
            b"[MOTD]w[/MOTD][AD]0.0.0.0:12345[/AD]", "10.0.0.2")
        self.assertEqual(out["port"], 12345)
        self.assertEqual(out["ip"], "10.0.0.2")

    def test_missing_motd_gets_default(self):
        out = lan.parse_lan_announcement(b"[AD]25565[/AD]", "10.0.0.3")
        self.assertTrue(out["motd"])
        self.assertEqual(out["port"], 25565)

    def test_garbage_rejected(self):
        for bad in (b"", b"hello", b"[MOTD]x[/MOTD]", b"[AD]notaport[/AD]",
                    b"[AD]0[/AD]", b"[AD]70000[/AD]", b"\xff\xfe\x00"):
            self.assertIsNone(lan.parse_lan_announcement(bad, "10.0.0.1"), msg=bad)

    def test_ipv6_sender_bracketed(self):
        out = lan.parse_lan_announcement(b"[AD]1234[/AD]", "fe80::1")
        self.assertEqual(out["address"], "[fe80::1]:1234")


class TestDiscover(unittest.TestCase):
    def _free_udp_port(self) -> int:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        return port

    def test_receives_and_dedupes(self):
        port = self._free_udp_port()

        def announce():
            time.sleep(0.2)
            out = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            for _ in range(3):  # 同一世界连发多次，应当去重
                out.sendto("[MOTD]测试世界[/MOTD][AD]45678[/AD]".encode("utf-8"),
                           ("127.0.0.1", port))
                time.sleep(0.05)
            out.sendto(b"not minecraft", ("127.0.0.1", port))
            out.close()

        t = threading.Thread(target=announce, daemon=True)
        t.start()
        worlds = lan.discover_lan_worlds(timeout=1.2, port=port)
        t.join(timeout=3)
        self.assertEqual(len(worlds), 1)
        self.assertEqual(worlds[0]["motd"], "测试世界")
        self.assertEqual(worlds[0]["port"], 45678)
        self.assertEqual(worlds[0]["ip"], "127.0.0.1")

    def test_empty_when_silent(self):
        port = self._free_udp_port()
        worlds = lan.discover_lan_worlds(timeout=0.6, port=port)
        self.assertEqual(worlds, [])

    def test_port_conflict_returns_empty(self):
        port = self._free_udp_port()
        holder = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # 不带 REUSEADDR 的独占监听，让 discover 的 bind 失败
        holder.bind(("127.0.0.1", port))
        try:
            sock2 = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                sock2.bind(("", port))
                self.skipTest("平台允许重复绑定，无法模拟端口冲突")
            except OSError:
                pass
            finally:
                sock2.close()
            self.assertEqual(lan.discover_lan_worlds(timeout=0.3, port=port), [])
        finally:
            holder.close()


class TestFacade(unittest.TestCase):
    """bridge.api 与 backend 对齐：discover_lan_worlds。"""

    def test_bridge_discover(self):
        from bridge.api import BackendAPI
        api = BackendAPI.__new__(BackendAPI)
        port = TestDiscover._free_udp_port(self)

        def announce():
            time.sleep(0.15)
            out = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            out.sendto(b"[MOTD]bridge[/MOTD][AD]40000[/AD]", ("127.0.0.1", port))
            out.close()

        t = threading.Thread(target=announce, daemon=True)
        t.start()
        with mock.patch.object(lan, "LAN_PORT", port):
            worlds = api.discover_lan_worlds(timeout=0.8)
        t.join(timeout=3)
        self.assertEqual([w["motd"] for w in worlds], ["bridge"])


if __name__ == "__main__":
    unittest.main()
