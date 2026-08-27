# -*- coding: utf-8 -*-
"""服务器列表管理：与游戏内 servers.dat 双向互通。

游戏读写的 servers.dat（NBT）是唯一真实来源——启动器里加的服务器
进游戏直接可见，游戏里加的这里也能列出来。description 等游戏文件
放不下的字段存 servers.meta.json 侧车；旧版启动器私有的 servers.json
首次读取时自动并入 servers.dat 并改名备份。
"""
from __future__ import annotations

import gzip
import struct
from io import BytesIO
from pathlib import Path
from typing import Optional

from . import nbt, utils
from .instances import Instance

LEGACY_FILE = "servers.json"
DAT_FILE = "servers.dat"
META_FILE = "servers.meta.json"


class ServerError(Exception):
    pass


# ---------------------------------------------------------------- 地址

def _split_ip(text: str) -> tuple[str, int]:
    """servers.dat 的 ip 字段可能带端口（host:port / [v6]:port）。"""
    from .server_ping import PingError, parse_address
    try:
        host, port, _explicit = parse_address(text)
        return host, port
    except PingError:
        return str(text or "").strip(), 25565


def _join_ip(host: str, port: int) -> str:
    host = str(host or "").strip()
    port = int(port or 25565)
    if port == 25565:
        return host
    if ":" in host and not host.startswith("["):
        return f"[{host}]:{port}"
    return f"{host}:{port}"


def _addr_key(host: str, port: int) -> str:
    return f"{str(host or '').strip().lower()}:{int(port or 25565)}"


# ---------------------------------------------------------------- servers.dat

def read_dat_entries(path) -> list[dict]:
    """读 servers.dat 的原始条目（name/ip/icon/acceptTextures/hidden…）。"""
    p = Path(path)
    if not p.is_file():
        return []
    try:
        root = nbt.loads(p.read_bytes())
    except Exception:
        return []
    rows = root.get("servers")
    if not isinstance(rows, list):
        return []
    return [r for r in rows if isinstance(r, dict)]


def _w_str(buf: BytesIO, text: str):
    data = str(text or "").encode("utf-8")
    buf.write(struct.pack(">H", len(data)))
    buf.write(data)


def write_dat_entries(path, entries: list[dict]):
    """写 servers.dat。保留 icon / acceptTextures（游戏自己写的字段）。"""
    buf = BytesIO()
    buf.write(b"\x0a")
    _w_str(buf, "")
    buf.write(b"\x09")
    _w_str(buf, "servers")
    buf.write(b"\x0a")
    buf.write(struct.pack(">i", len(entries)))
    for e in entries:
        for key in ("name", "ip", "icon"):
            val = e.get(key)
            if val is not None and str(val) != "":
                buf.write(b"\x08")
                _w_str(buf, key)
                _w_str(buf, str(val))
        for key in ("acceptTextures", "hidden"):
            val = e.get(key)
            if val is not None:
                buf.write(b"\x01")
                _w_str(buf, key)
                buf.write(bytes([1 if val else 0]))
        buf.write(b"\x00")
    buf.write(b"\x00")
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(gzip.compress(buf.getvalue()))


# ---------------------------------------------------------------- 存储

def _dat_path(instance: Instance) -> Path:
    return instance.path / DAT_FILE


def _meta_path(instance: Instance) -> Path:
    return instance.path / META_FILE


def _load(instance: Instance) -> tuple[list[dict], dict]:
    """读 servers.dat + 侧车 meta；旧 servers.json 自动并入（一次性）。"""
    entries = read_dat_entries(_dat_path(instance))
    meta = utils.read_json(_meta_path(instance), {}) or {}
    if not isinstance(meta, dict):
        meta = {}
    legacy_path = instance.path / LEGACY_FILE
    if legacy_path.is_file():
        legacy = utils.read_json(legacy_path, None)
        changed = False
        if isinstance(legacy, list):
            existing = {_addr_key(*_split_ip(str(e.get("ip") or ""))) for e in entries}
            for s in legacy:
                if not isinstance(s, dict):
                    continue
                host = str(s.get("ip") or "").strip()
                if not host:
                    continue
                try:
                    port = int(s.get("port") or 25565)
                except (TypeError, ValueError):
                    port = 25565
                key = _addr_key(host, port)
                if key in existing:
                    continue
                entry = {"name": str(s.get("name") or host), "ip": _join_ip(host, port)}
                if s.get("hidden"):
                    entry["hidden"] = 1
                entries.append(entry)
                if s.get("description"):
                    meta[key] = {"description": str(s.get("description"))}
                existing.add(key)
                changed = True
        if changed:
            write_dat_entries(_dat_path(instance), entries)
            utils.write_json(_meta_path(instance), meta)
        try:
            legacy_path.rename(legacy_path.with_name(LEGACY_FILE + ".imported"))
        except OSError:
            pass
    return entries, meta


def _save(instance: Instance, entries: list[dict], meta: dict):
    write_dat_entries(_dat_path(instance), entries)
    utils.write_json(_meta_path(instance), meta)


def _normalize(entry: dict, meta: dict, index: int) -> dict:
    host, port = _split_ip(str(entry.get("ip") or ""))
    key = _addr_key(host, port)
    return {
        "name": str(entry.get("name") or host or f"服务器 #{index + 1}"),
        "ip": host,
        "port": port,
        "icon": str(entry.get("icon") or ""),
        "description": str((meta.get(key) or {}).get("description") or ""),
        "hidden": bool(entry.get("hidden")),
        "index": index,
    }


# ---------------------------------------------------------------- 公开 API

def list_servers(instance: Instance) -> list[dict]:
    """该实例 servers.dat 里的所有服务器（与游戏内多人列表一致）。

    每条记录：{name, ip, port, icon, description, hidden, index}
    """
    entries, meta = _load(instance)
    return [_normalize(e, meta, i) for i, e in enumerate(entries)]


def get_server(instance: Instance, index: int) -> Optional[dict]:
    for s in list_servers(instance):
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
    entries, meta = _load(instance)
    host = ip.strip()
    entry = {"name": (name or host).strip(), "ip": _join_ip(host, port)}
    if icon:
        entry["icon"] = icon
    entries.append(entry)
    if description.strip():
        meta[_addr_key(host, port)] = {"description": description.strip()}
    _save(instance, entries, meta)
    return _normalize(entry, meta, len(entries) - 1)


def update_server(instance: Instance, index: int, **kwargs) -> dict:
    entries, meta = _load(instance)
    if index < 0 or index >= len(entries):
        raise ServerError(f"服务器索引 {index} 不存在")
    entry = entries[index]
    host, port = _split_ip(str(entry.get("ip") or ""))
    old_key = _addr_key(host, port)
    if "name" in kwargs:
        entry["name"] = str(kwargs["name"]).strip()
    if "ip" in kwargs:
        new_ip = str(kwargs["ip"]).strip()
        if not new_ip:
            raise ServerError("服务器地址不能为空")
        host = new_ip
    if "port" in kwargs:
        port = int(kwargs["port"])
        if port < 1 or port > 65535:
            raise ServerError("端口号必须在 1-65535 之间")
    entry["ip"] = _join_ip(host, port)
    if "icon" in kwargs:
        entry["icon"] = str(kwargs["icon"])
    if "hidden" in kwargs:
        entry["hidden"] = 1 if kwargs["hidden"] else 0
    new_key = _addr_key(host, port)
    if new_key != old_key and old_key in meta:
        meta[new_key] = meta.pop(old_key)
    if "description" in kwargs:
        desc = str(kwargs["description"]).strip()
        if desc:
            meta[new_key] = {"description": desc}
        else:
            meta.pop(new_key, None)
    _save(instance, entries, meta)
    return _normalize(entry, meta, index)


def delete_server(instance: Instance, index: int):
    entries, meta = _load(instance)
    if index < 0 or index >= len(entries):
        raise ServerError(f"服务器索引 {index} 不存在")
    entry = entries.pop(index)
    host, port = _split_ip(str(entry.get("ip") or ""))
    meta.pop(_addr_key(host, port), None)
    _save(instance, entries, meta)


def import_servers_txt(instance: Instance, text: str) -> int:
    """从纯文本批量导入服务器，每行格式：
    - 服务器名\t地址:端口
    - 地址:端口
    - 地址
    空行和 # 注释行被忽略。
    """
    entries, meta = _load(instance)
    existing = {_addr_key(*_split_ip(str(e.get("ip") or ""))) for e in entries}
    imported = 0
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "\t" in line:
            name, addr_part = (p.strip() for p in line.split("\t", 1))
        else:
            name, addr_part = "", line
        host, port = _split_ip(addr_part)
        if not host:
            continue
        key = _addr_key(host, port)
        if key in existing:
            continue
        entries.append({"name": name or host, "ip": _join_ip(host, port)})
        existing.add(key)
        imported += 1
    if imported:
        _save(instance, entries, meta)
    return imported


def export_servers_txt(instance: Instance) -> str:
    """导出为纯文本格式。"""
    servers = list_servers(instance)
    lines = ["# PyMCL 服务器列表导出", f"# 共 {len(servers)} 个服务器", ""]
    for s in servers:
        addr = f"{s['ip']}:{s['port']}"
        if s["name"] and s["name"] != s["ip"]:
            lines.append(f"{s['name']}\t{addr}")
        else:
            lines.append(addr)
    return "\n".join(lines)


def import_servers_json(instance: Instance, data: list[dict]) -> int:
    """从 JSON 数组导入。"""
    if not isinstance(data, list):
        raise ServerError("导入数据必须是 JSON 数组")
    entries, meta = _load(instance)
    existing = {_addr_key(*_split_ip(str(e.get("ip") or ""))) for e in entries}
    imported = 0
    for item in data:
        if not isinstance(item, dict):
            continue
        raw = str(item.get("ip") or item.get("address") or "").strip()
        if not raw:
            continue
        # ip 里内嵌端口（"host:25566"）时拆开，避免叠成 "host:25566:25565"
        host, port_from_ip = _split_ip(raw)
        try:
            port = int(item.get("port") or port_from_ip or 25565)
        except (TypeError, ValueError):
            port = 25565
        key = _addr_key(host, port)
        if key in existing:
            continue
        entry = {"name": str(item.get("name") or host), "ip": _join_ip(host, port)}
        if item.get("icon"):
            entry["icon"] = str(item.get("icon"))
        entries.append(entry)
        if item.get("description"):
            meta[key] = {"description": str(item.get("description"))}
        existing.add(key)
        imported += 1
    if imported:
        _save(instance, entries, meta)
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
