# -*- coding: utf-8 -*-
"""已装资源包的展示元数据：pack.png 图标 / pack.mcmeta 描述 / 兼容的游戏版本。

对标 PCL2 的资源包管理页：列表显示包图标、描述和 pack_format 对应的
MC 版本段，而不是一排 zip 文件名。mcmeta 解析复用 saves.py 的
_read_pack_mcmeta（数据包与资源包是同一种包格式）。
"""

from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

from . import utils
from .saves import _read_pack_mcmeta

_MAX_ICON_BYTES = 4 * 1024 * 1024

# 资源包 pack_format → 正式版版本段（快照用的中间值不在表里，UI 退回只显示格式号）
_FORMAT_RANGES = {
    1: "1.6.1–1.8.9",
    2: "1.9–1.10.2",
    3: "1.11–1.12.2",
    4: "1.13–1.14.4",
    5: "1.15–1.16.1",
    6: "1.16.2–1.16.5",
    7: "1.17–1.17.1",
    8: "1.18–1.18.2",
    9: "1.19–1.19.2",
    11: "1.19.3",
    12: "1.19.4",
    13: "1.20–1.20.1",
    15: "1.20.2",
    18: "1.20.3–1.20.4",
    22: "1.20.5–1.20.6",
    34: "1.21–1.21.1",
    42: "1.21.2–1.21.3",
    46: "1.21.4",
    55: "1.21.5",
    63: "1.21.6",
    64: "1.21.7–1.21.8",
}


def format_mc_range(pack_format: int) -> str:
    """pack_format 对应的正式版版本段；未知格式返回空串。"""
    return _FORMAT_RANGES.get(int(pack_format or 0), "")


def icons_cache_dir() -> Path:
    return utils.ROOT / "cache" / "pack_icons"


def _extract_icon(pack: Path, icons_dir: Path) -> str:
    """把 pack.png 抽到缓存目录，返回本地路径；没有/太大/坏包返回空串。"""
    try:
        key = hashlib.sha1(str(pack.resolve()).encode("utf-8")).hexdigest()
        dest = icons_dir / f"{key}.png"
        if pack.is_dir():
            src = pack / "pack.png"
            if not src.is_file() or src.stat().st_size > _MAX_ICON_BYTES:
                return ""
            if dest.is_file() and dest.stat().st_mtime_ns >= src.stat().st_mtime_ns:
                return str(dest)
            data = src.read_bytes()
        else:
            if dest.is_file() and dest.stat().st_mtime_ns >= pack.stat().st_mtime_ns:
                return str(dest)
            with zipfile.ZipFile(pack) as zf:
                try:
                    info = zf.getinfo("pack.png")
                except KeyError:
                    return ""
                if info.file_size <= 0 or info.file_size > _MAX_ICON_BYTES:
                    return ""
                data = zf.read("pack.png")
        utils.ensure_dir(icons_dir)
        dest.write_bytes(data)
        return str(dest)
    except (OSError, zipfile.BadZipFile, RuntimeError):
        return ""


def list_pack_entries_at(packs_dir, icons_dir=None) -> list[dict]:
    """列出目录下的资源包（zip 或文件夹包）并附上展示元数据。

    返回 [{filename, is_dir, bytes, path, description, pack_format, mc_range, icon}]。
    坏包/无 mcmeta 的照样列出（描述为空、格式为 0），删除入口不能因为包坏了就消失。
    """
    packs_dir = Path(packs_dir)
    if not packs_dir.is_dir():
        return []
    icons_dir = Path(icons_dir) if icons_dir else icons_cache_dir()
    rows = []
    for p in sorted(packs_dir.iterdir(), key=lambda x: x.name.lower()):
        if p.is_file() and p.suffix.lower() != ".zip":
            continue
        if p.is_dir() and not (p / "pack.mcmeta").is_file():
            continue
        meta = _read_pack_mcmeta(p)
        if p.is_dir():
            size = sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
        else:
            size = p.stat().st_size
        fmt = int(meta.get("pack_format") or 0)
        rows.append({
            "filename": p.name,
            "is_dir": p.is_dir(),
            "bytes": size,
            "path": str(p),
            "description": meta.get("description") or "",
            "pack_format": fmt,
            "mc_range": format_mc_range(fmt),
            "icon": _extract_icon(p, icons_dir),
        })
    return rows


def list_instance_resourcepacks(instance) -> list[dict]:
    """某实例 resourcepacks 目录下的资源包（含元数据）。"""
    return list_pack_entries_at(Path(instance.path) / "resourcepacks")
