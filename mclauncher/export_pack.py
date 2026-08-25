# -*- coding: utf-8 -*-
"""整合包导出：.mrpack（Modrinth）与 CurseForge 格式 zip（manifest.json）。

能解析到对应平台的模组走 files 清单，其余文件进 overrides。"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

from . import utils
from .downloader import DownloadManager
from .instances import Instance
MODRINTH_HASH = "https://api.modrinth.com/v2/version_file/{hash}"
OVERRIDE_DIRS = ("config", "resourcepacks", "shaderpacks", "datapacks")


def _sha1(path: Path) -> str:
    return utils.sha1_file(path)


def export_mrpack(instance: Instance, dest: str | Path, dm: DownloadManager | None = None,
                  on_note=None) -> str:
    inst = instance
    dest = Path(dest)
    utils.ensure_dir(dest.parent)
    dm = dm or DownloadManager(threads=4)
    mods_dir = inst.path / "mods"
    files = []
    overrides = []
    if mods_dir.is_dir():
        jars = [p for p in mods_dir.iterdir() if p.is_file() and p.name.lower().endswith(".jar")]
        for i, jar in enumerate(jars):
            if on_note:
                on_note(f"解析模组 {jar.name}", i, len(jars))
            digest = _sha1(jar)
            hit = None
            try:
                hit = dm.fetch_json(MODRINTH_HASH.format(hash=digest), timeout=15)
            except Exception:
                hit = None
            if isinstance(hit, dict):
                primary = None
                for f in hit.get("files") or []:
                    if f.get("primary") or not primary:
                        primary = f
                if primary and primary.get("url"):
                    files.append({
                        "path": f"mods/{jar.name}",
                        "hashes": {"sha1": digest, "sha512": (primary.get("hashes") or {}).get("sha512") or ""},
                        "downloads": [primary["url"]],
                        "fileSize": int(primary.get("size") or jar.stat().st_size),
                    })
                    continue
            overrides.append(("mods/" + jar.name, jar))
    overrides += _collect_overrides(inst)

    meta = _pack_meta(inst)
    deps = {"minecraft": meta["mc_version"]} if meta["mc_version"] else {}
    if meta["loader"]:
        deps[meta["loader"]] = meta["loader_version"]
    index = {
        "formatVersion": 1,
        "game": "minecraft",
        "versionId": meta["version"],
        "name": meta["name"],
        "summary": f"由 PyMCL 从实例 {inst.name} 导出",
        "files": [f for f in files if f.get("hashes", {}).get("sha1")],
        "dependencies": {k: v for k, v in deps.items() if v},
    }
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("modrinth.index.json", json.dumps(index, ensure_ascii=False, indent=2))
        for rel, path in overrides:
            zf.write(path, "overrides/" + rel)
    if on_note:
        on_note("导出完成", 1, 1)
    return str(dest)


def _collect_overrides(inst: Instance) -> list:
    out = []
    for folder in OVERRIDE_DIRS:
        src = inst.path / folder
        if not src.is_dir():
            continue
        for p in src.rglob("*"):
            if p.is_file():
                rel = Path(folder) / p.relative_to(src)
                out.append((str(rel).replace("\\", "/"), p))
    return out


def _pack_meta(inst: Instance) -> dict:
    meta = inst.meta() or {}
    pack = meta.get("modpack") if isinstance(meta.get("modpack"), dict) else {}
    return {
        "name": pack.get("name") or inst.name,
        "version": str(pack.get("version") or "1.0.0"),
        "author": str(pack.get("author") or ""),
        "mc_version": pack.get("mc_version") or meta.get("mc_version") or "",
        "loader": str(pack.get("loader") or "").lower(),
        "loader_version": str(pack.get("loader_version") or ""),
    }


def export_cf_zip(instance: Instance, dest: str | Path, dm: DownloadManager | None = None,
                  on_note=None) -> str:
    """导出 CurseForge 格式整合包（manifest.json + overrides）。

    mods 目录里的 jar 通过 CurseForge 指纹接口批量匹配成
    {projectID, fileID}；匹配不上的（Modrinth 独占、自打包等）原样进
    overrides/mods，导入方仍能完整还原。
    """
    from .mods import cf_fingerprint, cf_match_fingerprints
    inst = instance
    dest = Path(dest)
    utils.ensure_dir(dest.parent)
    dm = dm or DownloadManager(threads=4)
    mods_dir = inst.path / "mods"
    jars = []
    if mods_dir.is_dir():
        jars = [p for p in sorted(mods_dir.iterdir())
                if p.is_file() and p.name.lower().endswith(".jar")]
    fps = {}
    for i, jar in enumerate(jars):
        if on_note:
            on_note(f"计算指纹 {jar.name}", i, len(jars) + 1)
        try:
            fps[jar] = cf_fingerprint(jar)
        except OSError:
            fps[jar] = 0
    if on_note and jars:
        on_note("匹配 CurseForge 项目", len(jars), len(jars) + 1)
    matches = cf_match_fingerprints(dm, [f for f in fps.values() if f]) if jars else {}

    files = []
    overrides = []
    for jar in jars:
        hit = matches.get(fps.get(jar) or 0)
        if hit:
            files.append({
                "projectID": hit["projectID"],
                "fileID": hit["fileID"],
                "required": True,
            })
        else:
            overrides.append(("mods/" + jar.name, jar))
    overrides += _collect_overrides(inst)

    meta = _pack_meta(inst)
    loaders = []
    if meta["loader"] and meta["loader_version"]:
        loaders.append({"id": f"{meta['loader']}-{meta['loader_version']}", "primary": True})
    elif meta["loader"]:
        loaders.append({"id": meta["loader"], "primary": True})
    manifest = {
        "minecraft": {
            "version": meta["mc_version"],
            "modLoaders": loaders,
        },
        "manifestType": "minecraftModpack",
        "manifestVersion": 1,
        "name": meta["name"],
        "version": meta["version"],
        "author": meta["author"],
        "files": files,
        "overrides": "overrides",
    }
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        for rel, path in overrides:
            zf.write(path, "overrides/" + rel)
    if on_note:
        on_note("导出完成", 1, 1)
    return str(dest)
