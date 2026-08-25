# -*- coding: utf-8 -*-
"""导出整合包（Modrinth .mrpack）。

对标 PCL2 / HMCL 的「导出整合包」：把装好模组的版本打包分享。
mods 按 sha1 反查 Modrinth 拿官方下载地址（只记链接，包更小、可再分发）；
查不到的模组和 config、options.txt 等一起放进 overrides。
"""
from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path

from . import utils, version_settings
from .downloader import DownloadManager

MODRINTH_API = "https://api.modrinth.com/v2"

# overrides 里默认带走的游戏目录内容
DEFAULT_OVERRIDES = ("config", "options.txt", "servers.dat")


class ExportError(Exception):
    """导出失败，消息可直接展示给用户。"""


def _loader_deps(vjson: dict, deps: dict):
    for lib in vjson.get("libraries") or []:
        name = str(lib.get("name") or "")
        parts = name.split(":")
        if len(parts) < 3:
            continue
        ver = parts[2]
        if name.startswith("net.fabricmc:fabric-loader:"):
            deps["fabric-loader"] = ver
        elif name.startswith("org.quiltmc:quilt-loader:"):
            deps["quilt-loader"] = ver
        elif name.startswith("net.neoforged:neoforge:"):
            deps["neoforge"] = ver
        elif name.startswith("net.neoforged:forge:"):
            # NeoForge 1.20.1 时代沿用 forge 坐标，版本形如 1.20.1-47.1.84
            deps["neoforge"] = ver.split("-", 1)[1] if "-" in ver else ver
        elif name.startswith("net.minecraftforge:forge:"):
            deps["forge"] = ver.split("-", 1)[1] if "-" in ver else ver


def pack_dependencies(instance, version_id: str) -> dict:
    """从版本 JSON（含继承链）推断 mrpack 的 dependencies。"""
    deps = {}
    chain = []
    seen = set()
    vid = version_id
    while vid and vid not in seen:
        seen.add(vid)
        vjson = instance.version_json(vid)
        if not isinstance(vjson, dict):
            break
        chain.append(vjson)
        vid = vjson.get("inheritsFrom")
    if not chain:
        return deps
    for vjson in chain:
        _loader_deps(vjson, deps)
    root = chain[-1]
    mc = str(root.get("id") or "")
    if root.get("inheritsFrom"):
        # 继承链没走到底（父 JSON 缺失）：直接用 inheritsFrom 的名字
        mc = str(root["inheritsFrom"])
    if deps and re.search(r"forge|fabric|quilt", mc, re.I):
        # 单体安装（无继承）时 id 形如 1.20.4-forge-49.0.30，抠出纯 MC 版本
        m = re.search(r"(1\.\d+(?:\.\d+)?)", mc)
        if m:
            mc = m.group(1)
    if mc:
        deps["minecraft"] = mc
    return deps


def _match_modrinth(dm: DownloadManager, hashes: list[str]) -> dict:
    """sha1 → Modrinth version 对象。批量接口失败时退化为逐个查询。"""
    if not hashes:
        return {}
    try:
        resp = dm.session.post(
            f"{MODRINTH_API}/version_files",
            json={"hashes": hashes, "algorithm": "sha1"},
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict):
            return data
    except Exception as e:
        utils.log.warning("Modrinth 批量反查失败，改为逐个查询: %s", e)
    out = {}
    misses = 0
    for h in hashes:
        try:
            v = dm.fetch_json(f"{MODRINTH_API}/version_file/{h}", timeout=12)
            if isinstance(v, dict):
                out[h] = v
            misses = 0
        except Exception:
            misses += 1
            if misses >= 3:
                # 连续失败大概率是断网，别把每个模组都拖一遍超时
                break
    return out


def _download_url(version_obj, sha1: str) -> str:
    if not isinstance(version_obj, dict):
        return ""
    for f in version_obj.get("files") or []:
        if ((f.get("hashes") or {}).get("sha1") or "").lower() == sha1.lower():
            return f.get("url") or ""
    return ""


def export_mrpack(instance, version_id: str, dest=None, *, name: str = "",
                  pack_version: str = "1.0.0", summary: str = "",
                  include=None, dm: DownloadManager | None = None,
                  on_note=None) -> dict:
    """把版本导出为 .mrpack。返回 {path, mods, matched, overrides, dependencies}。"""
    dm = dm or DownloadManager(threads=4)

    def note(msg):
        if on_note:
            on_note(msg)

    deps = pack_dependencies(instance, version_id)
    if not deps.get("minecraft"):
        raise ExportError(f"读不到版本 JSON: {version_id}")
    settings = version_settings.load(instance, version_id)
    gdir = version_settings.game_dir(instance, version_id, settings)
    mods_folder = version_settings.mods_dir(instance, version_id, settings)

    jars = {}
    if mods_folder.is_dir():
        for p in sorted(mods_folder.iterdir()):
            if p.is_file() and p.suffix.lower() == ".jar":
                jars[utils.sha1_file(p)] = p
    note(f"共 {len(jars)} 个模组，正在反查 Modrinth…")
    matched = _match_modrinth(dm, list(jars))

    files = []
    leftovers = []
    for h, path in jars.items():
        url = _download_url(matched.get(h), h)
        if url:
            files.append({
                "path": f"mods/{path.name}",
                "hashes": {"sha1": h, "sha512": utils.sha512_file(path)},
                "env": {"client": "required", "server": "required"},
                "downloads": [url],
                "fileSize": path.stat().st_size,
            })
        else:
            leftovers.append(path)
    note(f"Modrinth 命中 {len(files)} 个，{len(leftovers)} 个打进 overrides")

    index = {
        "formatVersion": 1,
        "game": "minecraft",
        "versionId": pack_version or "1.0.0",
        "name": name or version_id,
        "dependencies": deps,
        "files": files,
    }
    if summary:
        index["summary"] = summary

    if not dest:
        dest = utils.ROOT / "exports" / f"{name or version_id}-{pack_version or '1.0.0'}.mrpack"
    dest = Path(dest)
    utils.ensure_dir(dest.parent)
    wanted = list(DEFAULT_OVERRIDES) + [str(x) for x in (include or [])]
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("modrinth.index.json",
                   json.dumps(index, ensure_ascii=False, indent=2))
        for path in leftovers:
            z.write(path, f"overrides/mods/{path.name}")
        for item in wanted:
            src = gdir / item
            if src.is_file():
                z.write(src, f"overrides/{item}")
            elif src.is_dir():
                for f in sorted(src.rglob("*")):
                    if f.is_file():
                        rel = f.relative_to(src).as_posix()
                        z.write(f, f"overrides/{item}/{rel}")
    note(f"已写出 {dest}")
    return {
        "path": str(dest),
        "mods": len(jars),
        "matched": len(files),
        "overrides": len(leftovers),
        "dependencies": deps,
    }
