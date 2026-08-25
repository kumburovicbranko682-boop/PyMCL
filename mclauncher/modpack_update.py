# -*- coding: utf-8 -*-
"""整合包更新：查实例装的整合包有没有新版本，一键更新到同一实例。

对标 HMCL 的「更新整合包」。存档保留；config 等会被新包 overrides 覆盖。
"""
from __future__ import annotations

from .downloader import DownloadManager
from .modpack import (
    ModpackError, _mrpack_candidates, install_cf_modpack,
    install_mrpack_by_slug, modrinth_versions, resolve_cf_modpack_file,
)


def pack_meta(instance) -> dict:
    meta = instance.meta() or {}
    pack = meta.get("modpack")
    return pack if isinstance(pack, dict) else {}


def check_pack_update(instance, dm: DownloadManager | None = None) -> dict:
    """返回 {has_update, source, name, current, latest, latest_id}。

    实例不是整合包装出来的 / 老数据没记录来源身份时抛 ModpackError。
    """
    pack = pack_meta(instance)
    if not pack:
        raise ModpackError("这个实例不是从整合包安装的，没有可更新的整合包")
    source = pack.get("source") or ""
    if source == "modrinth" and pack.get("slug"):
        dm = dm or DownloadManager(threads=2)
        versions = modrinth_versions(dm, pack["slug"])
        cands = _mrpack_candidates(versions, limit=1)
        if not cands:
            raise ModpackError("Modrinth 上找不到该整合包的可安装版本")
        _file, latest = cands[0]
        cur_id = str(pack.get("version_id") or "")
        latest_id = str(latest.get("id") or "")
        if cur_id:
            has = bool(latest_id) and latest_id != cur_id
        else:
            # 旧数据没记 version_id：退化为比较版本号字符串
            has = (latest.get("version_number") or "") != (pack.get("version") or "")
        return {
            "has_update": has,
            "source": "modrinth",
            "name": pack.get("name") or pack["slug"],
            "current": pack.get("version_number") or pack.get("version") or "?",
            "latest": latest.get("version_number") or latest.get("name") or "?",
            "latest_id": latest_id,
        }
    if source == "curseforge" and pack.get("addon_id"):
        dm = dm or DownloadManager(threads=2)
        info = resolve_cf_modpack_file(
            dm, pack["addon_id"], cf_slug=pack.get("cf_slug") or None)
        cur_id = str(pack.get("file_id") or "")
        latest_id = str(info.get("file_id") or "")
        has = bool(latest_id) and bool(cur_id) and latest_id != cur_id
        return {
            "has_update": has,
            "source": "curseforge",
            "name": pack.get("name") or info.get("name") or "?",
            "current": pack.get("version") or (f"file {cur_id}" if cur_id else "?"),
            "latest": info.get("fileName") or f"file {latest_id}",
            "latest_id": latest_id,
        }
    raise ModpackError(
        "这个实例的整合包没有记录来源（老版本安装的）。"
        "从目录重新安装一次后即可检查更新")


def apply_pack_update(instance, dm: DownloadManager | None = None,
                      on_progress=None, cancel=None, java=None) -> dict:
    """有新版本时按最新版本重装整合包到同一实例，返回 check 结果加 updated。"""
    pack = pack_meta(instance)
    info = check_pack_update(instance, dm=dm)
    if not info["has_update"]:
        return info
    dm = dm or DownloadManager(threads=4)
    if info["source"] == "modrinth":
        install_mrpack_by_slug(
            dm, pack["slug"], instance, on_progress=on_progress,
            cancel=cancel, java=java, version_id=info["latest_id"])
    else:
        install_cf_modpack(
            dm, pack["addon_id"], instance, on_progress=on_progress,
            cancel=cancel, cf_slug=pack.get("cf_slug") or None,
            file_id=info["latest_id"])
    info["updated"] = True
    return info
