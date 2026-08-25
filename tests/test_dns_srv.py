# -*- coding: utf-8 -*-
"""DNS SRV 查询（_minecraft._tcp）：报文编解码、假 DNS 服务器端到端、
以及 server_ping.ping 的 SRV 跟随行为。"""
from __future__ import annotations

import socket
import struct
import threading
import unittest
from unittest import mock

from mclauncher import dns_srv, server_ping
from mclauncher.dns_srv import (
    _encode_name, build_query, is_ip_literal, parse_srv_response,
    resolve_minecraft_srv,
)


def build_response(txid, qname, answers, rcode=0, qr=True):
    """构造 DNS 响应。answers: [(priority, weight, port, target_bytes 或 str)]。"""
    flags = (0x8180 if qr else 0x0100) | (rcode & 0xF)
    out = bytearray(struct.pack(">HHHHHH", txid, flags, 1, len(answers), 0, 0))
    out += _encode_name(qname) + struct.pack(">HH", 33, 1)
    for priority, weight, port, target in answers:
        raw = target if isinstance(target, bytes) else _encode_name(target)
        rdata = struct.pack(">HHH", priority, weight, port) + raw
        out += b"\xc0\x0c"  # 名字：压缩指针回指问题名（offset 12）
        out += struct.pack(">HHIH", 33, 1, 300, len(rdata))
        out += rdata
    return bytes(out)


class TestEncodeName(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(_encode_name("mc.example.com"),
                         b"\x02mc\x07example\x03com\x00")

    def test_trailing_dot_ok(self):
        self.assertEqual(_encode_name("a.b."), b"\x01a\x01b\x00")

    def test_bad_labels(self):
        with self.assertRaises(ValueError):
            _encode_name("a..b")
        with self.assertRaises(ValueError):
            _encode_name("x" * 64 + ".com")

    def test_query_layout(self):
        pkt = build_query("_minecraft._tcp.example.com", 0x1234)
        self.assertEqual(pkt[:2], b"\x12\x34")
        self.assertTrue(pkt.endswith(struct.pack(">HH", 33, 1)))


class TestParseResponse(unittest.TestCase):
    QNAME = "_minecraft._tcp.example.com"

    def test_single_record(self):
        data = build_response(7, self.QNAME, [(0, 5, 25566, "mc.example.com")])
        recs = parse_srv_response(data, 7)
        self.assertEqual(recs, [(0, 5, 25566, "mc.example.com")])

    def test_multiple_records(self):
        data = build_response(7, self.QNAME, [
            (10, 1, 1111, "a.example.com"),
            (0, 9, 2222, "b.example.com"),
        ])
        recs = parse_srv_response(data, 7)
        self.assertEqual(len(recs), 2)
        self.assertIn((0, 9, 2222, "b.example.com"), recs)

    def test_compressed_target(self):
        # 问题名 offset：12=_minecraft, 23=_tcp, 28=example, 36=com
        # target = "mc" + 指针(28) → mc.example.com
        raw = b"\x02mc\xc0\x1c"
        data = build_response(9, self.QNAME, [(0, 0, 7777, raw)])
        recs = parse_srv_response(data, 9)
        self.assertEqual(recs, [(0, 0, 7777, "mc.example.com")])

    def test_nxdomain_empty(self):
        data = build_response(3, self.QNAME, [], rcode=3)
        self.assertEqual(parse_srv_response(data, 3), [])

    def test_id_mismatch(self):
        data = build_response(1, self.QNAME, [(0, 0, 1, "a.b")])
        with self.assertRaises(ValueError):
            parse_srv_response(data, 2)

    def test_not_a_response(self):
        data = build_response(5, self.QNAME, [], qr=False)
        with self.assertRaises(ValueError):
            parse_srv_response(data, 5)

    def test_truncated(self):
        data = build_response(5, self.QNAME, [(0, 0, 1, "a.b")])
        with self.assertRaises(ValueError):
            parse_srv_response(data[:20], 5)
        with self.assertRaises(ValueError):
            parse_srv_response(b"\x00" * 5, 0)

    def test_pointer_loop_rejected(self):
        # 名字直接自指 → 压缩指针成环
        hdr = struct.pack(">HHHHHH", 5, 0x8180, 1, 0, 0, 0)
        with self.assertRaises(ValueError):
            parse_srv_response(hdr + b"\xc0\x0c", 5)


class FakeDNSServer(threading.Thread):
    """本地 UDP 假 DNS：收一个查询，按查询的 txid 回一条 SRV 记录。"""

    def __init__(self, answers, rcode=0):
        super().__init__(daemon=True)
        self.answers = answers
        self.rcode = rcode
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.start()

    def run(self):
        try:
            self.sock.settimeout(5)
            data, addr = self.sock.recvfrom(4096)
            txid = struct.unpack(">H", data[:2])[0]
            resp = build_response(txid, "_minecraft._tcp.example.com",
                                  self.answers, rcode=self.rcode)
            self.sock.sendto(resp, addr)
        except OSError:
            pass
        finally:
            self.sock.close()


class TestResolveEndToEnd(unittest.TestCase):
    def _resolve_via(self, server):
        with mock.patch.object(dns_srv, "DNS_PORT", server.port), \
             mock.patch.object(dns_srv, "system_resolvers", lambda: []), \
             mock.patch.object(dns_srv, "PUBLIC_RESOLVERS", ("127.0.0.1",)):
            out = resolve_minecraft_srv("example.com", timeout=3)
        server.join(timeout=5)
        return out

    def test_hit(self):
        srv = FakeDNSServer([(0, 5, 25566, "mc.example.com")])
        self.assertEqual(self._resolve_via(srv), ("mc.example.com", 25566))

    def test_nxdomain(self):
        srv = FakeDNSServer([], rcode=3)
        self.assertIsNone(self._resolve_via(srv))

    def test_priority_weight_selection(self):
        srv = FakeDNSServer([
            (10, 99, 1111, "low.example.com"),
            (0, 1, 2222, "light.example.com"),
            (0, 9, 3333, "heavy.example.com"),
        ])
        self.assertEqual(self._resolve_via(srv), ("heavy.example.com", 3333))


class TestResolveLogic(unittest.TestCase):
    def test_no_dot_skips_query(self):
        with mock.patch.object(dns_srv, "query_srv") as q:
            self.assertIsNone(resolve_minecraft_srv("localhost"))
            self.assertIsNone(resolve_minecraft_srv(""))
        q.assert_not_called()

    def test_empty_answer_stops(self):
        calls = []
        with mock.patch.object(dns_srv, "system_resolvers", lambda: []), \
             mock.patch.object(dns_srv, "PUBLIC_RESOLVERS", ("1.1.1.1", "2.2.2.2")), \
             mock.patch.object(dns_srv, "query_srv",
                               lambda name, r, t: calls.append(r) or []):
            self.assertIsNone(resolve_minecraft_srv("a.example.com"))
        self.assertEqual(calls, ["1.1.1.1"])  # 权威空答复后不再问下一个

    def test_failover_to_next_resolver(self):
        def fake_query(name, resolver, timeout):
            if resolver == "1.1.1.1":
                raise socket.timeout()
            return [(0, 0, 4444, "mc.example.com")]
        with mock.patch.object(dns_srv, "system_resolvers", lambda: []), \
             mock.patch.object(dns_srv, "PUBLIC_RESOLVERS", ("1.1.1.1", "2.2.2.2")), \
             mock.patch.object(dns_srv, "query_srv", fake_query):
            self.assertEqual(resolve_minecraft_srv("a.example.com"),
                             ("mc.example.com", 4444))

    def test_bad_port_rejected(self):
        with mock.patch.object(dns_srv, "system_resolvers", lambda: []), \
             mock.patch.object(dns_srv, "PUBLIC_RESOLVERS", ("1.1.1.1",)), \
             mock.patch.object(dns_srv, "query_srv",
                               lambda *a: [(0, 0, 0, "mc.example.com")]):
            self.assertIsNone(resolve_minecraft_srv("a.example.com"))


class TestSystemResolvers(unittest.TestCase):
    def test_parses_resolv_conf(self):
        content = ("# comment\nnameserver 192.168.1.1\n"
                   "nameserver fe80::1\nsearch lan\nnameserver 10.0.0.1\n")
        with mock.patch("builtins.open", mock.mock_open(read_data=content)):
            self.assertEqual(dns_srv.system_resolvers(),
                             ["192.168.1.1", "10.0.0.1"])  # IPv6 被跳过

    def test_missing_file(self):
        with mock.patch("builtins.open", side_effect=OSError):
            self.assertEqual(dns_srv.system_resolvers(), [])


class TestIsIpLiteral(unittest.TestCase):
    def test_cases(self):
        self.assertTrue(is_ip_literal("127.0.0.1"))
        self.assertTrue(is_ip_literal("::1"))
        self.assertTrue(is_ip_literal("[2001:db8::1]"))
        self.assertFalse(is_ip_literal("mc.example.com"))
        self.assertFalse(is_ip_literal(""))


class TestPingFollowsSrv(unittest.TestCase):
    """server_ping.ping 的 SRV 门控：域名 + 默认端口才查，跟随后重定向连接。"""

    def _ping(self, host, port, resolve, impl=None):
        seen = {}

        def fake_impl(h, p, timeout):
            seen["target"] = (h, p)
            if impl is not None:
                raise impl
            return {"online": True, "latency_ms": 1}

        with mock.patch.object(dns_srv, "resolve_minecraft_srv", resolve), \
             mock.patch.object(server_ping, "_ping_impl", fake_impl):
            out = server_ping.ping(host, port, timeout=2)
        return out, seen

    def test_domain_default_port_follows(self):
        out, seen = self._ping("play.example.com", 25565,
                               lambda h, timeout: ("node.example.net", 20000))
        self.assertEqual(seen["target"], ("node.example.net", 20000))
        self.assertTrue(out["online"])
        self.assertEqual(out["srv"], "node.example.net:20000")

    def test_explicit_port_skips_srv(self):
        resolver = mock.Mock()
        out, seen = self._ping("play.example.com", 25566, resolver)
        resolver.assert_not_called()
        self.assertEqual(seen["target"], ("play.example.com", 25566))
        self.assertNotIn("srv", out)

    def test_ip_literal_skips_srv(self):
        resolver = mock.Mock()
        out, seen = self._ping("127.0.0.1", 25565, resolver)
        resolver.assert_not_called()
        self.assertEqual(seen["target"], ("127.0.0.1", 25565))
        self.assertNotIn("srv", out)

    def test_no_record_uses_original(self):
        out, seen = self._ping("play.example.com", 25565,
                               lambda h, timeout: None)
        self.assertEqual(seen["target"], ("play.example.com", 25565))
        self.assertNotIn("srv", out)

    def test_resolver_crash_falls_back(self):
        def broken(h, timeout):
            raise RuntimeError("resolver exploded")
        out, seen = self._ping("play.example.com", 25565, broken)
        self.assertEqual(seen["target"], ("play.example.com", 25565))
        self.assertTrue(out["online"])

    def test_offline_result_keeps_srv_info(self):
        out, _seen = self._ping("play.example.com", 25565,
                                lambda h, timeout: ("node.example.net", 20000),
                                impl=ConnectionRefusedError())
        self.assertFalse(out["online"])
        self.assertEqual(out["srv"], "node.example.net:20000")


if __name__ == "__main__":
    unittest.main()
