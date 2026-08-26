# -*- coding: utf-8 -*-
"""已装模组更新：按 sha1 查 Modrinth，比出版本。支持忽略与整包锁定（PCL2 同款）。"""
from __future__ import annotations

from pathlib import Path

from . import utils
from .ai.conflict import inspect_jar
from .downloader import DownloadManager
from .instances import Instance
from .mods import list_instance_mod_entries

API = "https://api.modrinth.com/v2"

# 更新忽略表：{project_id: "*"（永不提醒）或 具体 latest 版本串（只忽略这个版本）}
IGNORE_FILE = "pymcl_mod_ignores.json"

# 实例级「禁止更新 Mod」开关（PCL 2.10.7 同款：防整合包玩家误更新拆包）。
# PyMCL 的模组更新作用在实例共享 mods 上，所以锁也挂在实例 meta 里。
LOCK_KEY = "lock_mod_updates"


class UpdateLockedError(Exception):
    """实例开启了 Mod 更新锁定时，检查/应用更新都拒绝执行。"""


def is_locked(instance: Instance) -> bool:
    return bool(instance.meta().get(LOCK_KEY))


def set_locked(instance: Instance, locked: bool) -> bool:
    instance.set_meta(LOCK_KEY, bool(locked))
    return bool(locked)


def _ensure_unlocked(instance: Instance):
    if is_locked(instance):
        raise UpdateLockedError(
            "该实例已锁定 Mod 更新（整合包保护）。如确需更新，请先在模组管理页解除锁定。")


def _game_mods(instance: Instance, mods_path: Path | None = None) -> Path:
    return Path(mods_path) if mods_path else instance.path / "mods"


def _ignore_file(instance: Instance) -> Path:
    return Path(instance.path) / IGNORE_FILE


def ignores(instance: Instance) -> dict:
    data = utils.read_json(_ignore_file(instance), None)
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items() if k}


def set_ignore(instance: Instance, project, latest: str = "*") -> dict:
    """忽略某个 mod 的更新。latest='*' 永不提醒；否则只忽略该 latest 版本，
    下次出更新的版本仍会提醒（PCL2「忽略此版本 / 不再提醒」两档）。"""
    project = str(project or "").strip()
    if not project:
        raise ValueError("project 不能为空")
    data = ignores(instance)
    data[project] = str(latest or "*").strip() or "*"
    utils.write_json(_ignore_file(instance), data)
    return data


def clear_ignore(instance: Instance, project) -> dict:
    data = ignores(instance)
    data.pop(str(project or "").strip(), None)
    utils.write_json(_ignore_file(instance), data)
    return data


def is_ignored(row: dict, ignore_map: dict) -> bool:
    v = ignore_map.get(str(row.get("project") or ""))
    if v is None:
        return False
    return v == "*" or v == str(row.get("latest") or "")


def check_updates(instance: Instance, dm: DownloadManager | None = None,
                  mods_path: Path | None = None, mc_version: str = "",
                  loader: str = "", include_ignored: bool = False) -> list:
    _ensure_unlocked(instance)
    dm = dm or DownloadManager(threads=4)
    rows = []
    folder = _game_mods(instance, mods_path)
    if not folder.is_dir():
        return rows
    ignore_map = ignores(instance)
    for entry in list_instance_mod_entries(instance) if mods_path is None else _entries(folder):
        path = folder / entry["filename"]
        if not path.is_file() or not entry.get("enabled"):
            continue
        info = inspect_jar(path)
        digest = utils.sha1_file(path)
        row = (_modrinth_update(dm, path, digest, mc_version, loader, info)
               or _curseforge_update(dm, path, mc_version, loader, info))
        if not row:
            continue
        row["ignored"] = is_ignored(row, ignore_map)
        if row["ignored"] and not include_ignored:
            continue
        rows.append(row)
    return rows


def _modrinth_update(dm, path: Path, digest: str, mc_version: str, loader: str, info: dict):
    try:
        current = dm.fetch_json(f"{API}/version_file/{digest}", timeout=12)
    except Exception:
        return None
    if not isinstance(current, dict):
        return None
    project = current.get("project_id")
    cur_ver = current.get("version_number") or current.get("name") or ""
    if not project:
        return None
    q = []
    if mc_version:
        q.append(f"game_versions=[\"{mc_version}\"]")
    if loader:
        q.append(f"loaders=[\"{loader}\"]")
    params = ("?" + "&".join(q)) if q else ""
    try:
        versions = dm.fetch_json(f"{API}/project/{project}/version{params}", timeout=12)
    except Exception:
        return None
    if not isinstance(versions, list) or not versions:
        return None
    latest = versions[0]
    latest_ver = latest.get("version_number") or latest.get("name") or ""
    if latest.get("id") == current.get("id"):
        return None
    primary = None
    for f in latest.get("files") or []:
        if f.get("primary") or not primary:
            primary = f
    return {
        "filename": path.name,
        "name": info.get("name") or path.stem,
        "current": cur_ver,
        "latest": latest_ver,
        "project": project,
        "url": (primary or {}).get("url") or "",
        "sha1": ((primary or {}).get("hashes") or {}).get("sha1") or "",
        "size": (primary or {}).get("size") or 0,
        "filename_new": (primary or {}).get("filename") or "",
        "source": "modrinth",
    }


def _curseforge_update(dm, path: Path, mc_version: str, loader: str, info: dict):
    from .mods import cf_fingerprint
    try:
        fp = cf_fingerprint(path)
    except OSError:
        return None
    from .mods import _cf_post, cf_detail, cf_files, cf_mod_download_urls
    try:
        data = _cf_post(dm, "/fingerprints", {"fingerprints": [fp]}, timeout=20)
    except Exception:
        return None
    matches = ((data or {}).get("data") or {}).get("exactMatches") or []
    if not matches:
        return None
    hit = matches[0]
    file_obj = hit.get("file") or {}
    addon_id = hit.get("id") or file_obj.get("modId")
    if not addon_id:
        return None
    try:
        files = cf_files(dm, addon_id, game_version=mc_version or None, page_size=20)
    except Exception:
        files = []
    if not files:
        return None
    latest = files[0]
    if str(latest.get("id")) == str(file_obj.get("id")):
        return None
    filename = latest.get("fileName") or path.name
    url = latest.get("downloadUrl") or ""
    if not url:
        urls = cf_mod_download_urls(addon_id, latest.get("id"), filename)
        url = urls[0] if urls else ""
    return {
        "filename": path.name,
        "name": info.get("name") or path.stem,
        "current": file_obj.get("displayName") or file_obj.get("fileName") or path.stem,
        "latest": latest.get("displayName") or filename,
        "project": str(addon_id),
        "url": url,
        "sha1": "",
        "size": latest.get("fileLength") or 0,
        "filename_new": filename,
        "source": "curseforge",
        "file_id": latest.get("id"),
    }


def _entries(folder: Path) -> list:
    rows = []
    for p in sorted(folder.iterdir()):
        if not p.is_file():
            continue
        low = p.name.lower()
        if low.endswith(".jar"):
            rows.append({"filename": p.name, "enabled": True})
        elif low.endswith(".jar.disabled"):
            rows.append({"filename": p.name, "enabled": False})
    return rows


def apply_update(instance: Instance, row: dict, dm: DownloadManager | None = None,
                 mods_path: Path | None = None) -> str:
    _ensure_unlocked(instance)
    dm = dm or DownloadManager(threads=2)
    folder = _game_mods(instance, mods_path)
    url = row.get("url")
    if not url:
        raise RuntimeError("没有可下载的更新地址")
    new_name = row.get("filename_new") or row.get("filename")
    dest = folder / new_name
    dm.download(url, dest, sha1=row.get("sha1") or None, size=row.get("size") or None)
    old = folder / row["filename"]
    if old.resolve() != dest.resolve() and old.is_file():
        old.unlink()
    return dest.name
