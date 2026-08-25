# -*- coding: utf-8 -*-
"""原理图管理（HMCL 原理图管理界面同款）。

管理游戏目录 schematics/ 文件夹（Litematica 与 WorldEdit 的默认目录）：
列出、导入、删除、打开文件夹。.litematic / .schem / .schematic / .nbt
都是（gzip）NBT，顺手把名称/作者/尺寸/方块数等元数据也读出来展示。
"""
from __future__ import annotations

import shutil
from pathlib import Path

from . import nbt
from . import version_settings as vs
from .instances import Instance

FOLDER = "schematics"

# 后缀 → 格式名。.nbt 是原版结构方块导出的结构文件
FORMAT_LABELS = {
    ".litematic": "Litematica",
    ".schem": "WorldEdit (Sponge)",
    ".schematic": "WorldEdit (MCEdit)",
    ".nbt": "结构方块",
}
EXTS = tuple(FORMAT_LABELS)


class SchematicError(Exception):
    pass


def folder(instance: Instance, version_id: str = "") -> Path:
    """schematics 目录（版本隔离时跟随版本的游戏目录）。"""
    base = vs.game_dir(instance, version_id) if version_id else Path(instance.path)
    return base / FOLDER


def _safe_child(fold: Path, name: str) -> Path:
    fold = fold.resolve()
    target = (fold / name).resolve()
    if target.parent != fold:
        raise SchematicError(f"非法原理图名: {name}")
    return target


def _meta(path: Path) -> dict:
    """尽力解析原理图元数据；解析不了就只按普通文件展示。"""
    try:
        data = nbt.read_file(path)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    out = {}
    md = data.get("Metadata")
    if isinstance(md, dict):  # Litematica
        if md.get("Name"):
            out["title"] = str(md["Name"])
        if md.get("Author"):
            out["author"] = str(md["Author"])
        size = md.get("EnclosingSize")
        if isinstance(size, dict) and size.get("x") is not None:
            out["size"] = f'{size.get("x")}×{size.get("y")}×{size.get("z")}'
        if md.get("TotalBlocks") is not None:
            out["blocks"] = int(md["TotalBlocks"])
        if md.get("RegionCount") is not None:
            out["regions"] = int(md["RegionCount"])
        return out
    # WorldEdit（Sponge v2/v3 会包一层 Schematic）
    root = data.get("Schematic") if isinstance(data.get("Schematic"), dict) else data
    dims = [root.get(k) for k in ("Width", "Height", "Length")]
    if all(isinstance(d, int) for d in dims):
        out["size"] = f"{dims[0]}×{dims[1]}×{dims[2]}"
        return out
    # 原版结构方块：size 是 [x, y, z]
    size = data.get("size")
    if isinstance(size, list) and len(size) == 3:
        out["size"] = "×".join(str(v) for v in size)
        if data.get("author"):
            out["author"] = str(data["author"])
    return out


def list_schematics(instance: Instance, version_id: str = "") -> list[dict]:
    fold = folder(instance, version_id)
    if not fold.is_dir():
        return []
    rows = []
    for p in sorted(fold.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if not p.is_file() or p.suffix.lower() not in EXTS:
            continue
        row = {
            "name": p.name,
            "path": str(p),
            "bytes": p.stat().st_size,
            "mtime": int(p.stat().st_mtime),
            "format": FORMAT_LABELS.get(p.suffix.lower(), p.suffix),
            "title": "",
            "author": "",
            "size": "",
            "blocks": 0,
            "regions": 0,
        }
        row.update(_meta(p))
        rows.append(row)
    return rows[:500]


def import_schematics(instance: Instance, paths, version_id: str = "") -> list[str]:
    """把本地原理图文件拷进 schematics 目录；重名自动加序号，不覆盖。"""
    fold = folder(instance, version_id)
    fold.mkdir(parents=True, exist_ok=True)
    added = []
    for raw in paths or []:
        src = Path(raw).expanduser()
        if not src.is_file():
            raise SchematicError(f"文件不存在: {src}")
        if src.suffix.lower() not in EXTS:
            raise SchematicError(
                f"不支持的格式: {src.name}（支持 {'/'.join(EXTS)}）")
        dest = fold / src.name
        n = 2
        while dest.exists():
            dest = fold / f"{src.stem} ({n}){src.suffix}"
            n += 1
        shutil.copy2(src, dest)
        added.append(dest.name)
    return added


def delete_schematic(instance: Instance, name: str, version_id: str = "") -> str:
    """删除原理图（尽量移入系统回收站，可找回）。"""
    target = _safe_child(folder(instance, version_id), name)
    if not target.is_file():
        raise SchematicError(f"原理图不存在: {name}")
    from . import trash
    return trash.trash_or_delete(target)


def open_folder(instance: Instance, version_id: str = "") -> str:
    from .crash import open_path
    fold = folder(instance, version_id)
    fold.mkdir(parents=True, exist_ok=True)
    open_path(fold)
    return str(fold)
