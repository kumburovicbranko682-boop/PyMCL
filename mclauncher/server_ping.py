# -*- coding: utf-8 -*-
"""Minecraft 服务器状态查询（Server List Ping，1.7+ 协议）。

对标 PCL2 / HMCL 的服务器列表：在线状态、延迟、MOTD、人数、版本、图标。
纯标准库实现，含 _minecraft._tcp SRV 解析（游戏客户端在默认端口时也会查）。
"""
from __future__ import annotations

import json
import random
import re
import socket
import struct
import time
from pathlib import Path

DEFAULT_PORT = 25565
_SRV_TIMEOUT = 1.5
# resolv.conf 不可用时（主要是 Windows）兜底的公共 DNS
_FALLBACK_DNS = ("223.5.5.5", "8.8.8.8")
_SECTION_RE = re.compile("§.")


class PingError(Exception):
    """状态查询失败，消息可直接展示。"""


# ---------------------------------------------------------------- VarInt

def pack_varint(value: int) -> bytes:
    out = bytearray()
    value &= 0xFFFFFFFF
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def read_varint(recv) -> int:
    """recv(n) -> bytes；最多 5 字节。"""
    result = 0
    for i in range(5):
        chunk = recv(1)
        if not chunk:
            raise PingError("连接被服务器提前关闭")
        byte = chunk[0]
        result |= (byte & 0x7F) << (7 * i)
        if not byte & 0x80:
            if result >= 1 << 31:
                result -= 1 << 32
            return result
    raise PingError("VarInt 过长，响应不是 Minecraft 协议")


# ---------------------------------------------------------------- MOTD 文本

def motd_text(desc) -> str:
    """把 status 响应里的 description（字符串 / chat 组件）拍平成纯文本。"""
    parts: list[str] = []

    def walk(node):
        if node is None:
            return
        if isinstance(node, str):
            parts.append(node)
            return
        if isinstance(node, list):
            for item in node:
                walk(item)
            return
        if isinstance(node, dict):
            if node.get("text"):
                parts.append(str(node["text"]))
            if node.get("translate") and not node.get("text"):
                parts.append(str(node["translate"]))
            walk(node.get("extra"))

    walk(desc)
    text = "".join(parts)
    text = _SECTION_RE.sub("", text)
    return " ".join(text.split())


# ---------------------------------------------------------------- SRV 解析

def _dns_servers() -> list[str]:
    servers = []
    try:
        for line in Path("/etc/resolv.conf").read_text("utf-8").splitlines():
            line = line.strip()
            if line.startswith("nameserver"):
                addr = line.split()[1] if len(line.split()) > 1 else ""
                if addr and ":" not in addr:  # 只用 IPv4，UDP 编码简单
                    servers.append(addr)
    except OSError:
        pass
    servers.extend(_FALLBACK_DNS)
    return servers[:3]


def _label_bytes(label: str) -> bytes:
    """DNS 标签编码。_minecraft 带下划线，idna codec 会拒绝，ASCII 优先。"""
    try:
        return label.encode("ascii")
    except UnicodeEncodeError:
        return label.encode("idna")


def _encode_dns_query(name: str) -> tuple[bytes, int]:
    tid = random.randint(0, 0xFFFF)
    header = struct.pack(">HHHHHH", tid, 0x0100, 1, 0, 0, 0)
    qname = b"".join(
        bytes([len(encoded)]) + encoded
        for label in name.strip(".").split(".") if label
        for encoded in (_label_bytes(label),))
    question = qname + b"\x00" + struct.pack(">HH", 33, 1)  # SRV IN
    return header + question, tid


def _read_dns_name(data: bytes, offset: int) -> tuple[str, int]:
    labels = []
    jumped = False
    end = offset
    for _ in range(64):
        if offset >= len(data):
            break
        length = data[offset]
        if length & 0xC0 == 0xC0:  # 压缩指针
            if not jumped:
                end = offset + 2
            offset = ((length & 0x3F) << 8) | data[offset + 1]
            jumped = True
            continue
        if length == 0:
            if not jumped:
                end = offset + 1
            break
        labels.append(data[offset + 1:offset + 1 + length].decode("ascii", "replace"))
        offset += 1 + length
    return ".".join(labels), end


def parse_srv_response(data: bytes, tid: int) -> tuple[str, int] | None:
    """从 DNS 响应中取 priority 最小的 SRV 记录 (target, port)。"""
    if len(data) < 12:
        return None
    rid, flags, qd, an = struct.unpack(">HHHH", data[:8])
    if rid != tid or not flags & 0x8000 or an == 0:
        return None
    offset = 12
    for _ in range(qd):  # 跳过 question
        _, offset = _read_dns_name(data, offset)
        offset += 4
    best: tuple[int, str, int] | None = None
    for _ in range(an):
        _, offset = _read_dns_name(data, offset)
        if offset + 10 > len(data):
            return None
        rtype, _rclass, _ttl, rdlen = struct.unpack(
            ">HHIH", data[offset:offset + 10])
        offset += 10
        rdata_at = offset
        offset += rdlen
        if rtype != 33 or rdlen < 7:
            continue
        priority, _weight, port = struct.unpack(
            ">HHH", data[rdata_at:rdata_at + 6])
        target, _ = _read_dns_name(data, rdata_at + 6)
        if target and (best is None or priority < best[0]):
            best = (priority, target, port)
    if best is None:
        return None
    return best[1], best[2]


def resolve_srv(host: str) -> tuple[str, int] | None:
    """查询 _minecraft._tcp.<host> 的 SRV 记录，查不到返回 None。"""
    query, tid = _encode_dns_query(f"_minecraft._tcp.{host}")
    for server in _dns_servers():
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.settimeout(_SRV_TIMEOUT)
                sock.sendto(query, (server, 53))
                data, _ = sock.recvfrom(2048)
            result = parse_srv_response(data, tid)
            if result:
                return result
        except OSError:
            continue
    return None


def _looks_like_ip(host: str) -> bool:
    try:
        socket.inet_aton(host)
        return True
    except OSError:
        return ":" in host  # IPv6


# ---------------------------------------------------------------- Ping 主体

def _send_packet(sock: socket.socket, payload: bytes):
    sock.sendall(pack_varint(len(payload)) + payload)


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(min(4096, n - len(buf)))
        if not chunk:
            raise PingError("连接被服务器提前关闭")
        buf.extend(chunk)
    return bytes(buf)


def ping(host: str, port: int = DEFAULT_PORT, timeout: float = 5.0,
         use_srv: bool = True) -> dict:
    """查询服务器状态。永不抛异常：失败时返回 online=False + error。"""
    host = str(host or "").strip()
    port = int(port or DEFAULT_PORT)
    result = {
        "online": False, "host": host, "port": port, "latency_ms": -1,
        "version": "", "protocol": -1, "players_online": 0, "players_max": 0,
        "sample": [], "motd": "", "favicon": "", "error": "",
    }
    if not host:
        result["error"] = "服务器地址为空"
        return result
    # 与游戏客户端一致：默认端口 + 域名时先查 SRV
    if use_srv and port == DEFAULT_PORT and not _looks_like_ip(host):
        srv = resolve_srv(host)
        if srv:
            host, port = srv
            result["host"], result["port"] = host, port
    try:
        started = time.monotonic()
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            host_bytes = host.encode("utf-8")
            handshake = (b"\x00" + pack_varint(-1) + pack_varint(len(host_bytes))
                         + host_bytes + struct.pack(">H", port & 0xFFFF)
                         + pack_varint(1))
            _send_packet(sock, handshake)
            _send_packet(sock, b"\x00")  # status request
            length = read_varint(lambda n: _recv_exact(sock, n))
            if length <= 0 or length > 4 * 1024 * 1024:
                raise PingError("响应长度异常，可能不是 Minecraft 服务器")
            frame = _recv_exact(sock, length)
            status_rtt = (time.monotonic() - started) * 1000
            if frame[0:1] != b"\x00":
                raise PingError("响应不是状态包，可能不是 Minecraft 服务器")
            pos = 1
            json_len = 0
            for i in range(5):
                byte = frame[pos]
                pos += 1
                json_len |= (byte & 0x7F) << (7 * i)
                if not byte & 0x80:
                    break
            payload = frame[pos:pos + json_len]
            data = json.loads(payload.decode("utf-8", "replace"))
            # ping / pong 精确测延迟；服务器不支持就用状态请求往返兜底
            latency = status_rtt
            try:
                token = struct.pack(">q", int(time.time() * 1000))
                sent = time.monotonic()
                _send_packet(sock, b"\x01" + token)
                pong_len = read_varint(lambda n: _recv_exact(sock, n))
                _recv_exact(sock, pong_len)
                latency = (time.monotonic() - sent) * 1000
            except (PingError, OSError):
                pass
        version = data.get("version") or {}
        players = data.get("players") or {}
        result.update({
            "online": True,
            "latency_ms": max(0, int(latency)),
            "version": str(version.get("name") or ""),
            "protocol": int(version.get("protocol") or -1),
            "players_online": int(players.get("online") or 0),
            "players_max": int(players.get("max") or 0),
            "sample": [_SECTION_RE.sub("", str(p.get("name") or ""))
                       for p in players.get("sample") or []
                       if isinstance(p, dict)],
            "motd": motd_text(data.get("description")),
            "favicon": str(data.get("favicon") or ""),
        })
        return result
    except socket.gaierror:
        result["error"] = "域名解析失败，请检查服务器地址"
    except socket.timeout:
        result["error"] = "连接超时，服务器可能离线"
    except ConnectionRefusedError:
        result["error"] = "连接被拒绝，服务器未在该端口开放"
    except PingError as exc:
        result["error"] = str(exc)
    except (OSError, ValueError, KeyError, IndexError) as exc:
        result["error"] = f"查询失败: {exc}"
    return result
