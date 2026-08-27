# -*- coding: utf-8 -*-
"""已装模组的展示元数据：名称 / 版本 / 加载器 / 描述 / 作者 / 图标。

对标 HMCL 模组列表和 PCL2 的 Mod 管理页：列表里显示的是模组真名
（Just Enough Items）而不是文件名（jei-1.20.2-forge-16.0.2.jar）。

解析复用 `ai.conflict.inspect_jar`（同一套 Fabric / Quilt / Forge /
NeoForge / mcmod.info 解析，冲突扫描也在用），这层只加两件事：

1. 按 (大小, mtime) 的磁盘缓存 —— 几百个 jar 的目录第二次打开
   不再逐个解压读元数据；
2. 图标从 jar 里抽出来落到缓存目录，UI 直接当本地 PNG 文件用。
"""

from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

from . import utils
from .mods import list_mod_entries_at

CACHE_VERSION = 2
_MAX_DESCRIPTION = 1000
_MAX_ICON_BYTES = 4 * 1024 * 1024

# UI 展示会用到的元数据键（缓存里也存这些）
_META_KEYS = ("id", "name", "version", "loader", "description", "authors", "icon")


def cache_root() -> Path:
    return utils.ROOT / "cache" / "mod_info"


def _cache_file(root: Path) -> Path:
    return root / "meta.json"


def _load_cache(root: Path) -> dict:
    data = utils.read_json(_cache_file(root), None)
    if not isinstance(data, dict) or data.get("version") != CACHE_VERSION:
        return {"version": CACHE_VERSION, "mods": {}}
    if not isinstance(data.get("mods"), dict):
        data["mods"] = {}
    return data


def _save_cache(root: Path, data: dict):
    try:
        utils.ensure_dir(root)
        utils.write_json(_cache_file(root), data)
    except OSError as e:
        utils.log.warning("写入模组元数据缓存失败: %s", e)


def _signature(path: Path) -> str:
    st = path.stat()
    return f"{st.st_size}:{st.st_mtime_ns}"


def _display_fallback(filename: str) -> str:
    """没有元数据时的显示名：去掉 .disabled / .jar / .litemod 后缀。"""
    name = filename
    for suffix in (".disabled", ".jar", ".litemod"):
        if name.lower().endswith(suffix):
            name = name[: -len(suffix)]
    return name or filename


def _extract_icon(jar_path: Path, inner: str, icons_dir: Path) -> str:
    """把 jar 里的图标抽到缓存目录，返回本地路径；抽不出来返回空串。"""
    inner = str(inner or "").strip().lstrip("/")
    if not inner or ".." in inner.split("/"):
        return ""
    key = hashlib.sha1(f"{jar_path.resolve()}|{inner}".encode("utf-8")).hexdigest()
    dest = icons_dir / f"{key}.png"
    try:
        if dest.is_file() and dest.stat().st_mtime_ns >= jar_path.stat().st_mtime_ns:
            return str(dest)
        with zipfile.ZipFile(jar_path) as zf:
            try:
                info = zf.getinfo(inner)
            except KeyError:
                return ""
            if info.file_size <= 0 or info.file_size > _MAX_ICON_BYTES:
                return ""
            data = zf.read(inner)
        utils.ensure_dir(icons_dir)
        dest.write_bytes(data)
        return str(dest)
    except (OSError, zipfile.BadZipFile, RuntimeError):
        return ""


def _inspect(path: Path) -> dict:
    from .ai.conflict import inspect_jar
    raw = inspect_jar(path)
    meta = {k: raw.get(k) for k in _META_KEYS}
    meta["name"] = str(meta.get("name") or "").strip()
    meta["version"] = str(meta.get("version") or "").strip()
    meta["loader"] = str(meta.get("loader") or "unknown").strip()
    desc = " ".join(str(meta.get("description") or "").split())
    meta["description"] = desc[:_MAX_DESCRIPTION]
    meta["authors"] = [str(a) for a in (meta.get("authors") or [])]
    meta["icon"] = str(meta.get("icon") or "").strip()
    return meta


def describe_mods_at(mods_dir, cache_dir=None) -> list[dict]:
    """列出 mods 目录并附上展示元数据。

    返回 list_mod_entries_at 的行（filename / enabled / bytes / path），
    每行追加 name / version / loader / description / authors / icon。
    icon 是抽到缓存目录的本地 PNG 路径，没有图标为空串。
    解析失败（损坏 jar、无元数据）时 name 回退为去后缀的文件名。
    """
    rows = list_mod_entries_at(mods_dir)
    if not rows:
        return rows
    root = Path(cache_dir) if cache_dir else cache_root()
    icons_dir = root / "icons"
    cache = _load_cache(root)
    known: dict = cache["mods"]
    dirty = False

    for row in rows:
        path = Path(row["path"])
        try:
            sig = _signature(path)
        except OSError:
            sig = ""
        key = str(path.resolve())
        hit = known.get(key)
        if sig and isinstance(hit, dict) and hit.get("sig") == sig:
            meta = {k: hit.get(k) for k in _META_KEYS}
        else:
            meta = _inspect(path)
            if sig:
                known[key] = {"sig": sig, **meta}
                dirty = True

        inner_icon = str(meta.get("icon") or "")
        icon_path = _extract_icon(path, inner_icon, icons_dir) if inner_icon else ""

        stem = _display_fallback(row["filename"])
        name = str(meta.get("name") or "").strip()
        # 解析器兜底会把 name 设成 path.stem；对禁用文件那是 "foo.jar"，不好看
        if not name or name == path.stem:
            name = stem
        modid = str(meta.get("id") or "").strip()
        row.update({
            "name": name,
            "modid": "" if modid == path.stem else modid,
            "version": str(meta.get("version") or ""),
            "loader": str(meta.get("loader") or "unknown"),
            "description": str(meta.get("description") or ""),
            "authors": [str(a) for a in (meta.get("authors") or [])],
            "icon": icon_path,
        })

    # 清掉已不存在的文件，缓存不随卸载无限膨胀
    stale = [k for k in known if not Path(k).is_file()]
    for k in stale:
        known.pop(k, None)
        dirty = True
    if dirty:
        _save_cache(root, cache)
    # mcmod.cn 中文名 + 百科链接（HMCL 同款；数据未加载时是无操作）
    from . import mod_translate
    return mod_translate.annotate_local(rows)


def export_mod_list(mods_dir, dest, fmt: str = "markdown", title: str = "") -> str:
    """把已装模组清单写成可分享的 Markdown 表格或纯文本（对标 HMCL 导出模组列表）。

    fmt: "markdown" 或 "text"。返回写出的文件路径。
    """
    rows = describe_mods_at(mods_dir)
    dest = Path(dest)
    utils.ensure_dir(dest.parent)
    header = title or Path(str(mods_dir)).parent.name
    lines: list[str] = []
    if fmt == "text":
        lines.append(f"# 模组清单 · {header}（共 {len(rows)} 个）")
        for r in rows:
            state = "" if r.get("enabled", True) else "（已禁用）"
            ver = f" {r['version']}" if r.get("version") else ""
            lines.append(f"{r['name']}{ver}{state}  [{r['filename']}]")
    else:
        lines.append(f"# 模组清单 · {header}")
        lines.append("")
        lines.append(f"共 {len(rows)} 个模组")
        lines.append("")
        lines.append("| 名称 | 版本 | 加载器 | 文件 | 状态 |")
        lines.append("| --- | --- | --- | --- | --- |")
        for r in rows:
            state = "启用" if r.get("enabled", True) else "禁用"
            loader = r.get("loader") or ""
            if loader == "unknown":
                loader = ""
            cells = [str(r.get("name") or ""), str(r.get("version") or ""),
                     loader, str(r.get("filename") or ""), state]
            lines.append("| " + " | ".join(c.replace("|", "\\|") for c in cells) + " |")
    dest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(dest)
