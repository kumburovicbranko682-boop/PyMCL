# -*- coding: utf-8 -*-
"""Minecraft 服务器状态查询（Server List Ping，1.7+ 现代协议）。

对齐 PCL2 联机页 / HMCL 多人游戏：不进游戏就能看到服务器是否在线、
延迟、在线人数、版本与 MOTD。纯 TCP 实现，无第三方依赖。

不做 SRV 记录解析（标准库没有 DNS SRV 查询）；绝大多数服务器
直接按 host:port 可达，与游戏内直连行为一致。
"""
from __future__ import annotations

import json
import re
import socket
import struct
import time

# MOTD 里的 § 格式码（颜色/粗体等）对启动器纯文本展示没有意义
_FORMAT_CODE = re.compile("§.")
# 状态响应 JSON 的上限；官方客户端也在此量级（防恶意服务器撑爆内存）
_MAX_STATUS = 1 << 21


class PingError(Exception):
    pass


def pack_varint(n: int) -> bytes:
    """Minecraft VarInt（32 位，7 位一组，最高位为继续位）。"""
    n &= 0xFFFFFFFF
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def read_varint(read_byte) -> int:
    """从字节源读 VarInt。read_byte: () -> int(0-255)。"""
    result = 0
    for i in range(5):
        b = read_byte()
        result |= (b & 0x7F) << (7 * i)
        if not (b & 0x80):
            if result >= 1 << 31:
                result -= 1 << 32
            return result
    raise PingError("VarInt 超过 5 字节，不是合法的 Minecraft 数据流")


def flatten_motd(desc) -> str:
    """把 description（字符串或 chat component）压平成纯文本。"""
    if isinstance(desc, str):
        text = desc
    elif isinstance(desc, dict):
        text = str(desc.get("text") or "")
        extra = desc.get("extra")
        if isinstance(extra, list):
            text += "".join(flatten_motd(part) for part in extra)
    elif isinstance(desc, list):
        text = "".join(flatten_motd(part) for part in desc)
    else:
        text = ""
    return _FORMAT_CODE.sub("", text)


class _Stream:
    def __init__(self, sock):
        self.sock = sock

    def read(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise PingError("连接被服务器关闭")
            buf += chunk
        return buf

    def read_byte(self) -> int:
        return self.read(1)[0]


def _send_packet(sock, payload: bytes):
    sock.sendall(pack_varint(len(payload)) + payload)


def _friendly_error(e) -> str:
    if isinstance(e, socket.timeout):
        return "连接超时"
    if isinstance(e, socket.gaierror):
        return "无法解析服务器地址"
    if isinstance(e, ConnectionRefusedError):
        return "连接被拒绝（服务器未开放此端口）"
    if isinstance(e, ConnectionResetError):
        return "连接被重置（可能不是 Minecraft 服务器）"
    if isinstance(e, (PingError, OSError)):
        return str(e) or e.__class__.__name__
    return str(e) or e.__class__.__name__


def _ping_impl(host: str, port: int, timeout: float) -> dict:
    t0 = time.monotonic()
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.settimeout(timeout)
        stream = _Stream(sock)

        # 握手（protocol=-1 表示只查询状态，服务端按自己的版本回答）
        addr = host.encode("utf-8")
        handshake = (pack_varint(0x00) + pack_varint(-1)
                     + pack_varint(len(addr)) + addr
                     + struct.pack(">H", port) + pack_varint(1))
        _send_packet(sock, handshake)
        _send_packet(sock, pack_varint(0x00))  # Status Request

        _total = read_varint(stream.read_byte)
        packet_id = read_varint(stream.read_byte)
        if packet_id != 0x00:
            raise PingError(f"服务器返回了意外的包 (id={packet_id})，可能不是 Minecraft 服务器")
        str_len = read_varint(stream.read_byte)
        if str_len < 0 or str_len > _MAX_STATUS:
            raise PingError("状态响应长度非法")
        status = json.loads(stream.read(str_len).decode("utf-8", "replace"))
        # 建连 + 状态往返的耗时作为延迟兜底
        latency_ms = max(1, int((time.monotonic() - t0) * 1000))

        # Ping/Pong 才是真实往返延迟；个别服务端不回 Pong，忽略即可
        try:
            payload = pack_varint(0x01) + struct.pack(">q", int(time.time() * 1000))
            t1 = time.monotonic()
            _send_packet(sock, payload)
            read_varint(stream.read_byte)
            pong_id = read_varint(stream.read_byte)
            stream.read(8)
            if pong_id == 0x01:
                latency_ms = max(1, int((time.monotonic() - t1) * 1000))
        except Exception:
            pass

    if not isinstance(status, dict):
        raise PingError("状态响应不是 JSON 对象")
    version = status.get("version") if isinstance(status.get("version"), dict) else {}
    players = status.get("players") if isinstance(status.get("players"), dict) else {}
    sample = []
    for p in players.get("sample") or []:
        if isinstance(p, dict) and p.get("name"):
            sample.append({"name": str(p.get("name")), "id": str(p.get("id") or "")})

    def _int(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return 0

    return {
        "online": True,
        "latency_ms": latency_ms,
        "version": str(version.get("name") or ""),
        "protocol": _int(version.get("protocol")),
        "players_online": _int(players.get("online")),
        "players_max": _int(players.get("max")),
        "players_sample": sample,
        "motd": flatten_motd(status.get("description")).strip(),
        "favicon": str(status.get("favicon") or ""),
    }


def favicon_base64(favicon) -> str:
    """把 SLP 返回的 favicon 规范成 servers.dat icon 字段的纯 base64。

    输入可以是 data URI（data:image/png;base64,xxx）或裸 base64；
    校验能解码、是 PNG、且不超过 512KB，不合法一律返回空串。
    游戏的多人列表就是从 servers.dat 的 icon 读图标的。
    """
    import base64
    text = str(favicon or "").strip()
    if not text:
        return ""
    if text.startswith("data:"):
        _head, _sep, text = text.partition(",")
        if not _sep:
            return ""
    text = "".join(text.split())
    try:
        raw = base64.b64decode(text, validate=True)
    except Exception:
        return ""
    if not raw.startswith(b"\x89PNG\r\n\x1a\n") or len(raw) > 512 * 1024:
        return ""
    return base64.b64encode(raw).decode("ascii")


def ping(host: str, port: int = 25565, timeout: float = 5.0) -> dict:
    """查询服务器状态。

    永不抛异常：离线/超时/协议错误返回 {"online": False, "error": 原因}，
    在线返回 {"online": True, latency_ms, version, protocol,
    players_online, players_max, players_sample, motd, favicon}。
    """
    host = str(host or "").strip()
    try:
        port = int(port or 25565)
    except (TypeError, ValueError):
        port = 25565
    if not host:
        return {"online": False, "error": "服务器地址为空"}
    if port < 1 or port > 65535:
        return {"online": False, "error": "端口号必须在 1-65535 之间"}
    try:
        return _ping_impl(host, port, timeout)
    except Exception as e:
        return {"online": False, "error": _friendly_error(e)}
