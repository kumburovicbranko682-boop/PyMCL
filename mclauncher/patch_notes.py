# -*- coding: utf-8 -*-
"""Minecraft 官方版本更新日志（对标 HMCL 下载页 patch notes）。

数据源是官方启动器同款 launchercontent.mojang.com：
v2/javaPatchNotes.json 是索引，正文按 contentPath 单独取（HTML）。
"""
from __future__ import annotations

BASE = "https://launchercontent.mojang.com"
INDEX_URL = f"{BASE}/v2/javaPatchNotes.json"


class PatchNoteError(Exception):
    pass


_index_cache = None


def fetch_index(dm, force=False) -> list:
    """更新日志索引（进程内缓存；索引 ~1MB，别每张卡片都拉一次）。"""
    global _index_cache
    if _index_cache is not None and not force:
        return _index_cache
    try:
        data = dm.fetch_json(INDEX_URL, timeout=(5, 20), expand=False)
    except Exception as e:
        raise PatchNoteError(f"获取更新日志索引失败: {e}")
    entries = (data or {}).get("entries") or []
    if not isinstance(entries, list):
        entries = []
    _index_cache = entries
    return entries


def _abs_url(u) -> str:
    u = str(u or "")
    return BASE + u if u.startswith("/") else u


def patch_note(dm, version: str) -> dict:
    """按版本号取官方更新日志；官方没写的版本抛 PatchNoteError。"""
    version = str(version or "").strip()
    if not version:
        raise PatchNoteError("没有版本号")
    entries = fetch_index(dm)
    entry = next((e for e in entries
                  if isinstance(e, dict) and str(e.get("version")) == version), None)
    if not entry:
        raise PatchNoteError(
            f"官方没有提供 {version} 的更新日志（远古版本与部分快照没有）")
    body = ""
    path = str(entry.get("contentPath") or "")
    if path:
        try:
            content = dm.fetch_json(f"{BASE}/v2/{path}", timeout=(5, 20), expand=False)
            body = str((content or {}).get("body") or "")
        except Exception as e:
            raise PatchNoteError(f"获取 {version} 更新日志正文失败: {e}")
    return {
        "version": version,
        "title": str(entry.get("title") or f"Minecraft {version}"),
        "type": str(entry.get("type") or ""),
        "image": _abs_url((entry.get("image") or {}).get("url")),
        "body_html": body,
    }
