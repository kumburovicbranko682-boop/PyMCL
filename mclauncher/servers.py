# -*- coding: utf-8 -*-
"""服务器列表管理：增删改查、批量导入导出。

数据存储：实例独立的 servers.json 文件，格式与官方启动器兼容。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from . import utils
from .instances import Instance

SERVER_FILE = "servers.json"


class ServerError(Exception):
    pass


def _server_path(instance: Instance) -> Path:
    return instance.path / SERVER_FILE


def _read_servers(instance: Instance) -> list[dict]:
    path = _server_path(instance)
    return utils.read_json(path, [])


def _write_servers(instance: Instance, servers: list[dict]):
    utils.write_json(_server_path(instance), servers)


def list_servers(instance: Instance) -> list[dict]:
    """返回该实例的所有服务器，格式与官方启动器 servers.json 兼容。

    每条记录：{name, ip, port, icon, description, hidden}
    """
    data = _read_servers(instance)
    if not isinstance(data, list):
        return []
    out = []
    for i, s in enumerate(data):
        if isinstance(s, dict):
            out.append(_normalize(s, i))
    return out


def _normalize(s: dict, index: int = 0) -> dict:
    return {
        "name": str(s.get("name") or s.get("ip", f"服务器 #{index + 1}")),
        "ip": str(s.get("ip", "")),
        "port": int(s.get("port") or 25565),
        "icon": str(s.get("icon", "")),
        "description": str(s.get("description", "")),
        "hidden": bool(s.get("hidden", False)),
        "index": index,
    }


def get_server(instance: Instance, index: int) -> Optional[dict]:
    servers = list_servers(instance)
    for s in servers:
        if s["index"] == index:
            return s
    return None


def add_server(instance: Instance, name: str, ip: str, port: int = 25565,
               description: str = "", icon: str = "") -> dict:
    if not ip or not ip.strip():
        raise ServerError("服务器地址不能为空")
    port = int(port) if port else 25565
    if port < 1 or port > 65535:
        raise ServerError("端口号必须在 1-65535 之间")
    servers = _read_servers(instance)
    entry = {
        "name": (name or ip).strip(),
        "ip": ip.strip(),
        "port": port,
        "icon": icon,
        "description": description.strip(),
    }
    servers.append(entry)
    _write_servers(instance, servers)
    return _normalize(entry, len(servers) - 1)


def update_server(instance: Instance, index: int, **kwargs) -> dict:
    servers = _read_servers(instance)
    if not isinstance(servers, list) or index < 0 or index >= len(servers):
        raise ServerError(f"服务器索引 {index} 不存在")
    entry = servers[index]
    if not isinstance(entry, dict):
        raise ServerError(f"服务器数据损坏: {index}")
    if "name" in kwargs:
        entry["name"] = str(kwargs["name"]).strip()
    if "ip" in kwargs:
        ip = str(kwargs["ip"]).strip()
        if not ip:
            raise ServerError("服务器地址不能为空")
        entry["ip"] = ip
    if "port" in kwargs:
        port = int(kwargs["port"])
        if port < 1 or port > 65535:
            raise ServerError("端口号必须在 1-65535 之间")
        entry["port"] = port
    if "description" in kwargs:
        entry["description"] = str(kwargs["description"]).strip()
    if "icon" in kwargs:
        entry["icon"] = str(kwargs["icon"])
    if "hidden" in kwargs:
        entry["hidden"] = bool(kwargs["hidden"])
    _write_servers(instance, servers)
    return _normalize(entry, index)


def delete_server(instance: Instance, index: int):
    servers = _read_servers(instance)
    if not isinstance(servers, list) or index < 0 or index >= len(servers):
        raise ServerError(f"服务器索引 {index} 不存在")
    servers.pop(index)
    _write_servers(instance, servers)


def import_servers_txt(instance: Instance, text: str) -> int:
    """从纯文本批量导入服务器，每行格式：
    - 服务器名\t地址:端口
    - 地址:端口
    - 地址
    空行和 # 注释行被忽略。
    """
    imported = 0
    servers = _read_servers(instance)
    existing_addrs = set()
    for s in servers:
        if isinstance(s, dict):
            addr = f"{s.get('ip', '')}:{s.get('port', 25565)}"
            existing_addrs.add(addr)

    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # 尝试解析: 名字\t地址:端口 或 地址:端口 或 地址
        if "\t" in line:
            parts = line.split("\t", 1)
            name = parts[0].strip()
            addr_part = parts[1].strip()
        else:
            name = ""
            addr_part = line

        # 解析地址和端口
        if ":" in addr_part:
            ip, port_str = addr_part.rsplit(":", 1)
            try:
                port = int(port_str)
            except ValueError:
                port = 25565
            ip = ip.strip()
        else:
            ip = addr_part
            port = 25565

        if not ip:
            continue
        addr = f"{ip}:{port}"
        if addr in existing_addrs:
            continue
        entry = {
            "name": name or ip,
            "ip": ip,
            "port": port,
        }
        servers.append(entry)
        existing_addrs.add(addr)
        imported += 1

    if imported > 0:
        _write_servers(instance, servers)
    return imported


def export_servers_txt(instance: Instance) -> str:
    """导出为纯文本格式。"""
    servers = list_servers(instance)
    lines = ["# PyMCL 服务器列表导出", f"# 共 {len(servers)} 个服务器", ""]
    for s in servers:
        name = s["name"]
        ip = s["ip"]
        port = s["port"]
        if name and name != ip:
            lines.append(f"{name}\t{ip}:{port}")
        else:
            lines.append(f"{ip}:{port}")
    return "\n".join(lines)


def import_servers_json(instance: Instance, data: list[dict]) -> int:
    """从 JSON 数组导入。"""
    if not isinstance(data, list):
        raise ServerError("导入数据必须是 JSON 数组")
    imported = 0
    servers = _read_servers(instance)
    existing_addrs = set()
    for s in servers:
        if isinstance(s, dict):
            existing_addrs.add(f"{s.get('ip', '')}:{s.get('port', 25565)}")

    for entry in data:
        if not isinstance(entry, dict):
            continue
        ip = str(entry.get("ip", "") or entry.get("address", "")).strip()
        if not ip:
            continue
        # ip 里内嵌端口（"host:25566"）时按 txt 导入的语义拆开，否则端口
        # 会叠加成 "host:25566:25565"，直连时把带冒号的整串当主机名用。
        port = entry.get("port")
        if ":" in ip:
            host, _, tail = ip.rpartition(":")
            try:
                embedded = int(tail)
            except ValueError:
                embedded = None
            if embedded is not None and host.strip():
                ip = host.strip()
                if not port:
                    port = embedded
        try:
            port = int(port or 25565)
        except (TypeError, ValueError):
            port = 25565
        addr = f"{ip}:{port}"
        if addr in existing_addrs:
            continue
        # 只落盘规范字段，别把用户粘贴的任意键写进 servers.json
        name = str(entry.get("name") or "").strip() or ip
        servers.append({"name": name, "ip": ip, "port": port})
        existing_addrs.add(addr)
        imported += 1

    if imported > 0:
        _write_servers(instance, servers)
    return imported


def export_servers_json(instance: Instance) -> list[dict]:
    """导出为 JSON 数组。"""
    out = []
    for s in list_servers(instance):
        out.append({
            "name": s["name"],
            "ip": s["ip"],
            "port": s["port"],
            "description": s["description"],
            "icon": s["icon"],
        })
    return out