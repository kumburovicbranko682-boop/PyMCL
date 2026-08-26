# -*- coding: utf-8 -*-
"""已装模组更新：按 sha1 查 Modrinth，比出版本。"""
from __future__ import annotations

from pathlib import Path

from . import utils
from .ai.conflict import inspect_jar
from .downloader import DownloadManager
from .instances import Instance
from .mods import list_instance_mod_entries


def _mr_get(dm: DownloadManager, path: str, timeout=12):
    """按 source 策略依次请求 Modrinth（官方优先、MCIM 兜底）。"""
    from . import source
    last = None
    for base in source.modrinth_api_bases():
        try:
            return dm.fetch_json(f"{base}{path}", timeout=timeout, expand=False)
        except Exception as exc:
            last = exc
    if last:
        raise last
    raise RuntimeError("Modrinth 不可用")


def _game_mods(instance: Instance, mods_path: Path | None = None) -> Path:
    return Path(mods_path) if mods_path else instance.path / "mods"


def check_updates(instance: Instance, dm: DownloadManager | None = None,
                  mods_path: Path | None = None, mc_version: str = "",
                  loader: str = "") -> list:
    dm = dm or DownloadManager(threads=4)
    rows = []
    folder = _game_mods(instance, mods_path)
    if not folder.is_dir():
        return rows
    for entry in list_instance_mod_entries(instance) if mods_path is None else _entries(folder):
        path = folder / entry["filename"]
        if not path.is_file() or not entry.get("enabled"):
            continue
        info = inspect_jar(path)
        digest = utils.sha1_file(path)
        row = _modrinth_update(dm, path, digest, mc_version, loader, info)
        if row:
            rows.append(row)
            continue
        row = _curseforge_update(dm, path, mc_version, loader, info)
        if row:
            rows.append(row)
    return rows


def _modrinth_update(dm, path: Path, digest: str, mc_version: str, loader: str, info: dict):
    try:
        current = _mr_get(dm, f"/version_file/{digest}")
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
        versions = _mr_get(dm, f"/project/{project}/version{params}")
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


def _murmur2(data: bytes, seed=1) -> int:
    m = 0x5bd1e995
    r = 24
    length = len(data)
    h = (seed ^ length) & 0xFFFFFFFF
    n = length // 4
    for i in range(n):
        k = int.from_bytes(data[i * 4:i * 4 + 4], "little")
        k = (k * m) & 0xFFFFFFFF
        k ^= k >> r
        k = (k * m) & 0xFFFFFFFF
        h = (h * m) & 0xFFFFFFFF
        h ^= k
    rest = data[n * 4:]
    if len(rest) >= 3:
        h ^= rest[2] << 16
    if len(rest) >= 2:
        h ^= rest[1] << 8
    if len(rest) >= 1:
        h ^= rest[0]
        h = (h * m) & 0xFFFFFFFF
    h ^= h >> 13
    h = (h * m) & 0xFFFFFFFF
    h ^= h >> 15
    return h & 0xFFFFFFFF


def _cf_fingerprint(path: Path) -> int:
    raw = path.read_bytes()
    cleaned = bytes(b for b in raw if b not in (9, 10, 13, 32))
    return _murmur2(cleaned, 1)


def _curseforge_update(dm, path: Path, mc_version: str, loader: str, info: dict):
    try:
        fp = _cf_fingerprint(path)
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
