# -*- coding: utf-8 -*-
"""列出某 MC 版本可用的加载器构建号，供安装向导选择。

每行 dict 至少含 id / label / stable；Forge 额外带 recommended / latest
（来自官方 promotions，PCL2 / HMCL 的「推荐版 / 最新版」同款标注）。
"""
from __future__ import annotations

from .downloader import DownloadManager
from .installer import (
    BMCLAPI, FABRIC_META, FORGE_MAVEN, NEOFORGE_MAVEN, QUILT_META,
    bmcl_forge_artifacts, forge_sort_key, parse_maven_versions,
    split_forge_artifact,
)

NEOFORGE_MC_MAP = {
    "1.20.1": "47.1", "1.20.2": "20.2", "1.20.3": "20.3", "1.20.4": "20.4",
    "1.20.5": "20.5", "1.20.6": "20.6", "1.21": "21.0", "1.21.1": "21.1",
}

FORGE_PROMOS_URL = ("https://files.minecraftforge.net"
                    "/net/minecraftforge/forge/promotions_slim.json")


def forge_promos(dm: DownloadManager | None = None) -> dict[str, str]:
    """Forge 官方推荐/最新构建表。

    返回 ``{"1.20.1-recommended": "47.2.0", "1.20.1-latest": "47.3.0", ...}``。
    官方 promotions_slim.json 失败时回退 BMCLAPI ``/forge/promos``
    （列表格式，name + build.version），全失败返回空 dict——
    标注只是锦上添花，绝不拖垮版本列表本身。
    """
    dm = dm or DownloadManager(threads=2)
    try:
        data = dm.fetch_json(FORGE_PROMOS_URL, timeout=20, expand=False)
        promos = (data or {}).get("promos")
        if isinstance(promos, dict):
            return {str(k): str(v) for k, v in promos.items() if v}
    except Exception:
        pass
    try:
        rows = dm.fetch_json(f"{BMCLAPI}/forge/promos", timeout=20)
        out: dict[str, str] = {}
        for r in rows or []:
            name = str((r or {}).get("name") or "")
            ver = str(((r or {}).get("build") or {}).get("version") or "")
            if name and ver:
                out[name] = ver
        return out
    except Exception:
        return {}


def list_loader_versions(dm: DownloadManager | None, mc_version: str, loader: str) -> list[dict]:
    dm = dm or DownloadManager(threads=2)
    mc = (mc_version or "").strip()
    kind = (loader or "").strip().lower()
    if not mc or kind in ("", "无", "none"):
        return []
    if kind == "fabric":
        return _fabric(dm, mc)
    if kind == "quilt":
        return _quilt(dm, mc)
    if kind == "forge":
        return _forge(dm, mc)
    if kind == "neoforge":
        return _neoforge(dm, mc)
    if kind == "optifine":
        from . import optifine as optifine_mod
        rows = optifine_mod.list_builds(dm, mc)
        return [{
            "id": f"{r['type']}_{r['patch']}".rstrip("_"),
            "label": f"{r['type']} {r['patch']}".strip(),
            "type": r.get("type") or "",
            "patch": r.get("patch") or "",
            "stable": True,
        } for r in rows]
    if kind == "liteloader":
        from . import liteloader as ll
        vers = ll.list_versions(dm) or {}
        if mc in vers:
            return [{"id": mc, "label": mc, "stable": True}]
        return []
    if kind == "cleanroom":
        from . import cleanroom as cr
        if mc != cr.MC_VERSION:
            return []
        return cr.list_versions(dm)
    return []


def _fabric(dm, mc):
    data = dm.fetch_json(f"{FABRIC_META}/versions/loader/{mc}", timeout=30)
    rows = []
    for d in data or []:
        ver = ((d or {}).get("loader") or {}).get("version")
        if not ver:
            continue
        rows.append({
            "id": ver,
            "label": ver,
            "stable": bool((d.get("loader") or {}).get("stable")),
        })
    return rows


def _quilt(dm, mc):
    data = dm.fetch_json(f"{QUILT_META}/versions/loader/{mc}", timeout=30)
    rows = []
    for d in data or []:
        ver = ((d or {}).get("loader") or {}).get("version")
        if not ver:
            continue
        rows.append({"id": ver, "label": ver, "stable": True})
    return rows


def _forge(dm, mc):
    found = []
    try:
        data = dm.fetch_json(f"{BMCLAPI}/forge/minecraft/{mc}", timeout=30)
        found = bmcl_forge_artifacts(data, mc)
    except Exception:
        found = []
    if not found:
        for url, expand in (
            (f"{BMCLAPI}/maven/net/minecraftforge/forge/maven-metadata.xml", True),
            (f"{FORGE_MAVEN}/maven-metadata.xml", False),
        ):
            try:
                xml = dm.fetch_text(url, timeout=40, expand=expand)
            except Exception:
                continue
            vers = parse_maven_versions(xml)
            found = [v for v in vers if v == mc or v.startswith(mc + "-")]
            if found:
                break
    found.sort(key=lambda v: forge_sort_key(v, mc), reverse=True)
    promos = forge_promos(dm)
    recommended = promos.get(f"{mc}-recommended") or ""
    latest = promos.get(f"{mc}-latest") or ""
    rows = []
    for v in found:
        _mc, build, _branch = split_forge_artifact(v, mc)
        rows.append({
            "id": v,
            "label": v,
            "stable": "-pre" not in v.lower(),
            "recommended": bool(recommended) and build == recommended,
            "latest": bool(latest) and build == latest,
        })
    return rows


def _neoforge(dm, mc):
    prefix = NEOFORGE_MC_MAP.get(mc)
    rows = []
    try:
        xml = dm.fetch_text(f"{NEOFORGE_MAVEN}/maven-metadata.xml", timeout=30, expand=False)
        vers = parse_maven_versions(xml)
    except Exception:
        vers = []
    if prefix:
        vers = [v for v in vers if str(v).startswith(str(prefix))]
    vers = list(reversed(vers[-80:])) if vers else []
    for v in vers:
        rows.append({"id": v, "label": v, "stable": "beta" not in str(v).lower()})
    return rows
