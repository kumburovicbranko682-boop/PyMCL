# -*- coding: utf-8 -*-
"""整合包导出：.mrpack（Modrinth）/ CurseForge zip / MultiMC（Prism）zip。

mrpack 与 CF 格式：能解析到对应平台的模组走 files 清单，其余文件进
overrides。MultiMC 格式没有下载清单，全部文件原样打进 .minecraft/。"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

from . import utils
from .downloader import DownloadManager
from .instances import Instance
OVERRIDE_DIRS = ("config", "resourcepacks", "shaderpacks", "datapacks")

# 导出候选（HMCL 导出向导同款可勾选清单）
# 默认勾选：装好即玩必需的内容
EXPORT_DEFAULT_ON = ("mods", "config", "resourcepacks", "shaderpacks",
                     "datapacks", "options.txt", "servers.dat")
# 可选：常见但默认不打包（体积大 / 因人而异）
EXPORT_OPTIONAL = ("saves", "screenshots", "scripts", "defaultconfigs",
                   "kubejs", "schematics", "journeymap", "local")
# 永不进包：游戏本体 / 启动器内部 / 日志缓存
_EXPORT_EXCLUDE = {
    "versions", "libraries", "assets", "natives", "logs", "crash-reports",
    "backups", "downloads", "cache", ".fabric", ".mixin.out", "webcache",
    "usercache.json", "usernamecache.json", "realms_persistence.json",
    "command_history.txt", ".pymcl_trash", "pymcl.json",
}


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


def _entry_stats(path: Path) -> tuple[int, int]:
    """(文件数, 总字节)。目录递归统计，普通文件就是它自己。"""
    if path.is_file():
        try:
            return 1, path.stat().st_size
        except OSError:
            return 1, 0
    files = 0
    size = 0
    for p in path.rglob("*"):
        if p.is_file():
            files += 1
            try:
                size += p.stat().st_size
            except OSError:
                pass
    return files, size


def list_export_candidates(instance: Instance) -> list[dict]:
    """实例目录里可以打进整合包的条目（HMCL 导出向导的勾选清单同款）。

    返回 [{path, dir, files, bytes, default}]，默认勾选见 EXPORT_DEFAULT_ON。
    游戏本体（versions/libraries/assets）与日志缓存永远不列。"""
    root = Path(instance.path)
    if not root.is_dir():
        return []
    rows = []
    for p in sorted(root.iterdir(), key=lambda x: x.name.lower()):
        name = p.name
        if name in _EXPORT_EXCLUDE or name.startswith("."):
            continue
        files, size = _entry_stats(p)
        if files == 0:
            continue
        rows.append({
            "path": name,
            "dir": p.is_dir(),
            "files": files,
            "bytes": size,
            "default": name in EXPORT_DEFAULT_ON,
        })
    rows.sort(key=lambda r: (not r["default"], r["path"].lower()))
    return rows


def _collect_included(inst: Instance, names) -> list:
    """按勾选的顶层条目收集 (rel, path) 文件对。目录递归，缺失跳过。"""
    out = []
    root = Path(inst.path)
    for name in names or []:
        rel_name = str(name).strip().strip("/\\")
        if not rel_name or rel_name in _EXPORT_EXCLUDE:
            continue
        src = root / rel_name
        if src.is_file():
            out.append((rel_name, src))
        elif src.is_dir():
            for p in src.rglob("*"):
                if p.is_file():
                    rel = Path(rel_name) / p.relative_to(src)
                    out.append((str(rel).replace("\\", "/"), p))
    return out


def export_mrpack(instance: Instance, dest: str | Path, dm: DownloadManager | None = None,
                  on_note=None, include=None, meta_override=None) -> str:
    inst = instance
    dest = Path(dest)
    utils.ensure_dir(dest.parent)
    dm = dm or DownloadManager(threads=4)
    mods_dir = inst.path / "mods"
    want_mods = include is None or "mods" in {str(x).strip("/\\") for x in include}
    files = []
    overrides = []
    if want_mods and mods_dir.is_dir():
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
    if include is None:
        overrides += _collect_overrides(inst)
    else:
        others = [x for x in include if str(x).strip("/\\") != "mods"]
        overrides += _collect_included(inst, others)
        if want_mods:
            # mods 目录里 jar 之外的文件（配置、子目录）原样进 overrides
            overrides += [(rel, p) for rel, p in _collect_included(inst, ["mods"])
                          if not rel.lower().endswith(".jar") or "/" in rel[5:]]

    meta = _pack_meta(inst, meta_override)
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


def _pack_meta(inst: Instance, override: dict | None = None) -> dict:
    meta = inst.meta() or {}
    pack = meta.get("modpack") if isinstance(meta.get("modpack"), dict) else {}
    out = {
        "name": pack.get("name") or inst.name,
        "version": str(pack.get("version") or "1.0.0"),
        "author": str(pack.get("author") or ""),
        "mc_version": pack.get("mc_version") or meta.get("mc_version") or "",
        "loader": str(pack.get("loader") or "").lower(),
        "loader_version": str(pack.get("loader_version") or ""),
    }
    # 导出向导可改名称 / 版本 / 作者；空值不覆盖
    for key in ("name", "version", "author"):
        val = str((override or {}).get(key) or "").strip()
        if val:
            out[key] = val
    return out


def export_cf_zip(instance: Instance, dest: str | Path, dm: DownloadManager | None = None,
                  on_note=None, include=None, meta_override=None) -> str:
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
    want_mods = include is None or "mods" in {str(x).strip("/\\") for x in include}
    jars = []
    if want_mods and mods_dir.is_dir():
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
    if include is None:
        overrides += _collect_overrides(inst)
    else:
        others = [x for x in include if str(x).strip("/\\") != "mods"]
        overrides += _collect_included(inst, others)
        if want_mods:
            overrides += [(rel, p) for rel, p in _collect_included(inst, ["mods"])
                          if not rel.lower().endswith(".jar") or "/" in rel[5:]]

    meta = _pack_meta(inst, meta_override)
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


# MultiMC / Prism 组件 uid（mmc-pack.json 的 components[].uid）
_MMC_LOADER_UIDS = {
    "fabric": "net.fabricmc.fabric-loader",
    "quilt": "org.quiltmc.quilt-loader",
    "forge": "net.minecraftforge",
    "neoforge": "net.neoforged",
}


def _mmc_components(meta: dict) -> list[dict]:
    comps: list[dict] = []
    mc = meta.get("mc_version") or ""
    if mc:
        comps.append({"uid": "net.minecraft", "version": mc, "important": True})
    loader = str(meta.get("loader") or "").lower()
    uid = _MMC_LOADER_UIDS.get(loader)
    if uid:
        # Fabric / Quilt 的 loader 组件依赖 intermediary 映射（版本同 MC），
        # Prism 自己导出时也会写这一条
        if loader in ("fabric", "quilt") and mc:
            comps.append({"uid": "net.fabricmc.intermediary", "version": mc})
        entry = {"uid": uid}
        if meta.get("loader_version"):
            entry["version"] = str(meta["loader_version"])
        comps.append(entry)
    return comps


def export_mmc_zip(instance: Instance, dest: str | Path, on_note=None,
                   include=None, meta_override=None) -> str:
    """导出 MultiMC / Prism 格式实例 zip（HMCL 同款第三种导出格式）。

    结构：instance.cfg + mmc-pack.json + .minecraft/<所有本地文件>。
    该格式没有在线下载清单，mods 里的 jar 原样打包，导入方开箱即玩。
    """
    inst = instance
    dest = Path(dest)
    utils.ensure_dir(dest.parent)
    meta = _pack_meta(inst, meta_override)

    files: list[tuple[str, Path]] = []
    if include is not None:
        files = _collect_included(inst, include)
    else:
        mods_dir = inst.path / "mods"
        if mods_dir.is_dir():
            for p in sorted(mods_dir.iterdir()):
                if p.is_file() and p.name.lower().endswith((".jar", ".jar.disabled", ".litemod")):
                    files.append(("mods/" + p.name, p))
        files += _collect_overrides(inst)
        for extra_name in ("options.txt", "servers.dat"):
            p = inst.path / extra_name
            if p.is_file():
                files.append((extra_name, p))

    cfg = "\n".join([
        "[General]",
        "ConfigVersion=1.2",
        "InstanceType=OneSix",
        "iconKey=default",
        f"name={meta['name']}",
        f"notes=由 PyMCL 从实例 {inst.name} 导出",
        "",
    ])
    pack = {
        "formatVersion": 1,
        "components": _mmc_components(meta),
    }
    total = len(files) + 1
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("instance.cfg", cfg)
        zf.writestr("mmc-pack.json", json.dumps(pack, ensure_ascii=False, indent=2))
        for i, (rel, path) in enumerate(files):
            if on_note:
                on_note(f"打包 {rel}", i, total)
            zf.write(path, ".minecraft/" + rel)
    if on_note:
        on_note("导出完成", total, total)
    return str(dest)
