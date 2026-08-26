# -*- coding: utf-8 -*-
"""把实例导出成 .mrpack（能解析到 Modrinth 的走 files，其余进 overrides）。"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

from . import utils
from .downloader import DownloadManager
from .instances import Instance
OVERRIDE_DIRS = ("config", "resourcepacks", "shaderpacks", "datapacks")


def _modrinth_version_by_hash(dm: DownloadManager, digest: str):
    """按 sha1 反查 Modrinth 版本：官方优先、MCIM 兜底（source 策略）。"""
    from . import source
    for base in source.modrinth_api_bases():
        try:
            hit = dm.fetch_json(f"{base}/version_file/{digest}", timeout=15, expand=False)
            if isinstance(hit, dict):
                return hit
        except Exception:
            continue
    return None


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
            hit = _modrinth_version_by_hash(dm, digest)
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
    for folder in OVERRIDE_DIRS:
        src = inst.path / folder
        if not src.is_dir():
            continue
        for p in src.rglob("*"):
            if p.is_file():
                rel = Path(folder) / p.relative_to(src)
                overrides.append((str(rel).replace("\\", "/"), p))

    meta = inst.meta() or {}
    pack = meta.get("modpack") if isinstance(meta.get("modpack"), dict) else {}
    name = pack.get("name") or inst.name
    version = pack.get("version") or "1.0.0"
    mc = pack.get("mc_version") or meta.get("mc_version") or ""
    loader = pack.get("loader") or ""
    deps = {"minecraft": mc} if mc else {}
    if loader:
        deps[str(loader).lower()] = pack.get("loader_version") or ""
    index = {
        "formatVersion": 1,
        "game": "minecraft",
        "versionId": version,
        "name": name,
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
