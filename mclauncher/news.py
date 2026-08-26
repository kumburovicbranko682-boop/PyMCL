# -*- coding: utf-8 -*-
"""启动页新闻 + 每版本官方更新说明：Mojang launchercontent，失败走缓存。"""
from __future__ import annotations

from . import utils
from .downloader import DownloadManager

LAUNCHERCONTENT = "https://launchercontent.mojang.com"
URLS = [
    f"{LAUNCHERCONTENT}/v2/javaPatchNotes.json",
    f"{LAUNCHERCONTENT}/javaPatchNotes.json",
]
NEWS_URLS = [
    f"{LAUNCHERCONTENT}/news.json",
]
CONTENT_BASE = "https://launchercontent.mojang.com"
CACHE = utils.ROOT / "cache" / "news.json"
MAX_ROWS = 12
PATCH_INDEX_CACHE = utils.ROOT / "cache" / "patch_notes_index.json"
PATCH_DIR = utils.ROOT / "cache" / "patch_notes"


def _rows_from(payload) -> list:
    entries = []
    if isinstance(payload, dict):
        entries = payload.get("entries") or payload.get("patchNotes") or []
    elif isinstance(payload, list):
        entries = payload
    rows = []
    for item in entries[:MAX_ROWS]:
        if not isinstance(item, dict):
            continue
        title = item.get("title") or item.get("version") or item.get("id") or ""
        body = (item.get("shortText") or item.get("body") or item.get("subtitle")
                or item.get("text") or "")
        if isinstance(body, str) and len(body) > 160:
            body = body[:160] + "…"
        image = ""
        img = (item.get("image") or item.get("cardBackground")
               or item.get("newsPageImage") or item.get("playPageImage") or {})
        if isinstance(img, dict):
            image = img.get("url") or ""
        elif isinstance(img, str):
            image = img
        if image.startswith("/"):
            image = LAUNCHERCONTENT + image
        rows.append({
            "title": str(title),
            "body": str(body).strip(),
            "version": str(item.get("version") or item.get("id") or ""),
            "image": image,
            "date": str(item.get("date") or item.get("updated_at") or "")[:10],
        })
    return rows


def _news_rows(payload) -> list:
    """官方资讯 news.json：优先 Java 相关条目。"""
    entries = payload.get("entries") if isinstance(payload, dict) else payload
    entries = [e for e in (entries or []) if isinstance(e, dict)]
    java = [e for e in entries
            if any("java" in str(t).lower() for t in (e.get("newsType") or []))]
    return _rows_from(java or entries)


def _merge_rows(rows) -> list:
    """按 title/version 去重，总数限制在 MAX_ROWS。"""
    out, seen = [], set()
    for r in rows:
        key = (str(r.get("title") or "").strip().lower(),
               str(r.get("version") or "").strip())
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
        if len(out) >= MAX_ROWS:
            break
    return out


def load_cached() -> list:
    data = utils.read_json(CACHE, None)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return _rows_from(data)
    return []


def fetch(dm: DownloadManager | None = None) -> list:
    dm = dm or DownloadManager(threads=2)
    last = None
    rows = []
    for url in URLS:
        try:
            payload = dm.fetch_json(url, timeout=15)
            rows = _rows_from(payload)
            if rows:
                break
        except Exception as exc:
            last = exc
    for url in NEWS_URLS:
        try:
            payload = dm.fetch_json(url, timeout=15)
            rows = rows + _news_rows(payload)
            break
        except Exception as exc:
            last = last or exc
    merged = _merge_rows(rows)
    if merged:
        utils.write_json(CACHE, merged)
        return merged
    cached = load_cached()
    if cached:
        return cached
    if last:
        raise last
    return []


# ---------------------------------------------------------------------------
# 每版本官方更新说明（HMCL 安装页版本公告同款）
# ---------------------------------------------------------------------------

def _index_rows(payload) -> list:
    """javaPatchNotes.json 的完整条目（首页新闻只取前 12 条，这里全量）。"""
    entries = []
    if isinstance(payload, dict):
        entries = payload.get("entries") or payload.get("patchNotes") or []
    elif isinstance(payload, list):
        entries = payload
    rows = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        version = str(item.get("version") or item.get("id") or "").strip()
        path = str(item.get("contentPath") or "").strip()
        if not version or not path:
            continue
        image = ""
        img = item.get("image") or {}
        if isinstance(img, dict):
            image = str(img.get("url") or "")
        rows.append({
            "version": version,
            "title": str(item.get("title") or version),
            "type": str(item.get("type") or ""),
            "date": str(item.get("date") or "")[:10],
            "contentPath": path,
            "image": image,
        })
    return rows


def patch_note_index(dm: DownloadManager | None = None, force: bool = False) -> list:
    """全量版本说明索引，带本地缓存兜底。"""
    if not force:
        cached = utils.read_json(PATCH_INDEX_CACHE, None)
        if isinstance(cached, list) and cached:
            return cached
    dm = dm or DownloadManager(threads=2)
    last = None
    for url in URLS:
        try:
            rows = _index_rows(dm.fetch_json(url, timeout=15))
            if rows:
                utils.write_json(PATCH_INDEX_CACHE, rows)
                return rows
        except Exception as exc:
            last = exc
    cached = utils.read_json(PATCH_INDEX_CACHE, None)
    if isinstance(cached, list) and cached:
        return cached
    if last:
        raise last
    return []


def _note_cache_file(version: str):
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in version)
    return PATCH_DIR / f"{safe}.json"


def patch_note(version: str, dm: DownloadManager | None = None) -> dict:
    """某个游戏版本的官方更新说明（正文转纯文本），带缓存。

    返回 {version, title, type, date, body, image}；没有该版本的说明时
    body 为空串。"""
    version = str(version or "").strip()
    if not version:
        return {}
    cache_file = _note_cache_file(version)
    cached = utils.read_json(cache_file, None)
    if isinstance(cached, dict) and cached.get("body"):
        return cached
    dm = dm or DownloadManager(threads=2)
    entry = None
    for row in patch_note_index(dm):
        if str(row.get("version") or "").lower() == version.lower():
            entry = row
            break
    if entry is None:
        return {"version": version, "title": version, "type": "", "date": "",
                "body": "", "image": ""}
    path = str(entry.get("contentPath") or "").lstrip("/")
    payload = None
    last = None
    for base in (f"{CONTENT_BASE}/v2", CONTENT_BASE):
        try:
            payload = dm.fetch_json(f"{base}/{path}", timeout=15)
            if payload:
                break
        except Exception as exc:
            last = exc
    if not isinstance(payload, dict):
        if last:
            raise last
        payload = {}
    from .catalog_files import html_to_text
    body = html_to_text(payload.get("body") or "")
    out = {
        "version": version,
        "title": str(payload.get("title") or entry.get("title") or version),
        "type": str(entry.get("type") or ""),
        "date": str(entry.get("date") or ""),
        "body": body,
        "image": str(entry.get("image") or ""),
    }
    if body:
        utils.write_json(cache_file, out)
    return out
