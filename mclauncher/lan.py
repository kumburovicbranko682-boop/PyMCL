# -*- coding: utf-8 -*-
"""本机局域网地址，给 PCL 风格「把 IP 发给好友」。"""
from __future__ import annotations

import socket


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
