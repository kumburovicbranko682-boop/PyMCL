# -*- coding: utf-8 -*-
"""提取游戏资源（对标 PCL2 百宝箱）。

assets 目录里的对象按哈希存放（objects/xx/<hash>），玩家拿不到能直接
播放/阅读的文件。这里按 assets index 把音乐、音效、语言文件等还原成
真实文件名导出。
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from . import utils
from .manifest import resolve_inherits

# category -> (显示名, 前缀元组)。前缀按 index 里的资源名匹配。
CATEGORIES = {
    "music": ("音乐", ("minecraft/sounds/music/",)),
    "records": ("唱片", ("minecraft/sounds/records/",)),
    "sounds": ("全部音效", ("minecraft/sounds/",)),
    "lang": ("语言文件", ("minecraft/lang/",)),
    "icons": ("图标", ("icons/", "minecraft/textures/gui/title/",)),
    "all": ("全部", ()),
}


class AssetExtractError(Exception):
    pass


def _index_id(instance, version_id: str) -> str:
    vjson = instance.version_json(version_id)
    if not vjson:
        raise AssetExtractError(f"版本 {version_id} 未安装")
    try:
        resolved = resolve_inherits(vjson, lambda pid: instance.version_json(pid))
    except Exception:
        resolved = vjson
    idx = (resolved.get("assetIndex") or {}).get("id") or resolved.get("assets")
    if not idx:
        raise AssetExtractError(f"版本 {version_id} 没有资源索引信息")
    return str(idx)


def load_index(instance, version_id: str) -> dict:
    """读取版本对应的 assets index，返回 {资源名: {hash, size}}。"""
    idx = _index_id(instance, version_id)
    index_file = Path(instance.assets_dir()) / "indexes" / f"{idx}.json"
    if not index_file.is_file():
        raise AssetExtractError(
            f"资源索引 {idx}.json 不存在，请先修复该版本（版本页 → 修复）")
    try:
        data = json.loads(index_file.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise AssetExtractError(f"资源索引损坏: {index_file}") from exc
    objects = data.get("objects")
    if not isinstance(objects, dict) or not objects:
        raise AssetExtractError(f"资源索引 {idx}.json 里没有资源")
    return objects


def _object_path(instance, obj_hash: str) -> Path:
    return Path(instance.assets_dir()) / "objects" / obj_hash[:2] / obj_hash


def list_assets(instance, version_id: str, category: str = "music",
                query: str = "") -> list[dict]:
    """列出可提取资源：[{name, hash, size, present}]，按名称排序。"""
    prefixes = CATEGORIES.get(category, CATEGORIES["all"])[1]
    q = (query or "").strip().lower()
    rows = []
    for name, obj in load_index(instance, version_id).items():
        if prefixes and not any(name.startswith(p) for p in prefixes):
            continue
        if q and q not in name.lower():
            continue
        h = str((obj or {}).get("hash") or "")
        if not h:
            continue
        rows.append({
            "name": name,
            "hash": h,
            "size": int((obj or {}).get("size") or 0),
            "present": _object_path(instance, h).is_file(),
        })
    rows.sort(key=lambda r: r["name"])
    return rows


def extract_assets(instance, version_id: str, names: list, dest_dir,
                   on_progress=None) -> dict:
    """把选中的资源按真实文件名复制到 dest_dir，返回 {count, skipped, dest}。"""
    wanted = [str(n) for n in (names or []) if str(n).strip()]
    if not wanted:
        raise AssetExtractError("请选择要提取的资源文件")
    objects = load_index(instance, version_id)
    dest_root = Path(dest_dir)
    count, skipped = 0, []
    for i, name in enumerate(wanted):
        obj = objects.get(name)
        h = str((obj or {}).get("hash") or "")
        src = _object_path(instance, h) if h else None
        if not src or not src.is_file():
            skipped.append(name)
            continue
        if on_progress:
            on_progress(f"提取 {name}", i, len(wanted))
        dest = dest_root / name
        utils.ensure_dir(dest.parent)
        shutil.copy2(src, dest)
        count += 1
    if not count:
        raise AssetExtractError(
            "选中的资源文件本地都不存在，请先修复该版本补全 assets")
    return {"count": count, "skipped": skipped, "dest": str(dest_root)}
