# -*- coding: utf-8 -*-
"""Server List Ping：对本地 mock Minecraft 服务器验证协议与解析。"""
from __future__ import annotations

import json
import socket
import struct
import threading
import unittest

from mclauncher import server_ping


STATUS = {
    "version": {"name": "Paper 1.21.1", "protocol": 767},
    "players": {"max": 100, "online": 3,
                "sample": [{"name": "Alice", "id": "u1"},
                           {"name": "Bob", "id": "u2"}]},
    "description": {"text": "§a欢迎来到 ", "extra": [
        {"text": "测试服", "color": "gold"},
        {"text": " §7| 生存"},
    ]},
    "favicon": "data:image/png;base64,aGk=",
}


def _read_varint(sock_file) -> int:
    result = 0
    for i in range(5):
        byte = sock_file.read(1)
        if not byte:
            raise EOFError
        result |= (byte[0] & 0x7F) << (7 * i)
        if not byte[0] & 0x80:
            return result
    raise ValueError("varint too long")


class MockMinecraftServer:
    """最小 SLP 服务端：握手 → status → 可选 ping/pong。"""

    def __init__(self, respond_pong=True):
        self.respond_pong = respond_pong
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(4)
        self.port = self.sock.getsockname()[1]
        self.seen_handshakes: list[dict] = []
        self._stop = False
        threading.Thread(target=self._serve, daemon=True).start()

    def close(self):
        self._stop = True
        try:
            self.sock.close()
        except OSError:
            pass

    def _serve(self):
        while not self._stop:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,),
                             daemon=True).start()

    def _handle(self, conn: socket.socket):
        try:
            f = conn.makefile("rb")
            # 握手包
            length = _read_varint(f)
            frame = f.read(length)
            pos = 1  # packet id 0x00
            proto = 0
            for i in range(5):
                b = frame[pos]
                pos += 1
                proto |= (b & 0x7F) << (7 * i)
                if not b & 0x80:
                    break
            host_len = frame[pos]
            pos += 1
            host = frame[pos:pos + host_len].decode()
            pos += host_len
            port = struct.unpack(">H", frame[pos:pos + 2])[0]
            self.seen_handshakes.append({"host": host, "port": port})
            # status request
            length = _read_varint(f)
            f.read(length)
            payload = json.dumps(STATUS).encode()
            body = b"\x00" + server_ping.pack_varint(len(payload)) + payload
            conn.sendall(server_ping.pack_varint(len(body)) + body)
            # ping → pong
            length = _read_varint(f)
            frame = f.read(length)
            if self.respond_pong and frame[0:1] == b"\x01":
                conn.sendall(server_ping.pack_varint(len(frame)) + frame)
        except (EOFError, OSError, IndexError):
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass


class VarIntTests(unittest.TestCase):
    def test_roundtrip(self):
        for value in (0, 1, 127, 128, 255, 25565, 2097151, 2 ** 31 - 1, -1):
            packed = server_ping.pack_varint(value)
            buf = bytearray(packed)

            def recv(n):
                out = bytes(buf[:n])
                del buf[:n]
                return out

            self.assertEqual(server_ping.read_varint(recv), value, value)


class MotdTests(unittest.TestCase):
    def test_plain_string_strips_section_codes(self):
        self.assertEqual(server_ping.motd_text("§aHello §lWorld"), "Hello World")

    def test_chat_component_with_extra(self):
        desc = {"text": "A", "extra": [{"text": "B"}, {"text": "§cC"}]}
        self.assertEqual(server_ping.motd_text(desc), "ABC")

    def test_list_form(self):
        self.assertEqual(server_ping.motd_text(["x", {"text": "y"}]), "xy")

    def test_none(self):
        self.assertEqual(server_ping.motd_text(None), "")


class SrvParseTests(unittest.TestCase):
    def test_parse_srv_answer(self):
        # 手工构造带压缩指针的 SRV 响应
        tid = 0x1234
        name = b"\x0a_minecraft\x04_tcp\x07example\x03com\x00"
        header = struct.pack(">HHHHHH", tid, 0x8180, 1, 1, 0, 0)
        question = name + struct.pack(">HH", 33, 1)
        target = b"\x02mc\x07example\x03com\x00"
        rdata = struct.pack(">HHH", 5, 0, 25599) + target
        answer = (b"\xc0\x0c" + struct.pack(">HHIH", 33, 1, 300, len(rdata))
                  + rdata)
        data = header + question + answer
        result = server_ping.parse_srv_response(data, tid)
        self.assertEqual(result, ("mc.example.com", 25599))

    def test_wrong_tid_rejected(self):
        data = struct.pack(">HHHHHH", 1, 0x8180, 0, 1, 0, 0)
        self.assertIsNone(server_ping.parse_srv_response(data, 2))

    def test_no_answers(self):
        data = struct.pack(">HHHHHH", 7, 0x8180, 0, 0, 0, 0)
        self.assertIsNone(server_ping.parse_srv_response(data, 7))


class PingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = MockMinecraftServer()

    @classmethod
    def tearDownClass(cls):
        cls.server.close()

    def test_full_status(self):
        result = server_ping.ping("127.0.0.1", self.server.port, timeout=5)
        self.assertTrue(result["online"], result.get("error"))
        self.assertEqual(result["version"], "Paper 1.21.1")
        self.assertEqual(result["protocol"], 767)
        self.assertEqual(result["players_online"], 3)
        self.assertEqual(result["players_max"], 100)
        self.assertEqual(result["sample"], ["Alice", "Bob"])
        self.assertEqual(result["motd"], "欢迎来到 测试服 | 生存")
        self.assertTrue(result["favicon"].startswith("data:image/png"))
        self.assertGreaterEqual(result["latency_ms"], 0)
        # 握手里带的是我们请求的 host/port
        hs = self.server.seen_handshakes[-1]
        self.assertEqual(hs["host"], "127.0.0.1")
        self.assertEqual(hs["port"], self.server.port)

    def test_no_pong_still_online(self):
        server = MockMinecraftServer(respond_pong=False)
        try:
            result = server_ping.ping("127.0.0.1", server.port, timeout=3)
            self.assertTrue(result["online"], result.get("error"))
            self.assertGreaterEqual(result["latency_ms"], 0)
        finally:
            server.close()

    def test_offline_refused(self):
        # 占个端口再关掉，保证没人监听
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()
        result = server_ping.ping("127.0.0.1", port, timeout=2)
        self.assertFalse(result["online"])
        self.assertIn("拒绝", result["error"])

    def test_bad_dns(self):
        result = server_ping.ping(
            "definitely-not-a-real-host-pymcl.invalid", 25566, timeout=2,
            use_srv=False)
        self.assertFalse(result["online"])
        self.assertIn("域名", result["error"])

    def test_empty_host(self):
        result = server_ping.ping("", 25565)
        self.assertFalse(result["online"])


if __name__ == "__main__":
    unittest.main()
