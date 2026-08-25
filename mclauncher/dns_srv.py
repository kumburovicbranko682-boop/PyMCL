# -*- coding: utf-8 -*-
"""纯标准库 DNS SRV 查询（_minecraft._tcp）。

游戏直连时若地址没写端口，会先查 SRV 记录拿到真实的 主机:端口
（很多服务器用「好记域名」+ SRV 转发到带端口的节点）。PCL2 / HMCL
的状态查询都跟随 SRV；PyMCL 之前不查，导致这类服务器被误报离线。

只发 UDP 标准查询：先用系统 resolv.conf 里的解析器，失败再落到
公共 DNS（阿里 / 腾讯 / Google / Cloudflare）。
"""
from __future__ import annotations

import random
import socket
import struct

from . import utils

# resolv.conf 不可用时的兜底（国内优先）
PUBLIC_RESOLVERS = ("223.5.5.5", "119.29.29.29", "8.8.8.8", "1.1.1.1")
DNS_PORT = 53

_TYPE_SRV = 33
_CLASS_IN = 1


def system_resolvers() -> list[str]:
    """读 /etc/resolv.conf 的 nameserver（Windows 上没有该文件，返回空）。"""
    out = []
    try:
        with open("/etc/resolv.conf", "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                parts = line.split()
                if len(parts) >= 2 and parts[0] == "nameserver":
                    ip = parts[1].strip()
                    # 只用 IPv4 解析器，UDP 套接字按 AF_INET 建
                    try:
                        socket.inet_pton(socket.AF_INET, ip)
                    except OSError:
                        continue
                    out.append(ip)
    except OSError:
        pass
    return out


def _encode_name(name: str) -> bytes:
    out = bytearray()
    for label in name.strip(".").split("."):
        raw = label.encode("idna") if not label.isascii() else label.encode("ascii")
        if not raw or len(raw) > 63:
            raise ValueError(f"非法域名标签: {label!r}")
        out.append(len(raw))
        out += raw
    out.append(0)
    return bytes(out)


def build_query(name: str, txid: int) -> bytes:
    """标准递归查询：一个 SRV 问题。"""
    header = struct.pack(">HHHHHH", txid, 0x0100, 1, 0, 0, 0)
    return header + _encode_name(name) + struct.pack(">HH", _TYPE_SRV, _CLASS_IN)


def _read_name(data: bytes, offset: int) -> tuple[str, int]:
    """解析可能带压缩指针的域名，返回 (名字, 新 offset)。"""
    labels = []
    jumps = 0
    end = -1
    while True:
        if offset >= len(data):
            raise ValueError("DNS 响应被截断")
        length = data[offset]
        if length & 0xC0 == 0xC0:
            if offset + 1 >= len(data):
                raise ValueError("DNS 压缩指针被截断")
            pointer = ((length & 0x3F) << 8) | data[offset + 1]
            if end < 0:
                end = offset + 2
            offset = pointer
            jumps += 1
            if jumps > 32:
                raise ValueError("DNS 压缩指针成环")
            continue
        offset += 1
        if length == 0:
            break
        labels.append(data[offset:offset + length].decode("ascii", "replace"))
        offset += length
    return ".".join(labels), (end if end >= 0 else offset)


def parse_srv_response(data: bytes, txid: int) -> list[tuple[int, int, int, str]]:
    """解析响应，返回 [(priority, weight, port, target), ...]。"""
    if len(data) < 12:
        raise ValueError("DNS 响应太短")
    rid, flags, qdcount, ancount, _ns, _ar = struct.unpack(">HHHHHH", data[:12])
    if rid != txid:
        raise ValueError("DNS 响应 ID 不匹配")
    if flags & 0x8000 == 0:
        raise ValueError("不是 DNS 响应")
    rcode = flags & 0x000F
    if rcode:
        # NXDOMAIN(3)/其他错误：没有 SRV 记录，按空列表处理
        return []
    offset = 12
    for _ in range(qdcount):
        _name, offset = _read_name(data, offset)
        offset += 4  # QTYPE + QCLASS
    records = []
    for _ in range(ancount):
        _name, offset = _read_name(data, offset)
        if offset + 10 > len(data):
            raise ValueError("DNS 记录头被截断")
        rtype, rclass, _ttl, rdlength = struct.unpack(
            ">HHIH", data[offset:offset + 10])
        offset += 10
        rdata_end = offset + rdlength
        if rdata_end > len(data):
            raise ValueError("DNS 记录数据被截断")
        if rtype == _TYPE_SRV and rclass == _CLASS_IN and rdlength >= 7:
            priority, weight, port = struct.unpack(">HHH", data[offset:offset + 6])
            target, _ = _read_name(data, offset + 6)
            if target:
                records.append((priority, weight, port, target))
        offset = rdata_end
    return records


def query_srv(name: str, resolver: str, timeout: float = 1.5):
    """向单个解析器查一次 SRV。异常向上抛，由调用方换下一个解析器。"""
    txid = random.randrange(0x10000)
    packet = build_query(name, txid)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.settimeout(timeout)
        sock.sendto(packet, (resolver, DNS_PORT))
        data, _addr = sock.recvfrom(4096)
    finally:
        sock.close()
    return parse_srv_response(data, txid)


def resolve_minecraft_srv(host: str, timeout: float = 1.5):
    """查 _minecraft._tcp.<host> 的 SRV。命中返回 (目标主机, 端口)，否则 None。

    多条记录时选 priority 最小、weight 最大的一条（与游戏行为一致的近似）。
    """
    host = str(host or "").strip().rstrip(".")
    if not host or "." not in host:
        return None
    name = f"_minecraft._tcp.{host}"
    resolvers = []
    for r in system_resolvers() + list(PUBLIC_RESOLVERS):
        if r not in resolvers:
            resolvers.append(r)
    # 断网时每个解析器都要等超时，限制数量以免状态刷新拖太久
    for resolver in resolvers[:4]:
        try:
            records = query_srv(name, resolver, timeout)
        except Exception as exc:
            utils.log.debug("SRV 查询失败 %s @ %s: %s", name, resolver, exc)
            continue
        if not records:
            return None  # 权威回答「没有记录」，不用再问别的解析器
        records.sort(key=lambda r: (r[0], -r[1]))
        priority, weight, port, target = records[0]
        if target and 1 <= port <= 65535:
            return target, port
        return None
    return None


def is_ip_literal(host: str) -> bool:
    for family in (socket.AF_INET, socket.AF_INET6):
        try:
            socket.inet_pton(family, str(host or "").strip("[]"))
            return True
        except OSError:
            continue
    return False
