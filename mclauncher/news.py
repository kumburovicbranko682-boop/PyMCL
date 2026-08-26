# -*- coding: utf-8 -*-
"""启动页新闻：Mojang launchercontent 补丁笔记 + 官方资讯，失败走缓存。"""
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
CACHE = utils.ROOT / "cache" / "news.json"
MAX_ROWS = 12


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
