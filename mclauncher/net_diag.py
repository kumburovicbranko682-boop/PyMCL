# -*- coding: utf-8 -*-
"""联机网络检测（PCL CE「网络检测」同款）：NAT 类型 + IPv6 可用性。

用标准 STUN Binding（RFC 5389）向多个公共服务器询问本机的公网映射，
按映射行为分类 NAT：

- open       无 NAT（公网映射 == 本机地址端口）
- cone       锥形（不同目标看到同一映射，P2P 打洞顺畅）
- symmetric  对称（每个目标映射都不同，打洞困难，联机多半要中继）
- blocked    UDP 出不去（防火墙 / 无网络）

不发任何伪造数据，全部是真实 STUN 请求；服务器列表优先国内可达。
"""
from __future__ import annotations

import os
import secrets
import socket
import struct

from . import utils

MAGIC_COOKIE = 0x2112A442
_BINDING_REQUEST = 0x0001
_BINDING_RESPONSE = 0x0101
_ATTR_MAPPED = 0x0001
_ATTR_XOR_MAPPED = 0x0020

# 混合国内外公共 STUN；前两个响应即可定型
STUN_SERVERS: list[tuple[str, int]] = [
    ("stun.miwifi.com", 3478),
    ("stun.chat.bilibili.com", 3478),
    ("stun.l.google.com", 19302),
    ("stun.cloudflare.com", 3478),
    ("stun.qq.com", 3478),
]

NAT_LABELS = {
    "open": "开放网络（无 NAT，联机无障碍）",
    "cone": "锥形 NAT（P2P 打洞顺畅，联机体验好）",
    "symmetric": "对称 NAT（打洞困难，联机可能要走中继，速度受影响）",
    "blocked": "UDP 不可用（防火墙拦截或没有网络）",
    "unknown": "无法判定（响应的服务器不足）",
}

NAT_ADVICE = {
    "open": "你的网络对联机完全友好，可以直接建房或加入。",
    "cone": "大多数情况能直连；偶尔失败换房主重试即可。",
    "symmetric": "建议由网络更好的一方建房；失败时会自动走公共中继，延迟略高。"
                 "可尝试开启路由器 UPnP 或改用手机热点。",
    "blocked": "请检查系统防火墙 / 安全软件是否拦截 UDP，或确认当前网络允许联网。",
    "unknown": "网络波动或 STUN 服务器暂不可达，稍后重试一次。",
}


def build_request(txid: bytes) -> bytes:
    """STUN Binding Request：20 字节头，无属性。"""
    if len(txid) != 12:
        raise ValueError("txid 必须是 12 字节")
    return struct.pack("!HHI", _BINDING_REQUEST, 0, MAGIC_COOKIE) + txid


def parse_response(data: bytes, txid: bytes) -> tuple[str, int] | None:
    """解析 Binding Response，返回 (公网 IP, 端口)；不匹配返回 None。

    优先 XOR-MAPPED-ADDRESS（0x0020），回退 MAPPED-ADDRESS（0x0001）。
    """
    if len(data) < 20:
        return None
    msg_type, msg_len, cookie = struct.unpack("!HHI", data[:8])
    if msg_type != _BINDING_RESPONSE or cookie != MAGIC_COOKIE or data[8:20] != txid:
        return None
    plain = None
    pos, end = 20, min(len(data), 20 + msg_len)
    while pos + 4 <= end:
        attr_type, attr_len = struct.unpack("!HH", data[pos:pos + 4])
        val = data[pos + 4:pos + 4 + attr_len]
        pos += 4 + attr_len + ((4 - attr_len % 4) % 4)  # 属性 4 字节对齐
        if len(val) < 8:
            continue
        family = val[1]
        port = struct.unpack("!H", val[2:4])[0]
        if attr_type == _ATTR_XOR_MAPPED:
            port ^= MAGIC_COOKIE >> 16
            if family == 0x01:
                raw = bytes(b ^ k for b, k in zip(val[4:8], struct.pack("!I", MAGIC_COOKIE)))
                return socket.inet_ntoa(raw), port
            if family == 0x02 and len(val) >= 20:
                key = struct.pack("!I", MAGIC_COOKIE) + txid
                raw = bytes(b ^ k for b, k in zip(val[4:20], key))
                return socket.inet_ntop(socket.AF_INET6, raw), port
        elif attr_type == _ATTR_MAPPED and plain is None:
            if family == 0x01:
                plain = (socket.inet_ntoa(val[4:8]), port)
            elif family == 0x02 and len(val) >= 20:
                plain = (socket.inet_ntop(socket.AF_INET6, val[4:20]), port)
    return plain


def stun_query(sock: socket.socket, host: str, port: int,
               timeout: float = 3.0, retries: int = 2) -> tuple[str, int] | None:
    """从给定 socket 向单个 STUN 服务器发 Binding，返回公网映射。"""
    for _ in range(max(1, retries)):
        txid = secrets.token_bytes(12)
        try:
            sock.settimeout(timeout)
            sock.sendto(build_request(txid), (host, port))
            while True:
                data, addr = sock.recvfrom(2048)
                # 只认来自目标服务器的响应，避免串包
                if addr[1] != port:
                    continue
                result = parse_response(data, txid)
                if result:
                    return result
                break
        except socket.timeout:
            continue
        except OSError:
            return None
    return None


def classify(results: list[tuple[str, int] | None],
             local_ip: str, local_port: int) -> str:
    """按各服务器返回的映射分类 NAT。纯函数，便于测试。"""
    seen = [r for r in results if r]
    if not seen:
        return "blocked"
    if len(seen) < 2:
        first = seen[0]
        if first[0] == local_ip and first[1] == local_port:
            return "open"
        return "unknown"
    first = seen[0]
    if any(r != first for r in seen[1:]):
        return "symmetric"
    if first[0] == local_ip and first[1] == local_port:
        return "open"
    return "cone"


def _local_outbound_ip() -> str:
    """本机默认路由出口 IP（UDP connect 不发包，仅查路由表）。"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("223.5.5.5", 53))
            return s.getsockname()[0]
        finally:
            s.close()
    except OSError:
        return ""


def check_ipv6() -> dict:
    """本机是否有可路由的全局 IPv6（connect 只查路由，不发包）。"""
    if not socket.has_ipv6:
        return {"supported": False, "address": ""}
    try:
        s = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
        try:
            s.connect(("2400:3200::1", 53))  # AliDNS，仅路由检查
            addr = s.getsockname()[0]
        finally:
            s.close()
    except OSError:
        return {"supported": False, "address": ""}
    if addr.startswith(("fe80", "::1")):
        return {"supported": False, "address": ""}
    return {"supported": True, "address": addr}


def detect_nat(servers: list[tuple[str, int]] | None = None,
               timeout: float = 3.0, want: int = 2) -> dict:
    """向多个 STUN 服务器询问映射并分类。"""
    servers = list(servers or STUN_SERVERS)
    results: list[tuple[str, int] | None] = []
    responded: list[str] = []
    mapped: list[str] = []
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    except OSError as e:
        utils.log.warning("网络检测创建 socket 失败: %s", e)
        return {"nat_type": "blocked", "public": "", "servers": []}
    try:
        sock.bind(("", 0))
        local_port = sock.getsockname()[1]
        ok = 0
        for host, port in servers:
            r = stun_query(sock, host, port, timeout=timeout)
            results.append(r)
            if r:
                ok += 1
                responded.append(f"{host}:{port}")
                mapped.append(f"{r[0]}:{r[1]}")
                if ok >= max(2, want):
                    break
    finally:
        sock.close()
    local_ip = _local_outbound_ip()
    nat = classify(results, local_ip, local_port)
    seen = [r for r in results if r]
    return {
        "nat_type": nat,
        "public": f"{seen[0][0]}:{seen[0][1]}" if seen else "",
        "servers": responded,
        "mappings": mapped,
    }


def diagnose(servers: list[tuple[str, int]] | None = None,
             timeout: float = 3.0) -> dict:
    """联机网络体检：NAT 类型 + IPv6，一次拿全。"""
    proxy_note = ""
    if os.environ.get("ALL_PROXY") or os.environ.get("all_proxy"):
        proxy_note = "检测走的是本机直连；启用代理不影响 UDP 打洞结果。"
    nat = detect_nat(servers, timeout=timeout)
    v6 = check_ipv6()
    kind = nat.get("nat_type") or "unknown"
    return {
        "nat_type": kind,
        "nat_label": NAT_LABELS.get(kind, NAT_LABELS["unknown"]),
        "advice": NAT_ADVICE.get(kind, NAT_ADVICE["unknown"]),
        "public": nat.get("public") or "",
        "servers": nat.get("servers") or [],
        "mappings": nat.get("mappings") or [],
        "ipv6": bool(v6.get("supported")),
        "ipv6_address": v6.get("address") or "",
        "note": proxy_note,
    }
