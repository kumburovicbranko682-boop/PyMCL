# -*- coding: utf-8 -*-
"""局域网联机辅助：本机地址提示 + 发现「对局域网开放」的世界。

游戏开放局域网后每 1.5 秒向 UDP 组播组 224.0.2.60:4445 广播
``[MOTD]名字[/MOTD][AD]端口[/AD]``，官方客户端的「局域网游戏」
就是这么发现的。这里实现同样的监听，对齐 PCL2 大厅 / HMCL
对本机 LAN 世界的检测。
"""
from __future__ import annotations

import re
import socket
import struct
import time

LAN_GROUP = "224.0.2.60"
LAN_PORT = 4445
_MOTD_RE = re.compile(r"\[MOTD\](.*?)\[/MOTD\]", re.S)
_AD_RE = re.compile(r"\[AD\](.*?)\[/AD\]", re.S)


def local_ips() -> list:
    found = []
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = info[4][0]
            if ip and not ip.startswith("127.") and ip not in found:
                found.append(ip)
    except OSError:
        pass
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("223.5.5.5", 80))
        ip = sock.getsockname()[0]
        sock.close()
        if ip and ip not in found and not ip.startswith("127."):
            found.insert(0, ip)
    except OSError:
        pass
    return found or ["127.0.0.1"]


def lan_hint(port: int = 25565) -> str:
    from .i18n import _
    ips = local_ips()
    lines = [f"{ip}:{port}" for ip in ips]
    return _("房主在游戏里「对局域网开放」后，把下面地址发给好友：") + "\n" + "\n".join(lines)


def parse_lan_announcement(data: bytes, sender_ip: str):
    """解析游戏的局域网广播；不是合法广播返回 None。

    [AD] 老版本只有端口，1.16 附近部分版本是 ip:port；广播里的 ip
    经常是 0.0.0.0 或错误网卡，这里一律以 UDP 实际发送者地址为准。
    """
    text = (data or b"").decode("utf-8", "replace")
    ad = _AD_RE.search(text)
    if not ad:
        return None
    ad_val = ad.group(1).strip()
    if ":" in ad_val:
        ad_val = ad_val.rpartition(":")[2].strip()
    if not ad_val.isdigit():
        return None
    port = int(ad_val)
    if not 1 <= port <= 65535:
        return None
    m = _MOTD_RE.search(text)
    motd = m.group(1).strip() if m else ""
    return {
        "motd": motd or "Minecraft 局域网世界",
        "ip": str(sender_ip or ""),
        "port": port,
        "address": _addr(sender_ip, port),
    }


def _addr(ip, port) -> str:
    ip = str(ip or "")
    if ":" in ip and not ip.startswith("["):
        return f"[{ip}]:{port}"
    return f"{ip}:{port}"


def discover_lan_worlds(timeout: float = 3.0, port=None) -> list:
    """监听游戏的局域网广播，返回发现的世界（按 ip:port 去重）。

    [{"motd", "ip", "port", "address"}]。游戏每 1.5 秒广播一次，
    默认 3 秒窗口足够收到两轮。监听失败（端口被占等）返回空列表。
    """
    port = LAN_PORT if port is None else int(port)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("", int(port)))
    except OSError:
        sock.close()
        return []
    try:
        mreq = struct.pack("4s4s", socket.inet_aton(LAN_GROUP), socket.inet_aton("0.0.0.0"))
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    except OSError:
        pass  # 加组失败时本机回环广播仍可能收到
    sock.settimeout(0.4)
    found = {}
    deadline = time.monotonic() + max(0.5, float(timeout))
    try:
        while time.monotonic() < deadline:
            try:
                data, addr = sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            entry = parse_lan_announcement(data, addr[0])
            if entry:
                found[(entry["ip"], entry["port"])] = entry
    finally:
        sock.close()
    return list(found.values())
