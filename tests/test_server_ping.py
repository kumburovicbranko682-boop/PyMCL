from __future__ import annotations

import json
import socket
import struct
import threading
import unittest

from mclauncher import server_ping as sp


class VarIntTests(unittest.TestCase):
    def test_roundtrip(self):
        for n in (0, 1, 127, 128, 255, 300, 25565, 2097151, 2147483647):
            data = sp.pack_varint(n)
            value, off = sp.unpack_varint(data)
            self.assertEqual(value, n)
            self.assertEqual(off, len(data))

    def test_negative_protocol(self):
        # 握手协议号 -1 按 32 位补码编码
        data = sp.pack_varint(-1)
        self.assertEqual(data, b"\xff\xff\xff\xff\x0f")

    def test_truncated_raises(self):
        with self.assertRaises(sp.PingError):
            sp.unpack_varint(b"\x80")


class ParseAddressTests(unittest.TestCase):
    def test_plain_host(self):
        self.assertEqual(sp.parse_address("mc.example.com"),
                         ("mc.example.com", 25565, False))

    def test_host_with_port(self):
        self.assertEqual(sp.parse_address("mc.example.com:25599"),
                         ("mc.example.com", 25599, True))

    def test_ipv6_bracketed(self):
        self.assertEqual(sp.parse_address("[2001:db8::1]:25566"),
                         ("2001:db8::1", 25566, True))

    def test_ipv6_bare(self):
        self.assertEqual(sp.parse_address("2001:db8::1"),
                         ("2001:db8::1", 25565, False))

    def test_empty_raises(self):
        with self.assertRaises(sp.PingError):
            sp.parse_address("  ")

    def test_bad_port_raises(self):
        with self.assertRaises(sp.PingError):
            sp.parse_address("host:99999")
        with self.assertRaises(sp.PingError):
            sp.parse_address("host:abc")


class MotdTests(unittest.TestCase):
    def test_plain_string_with_colors(self):
        self.assertEqual(sp.describe_motd("\u00a7a Hello \u00a7lWorld "),
                         "Hello World")

    def test_chat_component(self):
        desc = {"text": "A ", "extra": [{"text": "B", "extra": [" C"]}]}
        self.assertEqual(sp.describe_motd(desc), "A B C")

    def test_none(self):
        self.assertEqual(sp.describe_motd(None), "")


STATUS = {
    "version": {"name": "Paper 1.20.1", "protocol": 763},
    "players": {"online": 5, "max": 100,
                "sample": [{"id": "x", "name": "Steve"}, {"id": "y", "name": "Alex"}]},
    "description": {"text": "\u00a7b测试", "extra": [{"text": " 服务器"}]},
}


class _FakeSLPServer(threading.Thread):
    """最小 Server List Ping 服务端：握手 + 状态请求 → 状态响应。"""

    def __init__(self, payload: dict):
        super().__init__(daemon=True)
        self.payload = payload
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.got_handshake = b""

    def run(self):
        try:
            conn, _ = self.sock.accept()
        except OSError:
            return
        with conn:
            conn.settimeout(3)
            try:
                self.got_handshake = self._read_packet(conn)  # handshake
                self._read_packet(conn)  # status request
                raw = json.dumps(self.payload).encode("utf-8")
                body = b"\x00" + sp.pack_varint(len(raw)) + raw
                conn.sendall(sp.pack_varint(len(body)) + body)
            except OSError:
                pass
        self.sock.close()

    @staticmethod
    def _read_packet(conn) -> bytes:
        length = 0
        for i in range(5):
            b = conn.recv(1)
            if not b:
                raise OSError("closed")
            length |= (b[0] & 0x7F) << (7 * i)
            if not b[0] & 0x80:
                break
        buf = bytearray()
        while len(buf) < length:
            chunk = conn.recv(length - len(buf))
            if not chunk:
                raise OSError("closed")
            buf.extend(chunk)
        return bytes(buf)


class PingProtocolTests(unittest.TestCase):
    def test_full_status_query(self):
        server = _FakeSLPServer(STATUS)
        server.start()
        out = sp.ping("127.0.0.1", server.port, timeout=3)
        server.join(timeout=3)
        self.assertTrue(out["online"])
        self.assertEqual(out["players_online"], 5)
        self.assertEqual(out["players_max"], 100)
        self.assertEqual(out["version"], "Paper 1.20.1")
        self.assertEqual(out["protocol"], 763)
        self.assertEqual(out["motd"], "测试 服务器")
        self.assertEqual(out["sample"], ["Steve", "Alex"])
        self.assertGreaterEqual(out["latency_ms"], 0)
        # 握手包内容：packet id 0x00 + 协议号 + 地址 + 端口 + next state 1
        hs = server.got_handshake
        self.assertEqual(hs[0], 0x00)
        self.assertTrue(hs.endswith(struct.pack(">H", server.port) + b"\x01"))

    def test_offline_returns_error_dict(self):
        # 端口 1 基本不会有监听
        out = sp.ping_address("127.0.0.1:1", timeout=1)
        self.assertFalse(out["online"])
        self.assertIn("error", out)

    def test_ping_address_explicit_port_overrides(self):
        server = _FakeSLPServer(STATUS)
        server.start()
        out = sp.ping_address("127.0.0.1", port=server.port, timeout=3)
        server.join(timeout=3)
        self.assertTrue(out["online"])
        self.assertEqual(out["port"], server.port)


if __name__ == "__main__":
    unittest.main()
