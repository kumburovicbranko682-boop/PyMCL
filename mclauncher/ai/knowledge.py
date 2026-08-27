# -*- coding: utf-8 -*-
"""AI 助手的知识源：本地 FAQ 检索 + Minecraft Wiki 公开 API 查询。

wiki 用官方 MediaWiki action API（minecraft.wiki / zh.minecraft.wiki 均公开
提供），单次最多取 3 条摘要，带 UA、限速常识内使用，不抓页面 HTML。
"""

from __future__ import annotations

import requests

from mclauncher import help_content
from mclauncher.i18n import current_language

from .defaults import CLIENT_HEADER

_WIKI_API = {
    "zh": "https://zh.minecraft.wiki/api.php",
    "en": "https://minecraft.wiki/api.php",
}
_WIKI_PAGE = {
    "zh": "https://zh.minecraft.wiki/w/",
    "en": "https://minecraft.wiki/w/",
}
_TIMEOUT = 10
_MAX_EXTRACT = 900


def search_help(query: str, limit: int = 3) -> list[dict]:
    """本地 FAQ 关键词检索：命中标题权重高；中文按子串、英文按分词。"""
    q = (query or "").strip().lower()
    if not q:
        return [{"id": a["id"], "title": a["title"]} for a in help_content.ARTICLES]
    terms = [t for t in q.replace("，", " ").replace("？", " ").split() if t]
    if not terms:
        terms = [q]
    scored = []
    for art in help_content.ARTICLES:
        title = art["title"].lower()
        body = art["body"].lower()
        score = 0
        for t in terms:
            if t in title:
                score += 3
            if t in body:
                score += 1
            # 中文没空格分词：整句子串也算一档
            if len(t) >= 2:
                for i in range(len(t) - 1):
                    piece = t[i:i + 2]
                    if piece in title:
                        score += 2
                    elif piece in body:
                        score += 1
        if score > 0:
            scored.append((score, art))
    scored.sort(key=lambda x: -x[0])
    return [
        {"id": a["id"], "title": a["title"], "body": a["body"]}
        for _s, a in scored[:limit]
    ]


def _wiki_lang() -> str:
    lang = (current_language() or "zh_CN").lower()
    return "zh" if lang.startswith("zh") else "en"


def wiki_lookup(query: str, limit: int = 3) -> list[dict] | str:
    """查 Minecraft Wiki：返回 [{title, url, extract}]，失败返回一句话说明。"""
    q = (query or "").strip()
    if not q:
        return "关键词是空的"
    lang = _wiki_lang()
    api = _WIKI_API[lang]
    headers = {"User-Agent": f"{CLIENT_HEADER} (launcher assistant; wiki lookup)"}
    try:
        r = requests.get(api, params={
            "action": "opensearch", "search": q,
            "limit": max(1, min(int(limit or 3), 5)), "format": "json",
        }, headers=headers, timeout=_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        titles = list(data[1] or []) if isinstance(data, list) and len(data) > 1 else []
    except Exception as exc:
        return f"wiki 搜索失败: {exc}"
    if not titles:
        return f"wiki 上没搜到「{q}」"
    try:
        r = requests.get(api, params={
            "action": "query", "prop": "extracts", "exintro": 1,
            "explaintext": 1, "redirects": 1, "format": "json",
            "titles": "|".join(titles[:3]),
        }, headers=headers, timeout=_TIMEOUT)
        r.raise_for_status()
        pages = ((r.json().get("query") or {}).get("pages") or {})
    except Exception as exc:
        return f"wiki 摘要读取失败: {exc}"
    base = _WIKI_PAGE[lang]
    out = []
    for page in pages.values():
        title = page.get("title") or ""
        extract = (page.get("extract") or "").strip()
        if not title:
            continue
        out.append({
            "title": title,
            "url": base + title.replace(" ", "_"),
            "extract": extract[:_MAX_EXTRACT],
        })
    ordered = sorted(out, key=lambda row: titles.index(row["title"]) if row["title"] in titles else 99)
    return ordered or f"wiki 上没搜到「{q}」"
