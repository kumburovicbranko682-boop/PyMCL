# -*- coding: utf-8 -*-
"""目录项目文件列表：模组 / 整合包 / 光影 / 资源包 / 数据包 / 世界。"""
from __future__ import annotations

import re

from .downloader import DownloadManager
from . import mods as mods_mod

KIND_MR = {
    "mod": "mod",
    "modpack": "modpack",
    "shader": "shader",
    "resourcepack": "resourcepack",
    "datapack": "datapack",
}
KIND_CF = {
    "mod": mods_mod.CF_CLASS_MOD,
    "modpack": mods_mod.CF_CLASS_MODPACK,
    "shader": mods_mod.CF_CLASS_SHADER,
    "resourcepack": mods_mod.CF_CLASS_RESOURCEPACK,
    "datapack": mods_mod.CF_CLASS_DATAPACK,
    "world": 17,
}
CF_CLASS_WORLD = 17
_MC_VER = re.compile(r"^\d+\.\d+")
_LOADERS = {"forge", "fabric", "quilt", "neoforge", "rift", "liteloader", "cauldron", "nonspecific"}
# 类型筛选统一用 canonical key（英文），UI 层把下拉框的 key 传进来；
# 中文键只作为旧调用方的兼容入口保留，避免 tr() 翻译后查表失配。
TYPE_FACETS = {
    "optimization": ["optimization"],
    "technology": ["technology"],
    "magic": ["magic"],
    "adventure": ["adventure"],
    "survival": ["utility", "food"],
    "decoration": ["decoration"],
    "realistic": ["realistic"],
    "cartoon": ["cartoon"],
    "performance": ["performance"],
    "path-tracing": ["path-tracing", "pbr"],
    "16x": ["16x"],
    "32x": ["32x"],
    "64x": ["64x"],
    "modern": ["modern"],
    "animated": ["animated"],
    "skyblock": ["skyblock"],
    "creation": ["creation"],
}
_TYPE_ALIASES = {
    "优化": "optimization",
    "科技": "technology",
    "魔法": "magic",
    "冒险": "adventure",
    "生存": "survival",
    "装饰": "decoration",
    "写实": "realistic",
    "卡通": "cartoon",
    "高性能": "performance",
    "光追": "path-tracing",
    "现代风": "modern",
    "动态效果": "animated",
    "空岛": "skyblock",
    "创造": "creation",
}
# canonical key 在 CurseForge 结果里做客户端过滤时的展示名碎片。
CF_TYPE_TOKENS = {
    "optimization": ["performance", "optimization"],
    "technology": ["technology", "tech"],
    "magic": ["magic"],
    "adventure": ["adventure"],
    "survival": ["survival", "util", "food"],
    "decoration": ["decor"],
    "realistic": ["realistic"],
    "cartoon": ["cartoon", "animated"],
    "performance": ["performance"],
    "path-tracing": ["path tracing", "shader"],
    "16x": ["16x"],
    "32x": ["32x"],
    "64x": ["64x"],
    "modern": ["modern"],
    "animated": ["animated"],
    "skyblock": ["skyblock"],
    "creation": ["creation"],
}
CF_RELEASE = {1: "release", 2: "beta", 3: "alpha"}


def split_cf_game_versions(gvs) -> tuple[list[str], list[str]]:
    game, loaders = [], []
    for raw in gvs or []:
        s = str(raw or "").strip()
        if not s:
            continue
        low = s.lower()
        if low in _LOADERS:
            loaders.append(low)
        elif _MC_VER.match(s):
            game.append(s)
    return game, loaders


def _date(text) -> str:
    s = str(text or "")
    if not s:
        return ""
    return s[:10]


def _row(**kwargs) -> dict:
    return {
        "id": kwargs.get("id") or "",
        "name": kwargs.get("name") or "",
        "version_number": kwargs.get("version_number") or "",
        "filename": kwargs.get("filename") or "",
        "game_versions": list(kwargs.get("game_versions") or []),
        "loaders": list(kwargs.get("loaders") or []),
        "date": kwargs.get("date") or "",
        "downloads": int(kwargs.get("downloads") or 0),
        "size": int(kwargs.get("size") or 0),
        "release_type": kwargs.get("release_type") or "release",
        "source": kwargs.get("source") or "",
        "changelog": (kwargs.get("changelog") or "")[:400],
    }


def _source_of(extra: dict) -> str:
    s = str((extra or {}).get("source") or "").lower()
    if s.startswith("curse"):
        return "curseforge"
    if s.startswith("modrinth"):
        return "modrinth"
    if extra.get("id") and not extra.get("slug"):
        return "curseforge"
    if extra.get("slug"):
        return "modrinth"
    return "modrinth"


def _kind_of(extra: dict) -> str:
    k = str((extra or {}).get("kind") or extra.get("project_type") or "mod").lower()
    if k in KIND_CF or k in KIND_MR:
        return k
    return "mod"


def _game_version(extra: dict) -> str | None:
    gv = extra.get("game_version") or extra.get("mc_version") or extra.get("version") or ""
    gv = str(gv).strip()
    if not gv or gv.lower().startswith(("全部", "all")):
        return None
    return gv


def _loader(extra: dict) -> str | None:
    raw = extra.get("loader") or extra.get("loaders") or ""
    if isinstance(raw, (list, tuple)):
        raw = raw[0] if raw else ""
    s = str(raw).strip().lower()
    if not s or s in ("全部", "all", "任意"):
        return None
    return s


def list_project_files(dm: DownloadManager | None, extra: dict | None) -> list[dict]:
    extra = dict(extra or {})
    dm = dm or DownloadManager(threads=2)
    src = _source_of(extra)
    if src == "curseforge":
        return _list_cf(dm, extra)
    return _list_mr(dm, extra)


def _list_mr(dm, extra):
    slug = extra.get("slug") or extra.get("name")
    if not slug:
        return []
    gv = _game_version(extra)
    loader = _loader(extra)
    loaders = [loader] if loader else None
    versions = mods_mod.list_versions(dm, slug, game_version=gv, loaders=loaders)
    if not versions and (gv or loaders):
        versions = mods_mod.list_versions(dm, slug)
    rows = []
    for v in versions or []:
        f = mods_mod._primary_file(v) or {}
        rows.append(_row(
            id=v.get("id"),
            name=v.get("name") or v.get("version_number") or "",
            version_number=v.get("version_number") or "",
            filename=f.get("filename") or "",
            game_versions=v.get("game_versions") or [],
            loaders=v.get("loaders") or [],
            date=_date(v.get("date_published") or v.get("date")),
            downloads=v.get("downloads") or 0,
            size=f.get("size") or 0,
            release_type=v.get("version_type") or "release",
            source="modrinth",
            changelog=v.get("changelog") or "",
        ))
    return rows


def _list_cf(dm, extra):
    addon_id = extra.get("id")
    if not addon_id:
        slug = extra.get("slug")
        kind = _kind_of(extra)
        hit = mods_mod.cf_by_slug(
            dm, slug, class_id=KIND_CF.get(kind),
            api_key=extra.get("api_key"),
        ) if slug else None
        addon_id = (hit or {}).get("id")
    if not addon_id:
        return []
    files = mods_mod.cf_files(
        dm, addon_id, api_key=extra.get("api_key"),
        game_version=_game_version(extra), page_size=50,
    )
    rows = []
    loader = _loader(extra)
    for f in files or []:
        gvs = f.get("gameVersions") or []
        game, loaders = split_cf_game_versions(gvs)
        if loader and loader not in loaders and loaders:
            continue
        rid = f.get("id")
        rtype = f.get("releaseType")
        if isinstance(rtype, int):
            rtype = CF_RELEASE.get(rtype, "release")
        rows.append(_row(
            id=rid,
            name=f.get("displayName") or f.get("fileName") or str(rid),
            version_number=f.get("displayName") or f.get("fileName") or "",
            filename=f.get("fileName") or "",
            game_versions=game or [x for x in gvs if _MC_VER.match(str(x))],
            loaders=loaders,
            date=_date(f.get("fileDate")),
            downloads=f.get("downloadCount") or 0,
            size=f.get("fileLength") or f.get("fileSizeOnDisk") or 0,
            release_type=rtype or "release",
            source="curseforge",
        ))
    return rows


# ================================================================ 更新日志

_TAG_SCRIPT = re.compile(r"(?is)<\s*(script|style)[^>]*>.*?<\s*/\s*\1\s*>")
_TAG_LI = re.compile(r"(?i)<\s*li[^>]*>")
# </li> 不换行：<li> 已产生 "\n• "，两个都换会在列表项之间多出空行
_TAG_BREAK = re.compile(r"(?i)<\s*(?:br|/p|/div|/h[1-6]|/tr|/ul|/ol)\s*/?\s*>")
_TAG_ANY = re.compile(r"<[^>]+>")


def html_to_text(raw) -> str:
    """CurseForge 的更新日志是 HTML，转成可读纯文本（保留换行和列表符）。"""
    import html as html_lib
    s = str(raw or "")
    if not s:
        return ""
    s = _TAG_SCRIPT.sub("", s)
    s = _TAG_LI.sub("\n• ", s)
    s = _TAG_BREAK.sub("\n", s)
    s = _TAG_ANY.sub("", s)
    s = html_lib.unescape(s)
    out = []
    empty = 0
    for ln in (x.rstrip() for x in s.splitlines()):
        if not ln.strip():
            empty += 1
            if empty > 1:
                continue
        else:
            empty = 0
        out.append(ln.strip())
    return "\n".join(out).strip()


def fetch_changelog(dm: DownloadManager | None, extra: dict | None) -> str:
    """按需拉取某个文件/版本的完整更新日志。

    extra: {source, file_id | version_id, id / slug（CurseForge 需项目 id）,
    kind, api_key}。Modrinth 返回 changelog 原文（markdown），CurseForge
    的 HTML 转成纯文本。缺参数返回空串；网络失败抛异常由 UI 呈现。
    """
    extra = dict(extra or {})
    dm = dm or DownloadManager(threads=2)
    src = _source_of(extra)
    if src == "curseforge":
        addon_id = extra.get("id") or extra.get("addon_id")
        file_id = extra.get("file_id") or extra.get("version_id")
        if not addon_id and extra.get("slug"):
            hit = mods_mod.cf_by_slug(
                dm, extra["slug"], class_id=KIND_CF.get(_kind_of(extra)),
                api_key=extra.get("api_key"))
            addon_id = (hit or {}).get("id")
        if not addon_id or not file_id:
            return ""
        data = mods_mod._cf_fetch(
            dm, f"/mods/{addon_id}/files/{file_id}/changelog",
            api_key=extra.get("api_key"))
        raw = data.get("data") if isinstance(data, dict) else data
        return html_to_text(raw)

    vid = extra.get("version_id") or extra.get("file_id")
    if not vid:
        return ""
    from . import source
    last_err = None
    for base in source.modrinth_api_bases():
        try:
            data = dm.fetch_json(f"{base}/version/{vid}",
                                 timeout=mods_mod.API_TIMEOUT, expand=False)
            return str((data or {}).get("changelog") or "").strip()
        except Exception as e:
            last_err = e
    raise mods_mod.ModError(f"获取更新日志失败: {last_err}")


def type_key(label) -> str:
    """把 UI 传来的类型标签（canonical key / 中文 / 翻译文本）归一成 canonical key。"""
    s = str(label or "").strip().lower()
    if not s or s in ("全部", "all", "any"):
        return ""
    return _TYPE_ALIASES.get(s, s)


def category_facets(label) -> list[str]:
    key = type_key(label)
    if not key:
        return []
    return list(TYPE_FACETS.get(key) or [])


def cf_category_tokens(label) -> list[str]:
    """CurseForge 客户端过滤用的展示名碎片。"""
    key = type_key(label)
    if not key:
        return []
    return list(CF_TYPE_TOKENS.get(key) or [])


def search_projects(dm: DownloadManager | None, kind: str, query: str, source: str,
                    extra: dict | None = None) -> list[dict]:
    extra = extra or {}
    dm = dm or DownloadManager(threads=2)
    kind = (kind or "mod").lower()
    src = str(source or extra.get("source") or "").lower()
    gv = _game_version({**extra, "game_version": extra.get("game_version") or extra.get("version")})
    cats = category_facets(extra.get("category") or extra.get("type") or "")
    want_mr = src in ("", "全部", "all", "modrinth") and kind in KIND_MR
    want_cf = src in ("", "全部", "all") or src.startswith("curse")
    if src.startswith("modrinth"):
        want_cf = False
    if src.startswith("curse"):
        want_mr = False
    if kind == "world":
        want_mr = False
        want_cf = True
    rows = []
    q = (query or "").strip()
    sort = str(extra.get("sort") or "")
    offset = int(extra.get("offset") or 0)
    from .config import CONFIG
    api_key = CONFIG.get("curseforge_api_key")
    if want_mr:
        try:
            if kind == "mod":
                hits = mods_mod.search_mods(dm, q or " ", limit=30, game_version=gv,
                                            categories=cats, sort=sort, offset=offset)
            else:
                hits = mods_mod.search_modrinth_projects(
                    dm, q, KIND_MR[kind], limit=30, game_version=gv, categories=cats,
                    sort=sort, offset=offset)
            for h in hits:
                rows.append(_hit_row(h, "modrinth"))
        except Exception:
            pass
    if want_cf:
        try:
            hits = mods_mod.search_curseforge(
                dm, q or None, limit=30, api_key=api_key,
                class_id=KIND_CF.get(kind, mods_mod.CF_CLASS_MOD),
                game_version=gv,
                categories=cf_category_tokens(extra.get("category") or extra.get("type") or ""),
                sort=sort, offset=offset,
            )
            for h in hits:
                row = _hit_row(h, "curseforge")
                row["description"] = h.get("summary") or row["description"]
                rows.append(row)
        except Exception:
            pass
    return rows


def _hit_row(h: dict, default_source: str) -> dict:
    return {
        "name": h.get("title") or h.get("name") or "?",
        "author": h.get("author") or "?",
        "downloads": int(h.get("downloads") or 0),
        "id": h.get("id"),
        "slug": h.get("slug"),
        "source": h.get("source") or default_source,
        "description": h.get("description") or h.get("summary") or "",
        "tags": h.get("tags") or [],
        "updated": str(h.get("updated") or h.get("date_modified") or "")[:10],
    }


def pick_mr_version(versions, version_id):
    if version_id:
        for v in versions or []:
            if str(v.get("id")) == str(version_id):
                return v
    return (versions or [None])[0]
