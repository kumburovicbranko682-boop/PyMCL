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


# ---------------------------------------------------------------- 版本图标
# PCL2「版本设置 → 版本图标」/ HMCL 版本图标同款：每个版本可自选一张图。
# 图放版本文件夹里（.version_icon.*），重命名/复制版本时自动跟着走。

ICON_STEM = ".version_icon"
_ICON_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")
_ICON_MAX_BYTES = 4 * 1024 * 1024


def icon_path(instance: Instance, version_id: str) -> str:
    """版本自定义图标路径；没设置返回空串。"""
    try:
        vdir = _vdir(instance, version_id)
    except VersionOpError:
        return ""
    for suffix in _ICON_SUFFIXES:
        p = vdir / f"{ICON_STEM}{suffix}"
        if p.is_file():
            return str(p)
    return ""


def set_icon(instance: Instance, version_id: str, src) -> str:
    src = Path(src)
    if not src.is_file():
        raise VersionOpError(f"图片不存在: {src}")
    suffix = src.suffix.lower()
    if suffix not in _ICON_SUFFIXES:
        raise VersionOpError(f"不支持的图片格式: {suffix or src.name}")
    if src.stat().st_size > _ICON_MAX_BYTES:
        raise VersionOpError("图片太大（超过 4 MB）")
    vdir = _vdir(instance, version_id)
    clear_icon(instance, version_id)
    dest = vdir / f"{ICON_STEM}{suffix}"
    shutil.copyfile(src, dest)
    return str(dest)


def clear_icon(instance: Instance, version_id: str):
    vdir = _vdir(instance, version_id)
    for suffix in _ICON_SUFFIXES:
        p = vdir / f"{ICON_STEM}{suffix}"
        if p.is_file():
            p.unlink(missing_ok=True)


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


def export_launch_bat(dest: Path, cmd: list, cwd) -> str:
    dest = Path(dest)
    utils.ensure_dir(dest.parent)
    lines = [
        "@echo off",
        "chcp 65001 >nul",
        f'cd /d "{cwd}"',
    ]
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
