# -*- coding: utf-8 -*-
"""资源项目详情（对标 PCL2 / HMCL 的资源详情页）。

Modrinth：GET /project/{slug}（正文是 Markdown）。
CurseForge：GET /mods/{id} + /mods/{id}/description（正文是 HTML）。
统一輸出一个 UI 好渲染的结构，官方源失败自动轮询镜像。
"""
from __future__ import annotations

from .downloader import DownloadManager


class DetailError(Exception):
    pass


def project_detail(dm: DownloadManager, source_kind: str, ident,
                   api_key: str = "") -> dict:
    kind = str(source_kind or "").lower()
    if kind.startswith("curse"):
        return _curseforge_detail(dm, ident, api_key=api_key)
    return _modrinth_detail(dm, ident)


def _annotate_mod(detail: dict):
    """中文译名 + mcmod.cn 百科链接（对标 PCL2 详情页「百科」入口）。

    模组与整合包各有一份数据集，调用方按项目类型分派；
    数据集未加载时只触发后台预热，不阻塞详情弹窗。
    """
    from . import mod_translations
    mod_translations.annotate_hits([detail])


def _annotate_pack(detail: dict):
    from . import mod_translations
    mod_translations.annotate_pack_hits([detail])


# ---------------------------------------------------------------- Modrinth

def _modrinth_detail(dm: DownloadManager, slug) -> dict:
    from . import source
    from .mods import API_TIMEOUT
    slug = str(slug or "").strip()
    if not slug:
        raise DetailError("缺少项目标识")
    data = None
    last_err = None
    for base in source.modrinth_api_bases():
        try:
            data = dm.fetch_json(f"{base}/project/{slug}", timeout=API_TIMEOUT)
            break
        except Exception as e:
            last_err = e
    if data is None:
        raise DetailError(f"获取项目详情失败: {last_err}")
    ptype = data.get("project_type") or "mod"
    gallery = [{"url": g.get("url") or "", "title": g.get("title") or ""}
               for g in data.get("gallery") or [] if g.get("url")]
    versions = data.get("game_versions") or []
    detail = {
        "source": "modrinth",
        "id": str(data.get("id") or ""),
        "slug": data.get("slug") or slug,
        "name": data.get("title") or slug,
        "summary": data.get("description") or "",
        "body": data.get("body") or "",
        "body_format": "markdown",
        "icon_url": data.get("icon_url") or "",
        "downloads": int(data.get("downloads") or 0),
        "follows": int(data.get("followers") or 0),
        "categories": [str(c) for c in data.get("categories") or []],
        "loaders": [str(x) for x in data.get("loaders") or []],
        "game_versions": versions[-12:][::-1],
        "updated": str(data.get("updated") or "")[:10],
        "created": str(data.get("published") or "")[:10],
        "license": (data.get("license") or {}).get("id") or "",
        "client_side": data.get("client_side") or "",
        "server_side": data.get("server_side") or "",
        "gallery": gallery,
        "links": _clean_links({
            "project": f"https://modrinth.com/{ptype}/{data.get('slug') or slug}",
            "source": data.get("source_url"),
            "issues": data.get("issues_url"),
            "wiki": data.get("wiki_url"),
            "discord": data.get("discord_url"),
        }),
    }
    if ptype == "mod":
        _annotate_mod(detail)
    elif ptype == "modpack":
        _annotate_pack(detail)
    return detail


# ---------------------------------------------------------------- CurseForge

def _curseforge_detail(dm: DownloadManager, mod_id, api_key: str = "") -> dict:
    from .mods import API_TIMEOUT, _cf_api_headers, cf_api_bases
    mod_id = str(mod_id or "").strip()
    if not mod_id:
        raise DetailError("缺少项目标识")
    data = None
    last_err = None
    for base in cf_api_bases():
        try:
            resp = dm.fetch_json(f"{base}/mods/{mod_id}",
                                 headers=_cf_api_headers(api_key),
                                 timeout=API_TIMEOUT)
            data = resp.get("data") if isinstance(resp, dict) else None
            if data:
                break
        except Exception as e:
            last_err = e
    if not data:
        raise DetailError(f"获取项目详情失败: {last_err}")
    # 正文单独轮询：镜像多不支持 /description，无 key 时拿不到就回退 summary
    body = ""
    for base in cf_api_bases():
        try:
            desc = dm.fetch_json(f"{base}/mods/{mod_id}/description",
                                 headers=_cf_api_headers(api_key),
                                 timeout=API_TIMEOUT)
            body = str((desc or {}).get("data") or "")
            if body:
                break
        except Exception:
            continue
    links = data.get("links") or {}
    gallery = [{"url": s.get("url") or s.get("thumbnailUrl") or "",
                "title": s.get("title") or ""}
               for s in data.get("screenshots") or []
               if s.get("url") or s.get("thumbnailUrl")]
    versions: list[str] = []
    loaders: list[str] = []
    for idx in data.get("latestFilesIndexes") or []:
        gv = idx.get("gameVersion")
        if gv and gv not in versions:
            versions.append(str(gv))
        loader = idx.get("modLoader")
        loader_name = {1: "forge", 4: "fabric", 5: "quilt", 6: "neoforge"}.get(loader)
        if loader_name and loader_name not in loaders:
            loaders.append(loader_name)
    authors = ", ".join(a.get("name") or "" for a in data.get("authors") or [])
    detail = {
        "source": "curseforge",
        "id": str(data.get("id") or mod_id),
        "slug": data.get("slug") or "",
        "name": data.get("name") or f"#{mod_id}",
        "summary": data.get("summary") or "",
        "body": body,
        "body_format": "html",
        "icon_url": (data.get("logo") or {}).get("url") or "",
        "downloads": int(data.get("downloadCount") or 0),
        "follows": 0,
        "categories": [str(c.get("name") or "") for c in data.get("categories") or []],
        "loaders": loaders,
        "game_versions": versions[:12],
        "updated": str(data.get("dateModified") or "")[:10],
        "created": str(data.get("dateCreated") or "")[:10],
        "license": "",
        "client_side": "",
        "server_side": "",
        "author": authors,
        "gallery": gallery,
        "links": _clean_links({
            "project": links.get("websiteUrl"),
            "source": links.get("sourceUrl"),
            "issues": links.get("issuesUrl"),
            "wiki": links.get("wikiUrl"),
        }),
    }
    if data.get("classId") == 6:        # CF_CLASS_MOD
        _annotate_mod(detail)
    elif data.get("classId") == 4471:   # CF_CLASS_MODPACK
        _annotate_pack(detail)
    return detail


def _clean_links(links: dict) -> dict:
    return {k: str(v) for k, v in links.items() if v}
