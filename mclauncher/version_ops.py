# -*- coding: utf-8 -*-
"""版本文件夹操作：重命名 / 复制 / 隐藏 / 开目录 / 导出启动脚本。"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from . import utils
from . import version_settings as vs
from .crash import open_path
from .instances import Instance, InstanceError

_ILLEGAL = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


class VersionOpError(Exception):
    pass


def sanitize_id(raw: str) -> str:
    s = _ILLEGAL.sub("-", str(raw or "").strip())
    s = re.sub(r"\s+", " ", s).strip(" .")
    if not s:
        raise VersionOpError("版本名不能为空")
    if len(s) > 64:
        s = s[:64].rstrip(" .")
    return s


def _vdir(instance: Instance, version_id: str) -> Path:
    p = instance.versions_dir() / version_id
    if not p.is_dir():
        raise VersionOpError(f"版本不存在: {version_id}")
    return p


def rename_version(instance: Instance, old_id: str, new_id: str) -> str:
    new_id = sanitize_id(new_id)
    if new_id == old_id:
        return old_id
    src = _vdir(instance, old_id)
    dest = instance.versions_dir() / new_id
    if dest.exists():
        raise VersionOpError(f"已存在版本 {new_id}")
    shutil.copytree(src, dest)
    try:
        _rewrite_ids(dest, old_id, new_id)
        utils.remove_tree(src)
    except Exception:
        utils.remove_tree(dest)
        raise
    return new_id


def copy_version(instance: Instance, old_id: str, new_id: str) -> str:
    new_id = sanitize_id(new_id)
    src = _vdir(instance, old_id)
    dest = instance.versions_dir() / new_id
    if dest.exists():
        raise VersionOpError(f"已存在版本 {new_id}")
    shutil.copytree(src, dest)
    _rewrite_ids(dest, old_id, new_id)
    return new_id


def _rewrite_ids(dest: Path, old_id: str, new_id: str):
    for p in list(dest.iterdir()):
        if p.is_file() and p.stem == old_id:
            p.rename(dest / f"{new_id}{p.suffix}")
    jfile = dest / f"{new_id}.json"
    if not jfile.is_file():
        old_json = dest / f"{old_id}.json"
        if old_json.is_file():
            old_json.rename(jfile)
    data = utils.read_json(jfile, None)
    if isinstance(data, dict):
        data["id"] = new_id
        utils.write_json(jfile, data)
    settings = dest / vs.FILE_NAME
    if settings.is_file():
        stored = utils.read_json(settings, {}) or {}
        stored.pop("hidden", None)
        utils.write_json(settings, stored)


def set_hidden(instance: Instance, version_id: str, hidden: bool) -> dict:
    _vdir(instance, version_id)
    return vs.save(instance, version_id, {"hidden": bool(hidden)})


def is_hidden(instance: Instance, version_id: str) -> bool:
    return bool(vs.load(instance, version_id).get("hidden"))


def folder_map(instance: Instance, version_id: str = "") -> dict:
    settings = vs.load(instance, version_id) if version_id else None
    gdir = vs.game_dir(instance, version_id, settings) if version_id else Path(instance.path)
    vdir = instance.versions_dir() / version_id if version_id else instance.versions_dir()
    return {
        "root": Path(instance.path),
        "game": Path(gdir),
        "mods": vs.mods_dir(instance, version_id, settings) if version_id else Path(instance.path) / "mods",
        "saves": Path(gdir) / "saves",
        "screenshots": Path(gdir) / "screenshots",
        "resourcepacks": Path(gdir) / "resourcepacks",
        "shaderpacks": Path(gdir) / "shaderpacks",
        "datapacks": Path(gdir) / "datapacks",
        "logs": Path(gdir) / "logs",
        "crash-reports": Path(gdir) / "crash-reports",
        "version": vdir,
    }


def open_folder(instance: Instance, version_id: str = "", which: str = "root") -> str:
    mapping = folder_map(instance, version_id)
    path = mapping.get(which) or mapping["root"]
    utils.ensure_dir(path)
    if not open_path(path):
        raise VersionOpError(f"无法打开: {path}")
    return str(path)


def export_launch_bat(dest: Path, cmd: list, cwd, env: dict | None = None) -> str:
    dest = Path(dest)
    utils.ensure_dir(dest.parent)
    lines = [
        "@echo off",
        "chcp 65001 >nul",
        f'cd /d "{cwd}"',
    ]
    for k, v in (env or {}).items():
        lines.append(f'set "{k}={v}"')
    quoted = []
    for a in cmd:
        s = str(a)
        if any(ch in s for ch in ' \t&|<>^"'):
            quoted.append('"' + s.replace('"', '\\"') + '"')
        else:
            quoted.append(s)
    lines.append(" ".join(quoted))
    lines.append("pause")
    dest.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
    return str(dest)
