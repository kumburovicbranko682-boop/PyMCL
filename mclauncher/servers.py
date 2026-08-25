# -*- coding: utf-8 -*-
"""服务器列表管理：增删改查、批量导入导出。

数据存储：实例的 servers.dat（NBT），也就是游戏「多人游戏」列表
真正读写的文件——在启动器里加的服务器进游戏立刻可见。
旧版 PyMCL 写的 servers.json 首次读取时自动迁移进 servers.dat。
"""
from __future__ import annotations

import json
import re
import struct
from pathlib import Path
from typing import Optional

from . import nbt_lite as nbt
from . import utils
from .instances import Instance

SERVER_FILE = "servers.json"   # 旧格式，仅迁移时读一次
SERVER_DAT = "servers.dat"     # 游戏真实读取的 NBT 列表


class ServerError(Exception):
    pass


def _dat_path(instance: Instance) -> Path:
    return instance.path / SERVER_DAT


def _json_path(instance: Instance) -> Path:
    return instance.path / SERVER_FILE


def _split_address(addr: str) -> tuple[str, int]:
    """servers.dat 的 ip 字段是 host[:port]；支持 [IPv6]:port 与裸 IPv6。"""
    addr = str(addr or "").strip()
    if addr.startswith("[") and "]" in addr:
        host, _, rest = addr.partition("]")
        host = host[1:]
        rest = rest.lstrip(":")
        return host, int(rest) if rest.isdigit() else 25565
    if addr.count(":") == 1:
        host, p = addr.rsplit(":", 1)
        if p.isdigit():
            return host, int(p)
    return addr, 25565


def _join_address(host: str, port) -> str:
    host = str(host or "").strip()
    try:
        port = int(port or 25565)
    except (TypeError, ValueError):
        port = 25565
    if port == 25565:
        return host
    if ":" in host and not host.startswith("["):
        return f"[{host}]:{port}"
    return f"{host}:{port}"


_KNOWN_NBT_KEYS = {"name", "ip", "icon", "hidden", "description"}


def _entry_from_nbt(comp: dict) -> dict:
    def val(key, default=""):
        tag = comp.get(key)
        return tag[1] if isinstance(tag, tuple) else default

    host, port = _split_address(val("ip"))
    return {
        "name": str(val("name")),
        "ip": host,
        "port": port,
        "icon": str(val("icon")),
        "description": str(val("description")),
        "hidden": bool(val("hidden", 0)),
        # 游戏写的其他字段（acceptTextures 等）原样保留
        "_extra": {k: v for k, v in comp.items() if k not in _KNOWN_NBT_KEYS},
    }


def _entry_to_nbt(entry: dict) -> dict:
    comp = dict(entry.get("_extra") or {})
    comp["name"] = (nbt.TAG_STRING, str(entry.get("name") or entry.get("ip") or ""))
    comp["ip"] = (nbt.TAG_STRING, _join_address(entry.get("ip"), entry.get("port")))
    if entry.get("icon"):
        comp["icon"] = (nbt.TAG_STRING, str(entry["icon"]))
    if entry.get("hidden"):
        comp["hidden"] = (nbt.TAG_BYTE, 1)
    if entry.get("description"):
        comp["description"] = (nbt.TAG_STRING, str(entry["description"]))
    return comp


def read_servers_dat(path: Path) -> list[dict]:
    """读任意 servers.dat（游戏格式），返回启动器内部条目列表。"""
    p = Path(path)
    if not p.is_file():
        return []
    try:
        _root_name, root = nbt.load(p)
    except (nbt.NBTError, OSError, struct.error) as e:
        utils.log.warning("servers.dat 解析失败（%s），按空列表处理", e)
        return []
    tag = root.get("servers")
    if not (isinstance(tag, tuple) and tag[0] == nbt.TAG_LIST):
        return []
    _elem_type, items = tag[1]
    return [_entry_from_nbt(c) for c in items if isinstance(c, dict)]


def write_servers_dat(path: Path, entries: list[dict]):
    """把条目列表写成游戏可读的 servers.dat（未压缩 NBT）。"""
    p = Path(path)
    items = [_entry_to_nbt(s) for s in entries if isinstance(s, dict)]
    root = {"servers": (nbt.TAG_LIST, (nbt.TAG_COMPOUND, items))}
    utils.ensure_dir(p.parent)
    nbt.dump(p, root)


def _read_servers(instance: Instance) -> list[dict]:
    dat = _dat_path(instance)
    if dat.is_file():
        return read_servers_dat(dat)
    # 迁移旧版 servers.json（一次性；此后以 servers.dat 为准）
    legacy = utils.read_json(_json_path(instance), [])
    if isinstance(legacy, list) and legacy:
        entries = [dict(s) for s in legacy if isinstance(s, dict)]
        _write_servers(instance, entries)
        utils.log.info("已把 %d 条服务器从 servers.json 迁移进 servers.dat", len(entries))
        return _read_servers(instance)
    return []


def _write_servers(instance: Instance, servers: list[dict]):
    write_servers_dat(_dat_path(instance), servers)


def list_servers(instance: Instance) -> list[dict]:
    """返回该实例的所有服务器（读游戏的 servers.dat）。

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
        ip = str(entry.get("ip", "")).strip()
        if not ip:
            continue
        port = int(entry.get("port") or 25565)
        addr = f"{ip}:{port}"
        if addr in existing_addrs:
            continue
        servers.append(entry)
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