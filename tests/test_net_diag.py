# -*- coding: utf-8 -*-
"""联机网络检测（PCL CE「网络检测」同款）：STUN 报文与 NAT 分类。

覆盖：Binding Request 构造、XOR/普通 MAPPED-ADDRESS 解析（v4/v6）、
事务 ID 与报文类型校验、NAT 分类纯函数、detect_nat 汇总与 IPv6 探测。
"""
from __future__ import annotations

import socket
import struct
import unittest
from unittest.mock import patch

from mclauncher import net_diag
from mclauncher.net_diag import (
    MAGIC_COOKIE, build_request, classify, parse_response,
)

TXID = bytes(range(12))


def _attr(attr_type: int, value: bytes) -> bytes:
    pad = (4 - len(value) % 4) % 4
    return struct.pack("!HH", attr_type, len(value)) + value + b"\x00" * pad


def _response(txid: bytes, attrs: bytes, msg_type: int = 0x0101) -> bytes:
    return struct.pack("!HHI", msg_type, len(attrs), MAGIC_COOKIE) + txid + attrs


def _xor_mapped_v4(ip: str, port: int) -> bytes:
    xport = port ^ (MAGIC_COOKIE >> 16)
    raw = bytes(b ^ k for b, k in zip(socket.inet_aton(ip),
                                      struct.pack("!I", MAGIC_COOKIE)))
    return _attr(0x0020, b"\x00\x01" + struct.pack("!H", xport) + raw)


def _plain_mapped_v4(ip: str, port: int) -> bytes:
    return _attr(0x0001, b"\x00\x01" + struct.pack("!H", port) + socket.inet_aton(ip))


class TestStunMessages(unittest.TestCase):
    def test_build_request(self):
        req = build_request(TXID)
        self.assertEqual(len(req), 20)
        msg_type, length, cookie = struct.unpack("!HHI", req[:8])
        self.assertEqual(msg_type, 0x0001)
        self.assertEqual(length, 0)
        self.assertEqual(cookie, MAGIC_COOKIE)
        self.assertEqual(req[8:], TXID)

    def test_build_request_bad_txid(self):
        with self.assertRaises(ValueError):
            build_request(b"short")

    def test_parse_xor_mapped(self):
        data = _response(TXID, _xor_mapped_v4("203.0.113.7", 45678))
        self.assertEqual(parse_response(data, TXID), ("203.0.113.7", 45678))

    def test_parse_plain_mapped_fallback(self):
        data = _response(TXID, _plain_mapped_v4("198.51.100.2", 12345))
        self.assertEqual(parse_response(data, TXID), ("198.51.100.2", 12345))

    def test_xor_preferred_over_plain(self):
        attrs = _plain_mapped_v4("10.0.0.1", 1) + _xor_mapped_v4("203.0.113.7", 45678)
        data = _response(TXID, attrs)
        self.assertEqual(parse_response(data, TXID), ("203.0.113.7", 45678))

    def test_parse_xor_mapped_v6(self):
        ip6 = "2001:db8::1234"
        port = 40000
        xport = port ^ (MAGIC_COOKIE >> 16)
        key = struct.pack("!I", MAGIC_COOKIE) + TXID
        raw = bytes(b ^ k for b, k in zip(
            socket.inet_pton(socket.AF_INET6, ip6), key))
        data = _response(TXID, _attr(0x0020, b"\x00\x02" + struct.pack("!H", xport) + raw))
        self.assertEqual(parse_response(data, TXID), (ip6, port))

    def test_reject_wrong_txid(self):
        data = _response(bytes(12), _xor_mapped_v4("203.0.113.7", 45678))
        self.assertIsNone(parse_response(data, TXID))

    def test_reject_wrong_type(self):
        data = _response(TXID, _xor_mapped_v4("203.0.113.7", 45678), msg_type=0x0111)
        self.assertIsNone(parse_response(data, TXID))

    def test_reject_short_packet(self):
        self.assertIsNone(parse_response(b"\x01\x01", TXID))


class TestClassify(unittest.TestCase):
    LOCAL = ("192.168.1.10", 50000)

    def test_blocked(self):
        self.assertEqual(classify([None, None], *self.LOCAL), "blocked")

    def test_open(self):
        r = ("192.168.1.10", 50000)
        self.assertEqual(classify([r, r], *self.LOCAL), "open")

    def test_cone(self):
        r = ("203.0.113.7", 45678)
        self.assertEqual(classify([r, r, None], *self.LOCAL), "cone")

    def test_symmetric(self):
        self.assertEqual(classify(
            [("203.0.113.7", 45678), ("203.0.113.7", 45999)], *self.LOCAL),
            "symmetric")

    def test_single_response_unknown(self):
        self.assertEqual(classify([("203.0.113.7", 45678), None], *self.LOCAL),
                         "unknown")

    def test_single_response_open(self):
        self.assertEqual(classify([("192.168.1.10", 50000)], *self.LOCAL), "open")


class TestDetectAndDiagnose(unittest.TestCase):
    def test_detect_nat_cone(self):
        mapped = ("203.0.113.7", 45678)
        with patch.object(net_diag, "stun_query", return_value=mapped), \
                patch.object(net_diag, "_local_outbound_ip", return_value="192.168.1.10"):
            out = net_diag.detect_nat([("s1", 3478), ("s2", 3478), ("s3", 3478)])
        self.assertEqual(out["nat_type"], "cone")
        self.assertEqual(out["public"], "203.0.113.7:45678")
        # 拿到两个响应就停，不会打第三个服务器
        self.assertEqual(len(out["servers"]), 2)

    def test_detect_nat_blocked(self):
        with patch.object(net_diag, "stun_query", return_value=None), \
                patch.object(net_diag, "_local_outbound_ip", return_value=""):
            out = net_diag.detect_nat([("s1", 3478), ("s2", 3478)])
        self.assertEqual(out["nat_type"], "blocked")
        self.assertEqual(out["public"], "")

    def test_diagnose_shape(self):
        with patch.object(net_diag, "detect_nat",
                          return_value={"nat_type": "symmetric",
                                        "public": "1.2.3.4:5", "servers": ["s1"],
                                        "mappings": ["1.2.3.4:5"]}), \
                patch.object(net_diag, "check_ipv6",
                             return_value={"supported": True, "address": "2001:db8::1"}):
            out = net_diag.diagnose()
        self.assertEqual(out["nat_type"], "symmetric")
        self.assertIn("对称", out["nat_label"])
        self.assertTrue(out["advice"])
        self.assertTrue(out["ipv6"])
        self.assertEqual(out["ipv6_address"], "2001:db8::1")

    def test_check_ipv6_no_route(self):
        with patch.object(socket, "socket", side_effect=OSError("no v6")):
            self.assertEqual(net_diag.check_ipv6(),
                             {"supported": False, "address": ""})


class TestFacades(unittest.TestCase):
    def test_bridge_has_method(self):
        from bridge.api import BackendAPI
        self.assertTrue(callable(getattr(BackendAPI, "network_diagnose", None)))

    def test_bridge_impl(self):
        from bridge.api import BackendAPI
        with patch.object(net_diag, "diagnose",
                          return_value={"nat_type": "cone"}) as mocked:
            out = BackendAPI.network_diagnose(None)
        mocked.assert_called_once()
        self.assertEqual(out["nat_type"], "cone")


if __name__ == "__main__":
    unittest.main()
