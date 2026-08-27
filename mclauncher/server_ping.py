# -*- coding: utf-8 -*-
"""Minecraft 服务器状态查询（Server List Ping 协议）。

对标 PCL2 / HMCL 的服务器列表在线状态：MOTD、在线人数、版本、延迟。
纯标准库实现（TCP + VarInt 协议帧），可选做一次尽力而为的 SRV 解析
（原版行为：地址未显式带端口时才查 _minecraft._tcp SRV）。
"""
from __future__ import annotations

import json
import re
import socket
import struct
import time

DEFAULT_PORT = 25565
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


# ---------------------------------------------------------------- SRV（尽力而为）

def resolve_srv(host: str, timeout: float = 2.0) -> tuple[str, int] | None:
    """查 _minecraft._tcp.<host> 的 SRV 记录。查不到 / 出错返回 None。

    优先用 dnspython（若装了），否则跳过——SLP 直连绝大多数服务器可用，
    SRV 只是锦上添花，不值得为它手写 DNS 客户端引入故障面。
    """
    if not host or _looks_like_ip(host):
        return None
    try:
        import dns.resolver  # type: ignore
    except ImportError:
        return None
    try:
        answers = dns.resolver.resolve(f"_minecraft._tcp.{host}", "SRV",
                                       lifetime=timeout)
        best = min(answers, key=lambda r: (r.priority, -r.weight))
        return str(best.target).rstrip("."), int(best.port)
    except Exception:
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
