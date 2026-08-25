# -*- coding: utf-8 -*-
"""整合包更新（对标 HMCL 的整合包升级路径）。

安装时 modpack.py 会把来源（Modrinth slug / CurseForge addon_id）和
文件清单（managed_files + override_files sha1）写进实例 meta，本模块负责：

- pack_state()   实例装了什么整合包、能不能原地更新
- check_update() 查上游有没有新版本
- update()       删旧清单文件（用户改过的 overrides 先备份）后重装新版本
"""
from __future__ import annotations

import time
from pathlib import Path

from . import utils
from .downloader import DownloadManager
from .instances import Instance
from .modpack import (
    ModpackError, _mrpack_candidates, install_cf_modpack, install_mrpack_by_slug,
    modrinth_versions, resolve_cf_modpack_file,
)


def _meta(instance: Instance) -> dict:
    data = instance.meta() or {}
    pack = data.get("modpack")
    return pack if isinstance(pack, dict) else {}


def _updatable(pack: dict) -> tuple[bool, str]:
    if not pack:
        return False, "该实例不是整合包安装"
    source = pack.get("source") or ""
    if source in ("multimc", "plain-zip"):
        return False, "本地导入的整合包没有在线来源，无法检查更新"
    if not isinstance(pack.get("managed_files"), list):
        return False, "该整合包由旧版 PyMCL 安装，缺少文件清单，无法原地更新；请新建实例重新安装"
    if source == "modrinth":
        if not pack.get("slug"):
            return False, "该整合包从本地文件安装，没有 Modrinth 来源，无法检查更新"
        return True, ""
    if source == "curseforge":
        if not (pack.get("addon_id") or pack.get("slug")):
            return False, "该整合包从本地文件安装，没有 CurseForge 来源，无法检查更新"
        return True, ""
    return False, f"未知整合包来源: {source or '无'}"


def pack_state(instance: Instance) -> dict:
    """实例的整合包状态（无网络）。"""
    pack = _meta(instance)
    if not pack:
        return {"installed": False}
    ok, reason = _updatable(pack)
    return {
        "installed": True,
        "name": pack.get("name") or "",
        "version": pack.get("version") or "",
        "source": pack.get("source") or "",
        "can_update": ok,
        "reason": reason,
    }


def check_update(dm: DownloadManager, instance: Instance, api_key=None) -> dict:
    """查上游最新版本。返回 {has_update, current, latest, latest_id, date, changelog}。"""
    pack = _meta(instance)
    ok, reason = _updatable(pack)
    if not ok:
        raise ModpackError(reason)
    current = str(pack.get("version") or "")
    if pack.get("source") == "modrinth":
        versions = modrinth_versions(dm, pack["slug"])
        candidates = _mrpack_candidates(versions, limit=1)
        if not candidates:
            raise ModpackError(f"整合包 {pack['slug']} 上游没有可安装的 .mrpack 版本")
        _f, latest = candidates[0]
        latest_id = str(latest.get("id") or "")
        has_update = bool(latest_id) and latest_id != str(pack.get("source_version_id") or "")
        # 老记录没有 source_version_id 时退化成版本号比较
        if not pack.get("source_version_id"):
            has_update = str(latest.get("version_number") or "") != current
        return {
            "has_update": has_update,
            "current": current,
            "latest": str(latest.get("version_number") or ""),
            "latest_id": latest_id,
            "date": str(latest.get("date_published") or "")[:10],
            "changelog": (latest.get("changelog") or "")[:1000],
        }
    # curseforge
    info = resolve_cf_modpack_file(
        dm, pack.get("addon_id") or "", api_key=api_key, cf_slug=pack.get("slug") or None)
    latest_id = str(info.get("file_id") or "")
    has_update = bool(latest_id) and latest_id != str(pack.get("file_id") or "")
    return {
        "has_update": has_update,
        "current": current,
        "latest": str(info.get("fileName") or ""),
        "latest_id": latest_id,
        "date": "",
        "changelog": "",
    }


def _remove_old_files(instance: Instance, pack: dict, on_note=None) -> dict:
    """删除旧整合包管理的文件；用户改过的 overrides 备份后保留原位。

    返回 {removed, kept_modified, backup_dir}。
    """
    root = instance.path.resolve()

    def _safe(rel: str) -> Path | None:
        p = (instance.path / rel).resolve()
        if str(p).startswith(str(root) + "/") or str(p).startswith(str(root) + "\\"):
            return p
        return None

    removed = 0
    modified = []
    for rel in pack.get("managed_files") or []:
        p = _safe(str(rel))
        if p and p.is_file():
            try:
                p.unlink()
                removed += 1
            except OSError:
                pass
    for entry in pack.get("override_files") or []:
        rel = str((entry or {}).get("path") or "")
        want = str((entry or {}).get("sha1") or "")
        p = _safe(rel)
        if not p or not p.is_file():
            continue
        try:
            unchanged = bool(want) and utils.sha1_file(p) == want.lower()
        except OSError:
            unchanged = False
        if unchanged:
            try:
                p.unlink()
                removed += 1
            except OSError:
                pass
        else:
            modified.append((rel, p))

    backup_dir = ""
    if modified:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        bdir = instance.path / "backups" / f"modpack-update-{stamp}"
        for rel, p in modified:
            target = bdir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                import shutil
                shutil.copy2(p, target)
            except OSError:
                continue
        backup_dir = str(bdir)
        if on_note:
            on_note(f"检测到 {len(modified)} 个被修改过的整合包配置，"
                    f"已备份到 {bdir}（新整合包若带同名文件会覆盖原位）")
    return {"removed": removed, "kept_modified": len(modified), "backup_dir": backup_dir}


def update(dm: DownloadManager, instance: Instance, on_progress=None, cancel=None,
           target_version_id=None, api_key=None) -> dict:
    """原地更新整合包到最新（或指定）版本。返回新的 pack_meta。"""
    pack = _meta(instance)
    ok, reason = _updatable(pack)
    if not ok:
        raise ModpackError(reason)

    def note(msg):
        if on_progress:
            on_progress(msg, 0, 0)

    note(f"更新整合包 {pack.get('name')}（当前 {pack.get('version') or '?'}）")
    cleanup = _remove_old_files(instance, pack, on_note=note)
    note(f"已清理旧整合包文件 {cleanup['removed']} 个")

    if pack.get("source") == "modrinth":
        new_meta = install_mrpack_by_slug(
            dm, pack["slug"], instance, on_progress=on_progress, cancel=cancel,
            version_id=target_version_id or None)
    else:
        new_meta = install_cf_modpack(
            dm, pack.get("addon_id") or "", instance, api_key=api_key,
            on_progress=on_progress, cancel=cancel,
            cf_slug=pack.get("slug") or None,
            file_id=target_version_id or None)
    if cleanup.get("backup_dir"):
        new_meta = dict(new_meta or {})
        new_meta["last_update_backup"] = cleanup["backup_dir"]
        instance.set_meta("modpack", new_meta)
    note(f"整合包已更新到 {new_meta.get('version') or '?'}")
    return new_meta
