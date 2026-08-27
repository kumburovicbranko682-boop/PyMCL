# -*- coding: utf-8 -*-
"""Minecraft 服务器状态查询（Server List Ping 协议）。

对标 PCL2 / HMCL 的服务器列表在线状态：MOTD、在线人数、版本、延迟。
纯标准库实现（TCP + VarInt 协议帧），可选做一次尽力而为的 SRV 解析
（原版行为：地址未显式带端口时才查 _minecraft._tcp SRV）。
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
_COLOR_RE = re.compile("\u00a7.")


class PingError(Exception):
    """服务器状态查询失败。"""


# ---------------------------------------------------------------- 地址解析

def parse_address(text: str, default_port: int = DEFAULT_PORT) -> tuple[str, int, bool]:
    """解析 "host[:port]" / "[v6]:port"。返回 (host, port, 是否显式带端口)。"""
    raw = str(text or "").strip()
    if not raw:
        raise PingError("服务器地址为空")
    if raw.startswith("["):
        # [IPv6] 或 [IPv6]:port
        end = raw.find("]")
        if end < 0:
            raise PingError(f"无效地址: {raw}")
        host = raw[1:end]
        rest = raw[end + 1:]
        if rest.startswith(":"):
            return host, _parse_port(rest[1:], raw), True
        return host, default_port, False
    if raw.count(":") == 1:
        host, port_s = raw.split(":", 1)
        return host.strip(), _parse_port(port_s, raw), True
    if raw.count(":") > 1:
        # 裸 IPv6
        return raw, default_port, False
    return raw, default_port, False


def _parse_port(text: str, raw: str) -> int:
    try:
        port = int(text.strip())
    except ValueError:
        raise PingError(f"无效端口: {raw}") from None
    if not 1 <= port <= 65535:
        raise PingError(f"无效端口: {raw}")
    return port


# ---------------------------------------------------------------- VarInt / 帧

def pack_varint(value: int) -> bytes:
    if value < 0:
        value += 1 << 32
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def unpack_varint(data: bytes, offset: int = 0) -> tuple[int, int]:
    """返回 (值, 新 offset)。"""
    value = 0
    for i in range(5):
        if offset + i >= len(data):
            raise PingError("VarInt 数据不完整")
        byte = data[offset + i]
        value |= (byte & 0x7F) << (7 * i)
        if not byte & 0x80:
            return value, offset + i + 1
    raise PingError("VarInt 过长")


def _pack_string(text: str) -> bytes:
    raw = text.encode("utf-8")
    return pack_varint(len(raw)) + raw


def _pack_packet(packet_id: int, payload: bytes = b"") -> bytes:
    body = pack_varint(packet_id) + payload
    return pack_varint(len(body)) + body


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise PingError("连接被服务器关闭")
        buf.extend(chunk)
    return bytes(buf)


def _read_varint_sock(sock: socket.socket) -> int:
    value = 0
    for i in range(5):
        byte = _recv_exact(sock, 1)[0]
        value |= (byte & 0x7F) << (7 * i)
        if not byte & 0x80:
            return value
    raise PingError("VarInt 过长")


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


# ---------------------------------------------------------------- MOTD

def describe_motd(desc) -> str:
    """Chat 组件 / 纯字符串 → 纯文本（去 § 颜色码，压多余空白）。"""
    def _walk(node) -> str:
        if node is None:
            return ""
        if isinstance(node, str):
            return node
        if isinstance(node, list):
            return "".join(_walk(x) for x in node)
        if isinstance(node, dict):
            out = str(node.get("text") or "")
            out += "".join(_walk(x) for x in node.get("extra") or [])
            return out
        return str(node)

    text = _COLOR_RE.sub("", _walk(desc))
    return " ".join(text.split())


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
    text = _COLOR_RE.sub("", text)
    return " ".join(text.split())

# ---------------------------------------------------------------- SRV（尽力而为）

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


def resolve_srv(host: str, timeout: float = _SRV_TIMEOUT) -> tuple[str, int] | None:
    """查 _minecraft._tcp.<host> 的 SRV 记录。查不到 / 出错返回 None。

    纯标准库手写 DNS 查询（UDP 53），不依赖 dnspython；
    resolv.conf 读不到（Windows）时用公共 DNS 兜底。
    """
    if not host or _looks_like_ip(host):
        return None
    query, tid = _encode_dns_query(f"_minecraft._tcp.{host}")
    for server in _dns_servers():
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.settimeout(timeout or _SRV_TIMEOUT)
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
        pass
    return ":" in host


# ---------------------------------------------------------------- 状态查询

def ping(host: str, port: int = DEFAULT_PORT, timeout: float = 4.0) -> dict:
    """Server List Ping：返回 MOTD / 人数 / 版本 / 延迟。失败抛 PingError。"""
    started = time.monotonic()
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
    except OSError as e:
        raise PingError(f"无法连接 {host}:{port}（{e}）") from e
    try:
        sock.settimeout(timeout)
        # 握手：协议号 -1（状态查询不校验）、地址、端口、next state = 1
        handshake = (pack_varint(-1) + _pack_string(host)
                     + struct.pack(">H", port) + pack_varint(1))
        sock.sendall(_pack_packet(0x00, handshake))
        sock.sendall(_pack_packet(0x00))  # status request
        length = _read_varint_sock(sock)
        if length <= 0 or length > 4 * 1024 * 1024:
            raise PingError(f"响应长度异常: {length}")
        body = _recv_exact(sock, length)
        latency_ms = int((time.monotonic() - started) * 1000)
        packet_id, off = unpack_varint(body)
        if packet_id != 0x00:
            raise PingError(f"意外的包 id: {packet_id}")
        str_len, off = unpack_varint(body, off)
        raw = body[off:off + str_len]
        try:
            data = json.loads(raw.decode("utf-8", errors="replace"))
        except ValueError as e:
            raise PingError(f"状态 JSON 解析失败: {e}") from e
    finally:
        try:
            sock.close()
        except OSError:
            pass

    players = data.get("players") or {}
    version = data.get("version") or {}
    sample = [p.get("name") for p in players.get("sample") or []
              if isinstance(p, dict) and p.get("name")]
    return {
        "online": True,
        "host": host,
        "port": port,
        "latency_ms": latency_ms,
        "motd": describe_motd(data.get("description")),
        "players_online": int(players.get("online") or 0),
        "players_max": int(players.get("max") or 0),
        "sample": sample[:12],
        "version": str(version.get("name") or ""),
        "protocol": int(version.get("protocol") or 0),
        "favicon": data.get("favicon") or "",
    }


def ping_address(text: str, port: int = 0, timeout: float = 4.0) -> dict:
    """解析地址并查询状态。查询失败返回 {"online": False, "error": ...} 而不抛，
    地址本身无效才抛 PingError。"""
    host, parsed_port, explicit = parse_address(text)
    if port:
        parsed_port, explicit = int(port), True
    if not explicit:
        srv = resolve_srv(host, timeout=min(timeout, 2.0))
        if srv:
            host, parsed_port = srv
    try:
        return ping(host, parsed_port, timeout=timeout)
    except PingError as e:
        return {"online": False, "host": host, "port": parsed_port, "error": str(e)}
