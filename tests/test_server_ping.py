# -*- coding: utf-8 -*-
"""服务器状态查询（Server List Ping）：协议编解码 + mock 服务器端到端。"""
from __future__ import annotations

import io
import json
import socket
import threading
import unittest

from mclauncher import server_ping
from mclauncher.server_ping import (
    PingError, flatten_motd, pack_varint, ping, read_varint,
)


def _decode_varint(data: bytes) -> int:
    buf = io.BytesIO(data)
    return read_varint(lambda: buf.read(1)[0])


class TestVarint(unittest.TestCase):
    def test_roundtrip(self):
        for n in (0, 1, 127, 128, 255, 300, 25565, 2097151, 2 ** 31 - 1, -1):
            self.assertEqual(_decode_varint(pack_varint(n)), n, msg=n)

    def test_known_encodings(self):
        self.assertEqual(pack_varint(0), b"\x00")
        self.assertEqual(pack_varint(128), b"\x80\x01")
        self.assertEqual(pack_varint(-1), b"\xff\xff\xff\xff\x0f")

    def test_overlong_rejected(self):
        buf = io.BytesIO(b"\xff\xff\xff\xff\xff\xff")
        with self.assertRaises(PingError):
            read_varint(lambda: buf.read(1)[0])


class TestMotd(unittest.TestCase):
    def test_plain_string(self):
        self.assertEqual(flatten_motd("Hello §aWorld"), "Hello World")

    def test_chat_component(self):
        desc = {"text": "A ", "extra": [{"text": "§lB"}, {"text": " C"}]}
        self.assertEqual(flatten_motd(desc), "A B C")

    def test_list_and_garbage(self):
        self.assertEqual(flatten_motd(["a", {"text": "b"}]), "ab")
        self.assertEqual(flatten_motd(None), "")
        self.assertEqual(flatten_motd(42), "")


def _read_packet(stream):
    length = read_varint(stream.read_byte)
    data = stream.read(length)
    buf = io.BytesIO(data)
    pid = read_varint(lambda: buf.read(1)[0])
    return pid, buf.read()


class MockSLPServer(threading.Thread):
    """最小 SLP 服务端：握手 → 状态响应，可选回 Pong。"""

    def __init__(self, status: dict, pong: bool = True):
        super().__init__(daemon=True)
        self.status = status
        self.pong = pong
        self.handshake = None
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.start()

    def run(self):
        conn = None
        try:
            conn, _ = self.sock.accept()
            conn.settimeout(5)
            stream = server_ping._Stream(conn)
            _pid, self.handshake = _read_packet(stream)
            _read_packet(stream)  # Status Request
            payload = json.dumps(self.status).encode("utf-8")
            body = pack_varint(0x00) + pack_varint(len(payload)) + payload
            conn.sendall(pack_varint(len(body)) + body)
            if self.pong:
                _pid, ts = _read_packet(stream)  # Ping
                body = pack_varint(0x01) + ts
                conn.sendall(pack_varint(len(body)) + body)
        except Exception:
            pass
        finally:
            if conn is not None:
                try:
                    conn.close()
                except OSError:
                    pass
            try:
                self.sock.close()
            except OSError:
                pass


STATUS = {
    "version": {"name": "Paper 1.20.4", "protocol": 765},
    "players": {
        "max": 100, "online": 3,
        "sample": [{"name": "Alice", "id": "u1"}, {"name": "Bob", "id": "u2"}],
    },
    "description": {"text": "§b欢迎来到 ", "extra": [{"text": "测试服"}]},
    "favicon": "data:image/png;base64,AAAA",
}


class TestPingEndToEnd(unittest.TestCase):
    def test_full_status(self):
        srv = MockSLPServer(STATUS)
        out = ping("127.0.0.1", srv.port, timeout=3)
        srv.join(timeout=5)
        self.assertTrue(out["online"])
        self.assertEqual(out["version"], "Paper 1.20.4")
        self.assertEqual(out["protocol"], 765)
        self.assertEqual(out["players_online"], 3)
        self.assertEqual(out["players_max"], 100)
        self.assertEqual([p["name"] for p in out["players_sample"]], ["Alice", "Bob"])
        self.assertEqual(out["motd"], "欢迎来到 测试服")
        self.assertTrue(out["favicon"].startswith("data:image/png"))
        self.assertGreaterEqual(out["latency_ms"], 1)
        # 握手内容正确（protocol=-1，next_state=1）
        buf = io.BytesIO(srv.handshake)
        self.assertEqual(read_varint(lambda: buf.read(1)[0]), -1)

    def test_server_without_pong_still_online(self):
        srv = MockSLPServer(STATUS, pong=False)
        out = ping("127.0.0.1", srv.port, timeout=3)
        srv.join(timeout=5)
        self.assertTrue(out["online"])
        self.assertEqual(out["players_max"], 100)

    def test_offline_connection_refused(self):
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        free_port = probe.getsockname()[1]
        probe.close()
        out = ping("127.0.0.1", free_port, timeout=2)
        self.assertFalse(out["online"])
        self.assertTrue(out["error"])

    def test_not_a_minecraft_server(self):
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]

        def junk():
            conn, _ = srv.accept()
            conn.sendall(b"HTTP/1.1 400 Bad Request\r\n\r\n")
            conn.close()
            srv.close()

        threading.Thread(target=junk, daemon=True).start()
        out = ping("127.0.0.1", port, timeout=2)
        self.assertFalse(out["online"])

    def test_bad_inputs(self):
        self.assertFalse(ping("", 25565)["online"])
        self.assertFalse(ping("localhost", 99999)["online"])


class TestFacade(unittest.TestCase):
    """bridge.api 与 backend 对齐：ping_server。"""

    def test_bridge_ping_server(self):
        from bridge.api import BackendAPI
        api = BackendAPI.__new__(BackendAPI)
        srv = MockSLPServer(STATUS)
        out = api.ping_server("127.0.0.1", srv.port)
        srv.join(timeout=5)
        self.assertTrue(out["online"])
        self.assertEqual(out["players_online"], 3)


if __name__ == "__main__":
    unittest.main()
