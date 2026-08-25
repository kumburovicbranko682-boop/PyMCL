# -*- coding: utf-8 -*-
"""每版本设置：隔离、内存、Java、JVM、启动前后命令、直连服务器。对齐 PCL 版本设置。"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from . import utils
from .config import CONFIG

FILE_NAME = "pymcl.json"
ISOLATION_NONE = "none"
ISOLATION_SAVES = "saves"
ISOLATION_MODS = "mods"
ISOLATION_ALL = "all"
ISOLATION_LABELS = {
    ISOLATION_NONE: "关闭（共用实例目录）",
    ISOLATION_SAVES: "隔离存档",
    ISOLATION_MODS: "隔离 Mod 与配置",
    ISOLATION_ALL: "隔离全部",
}
SHARED_LINKS = ("mods", "config", "resourcepacks", "shaderpacks", "downloads")
SAVES_LINKS = ("saves",)

DEFAULTS = {
    "isolation": ISOLATION_NONE,
    "memory_mb": None,
    "java": "自动选择",
    "jvm_args": "",
    "game_args": "",
    "wrapper": "",
    "pre_launch": "",
    "post_launch": "",
    "pre_launch_wait": True,
    "server": "",
    "port": "",
    "process_priority": "normal",
    "icon": "",
    "hidden": False,
    "login_account": "",
    "auth_server": "",
    "auth_server_name": "",
    "nide8_id": "",
    "gc": "",
    "window_title": "",
    "window_mode": "window",
    "window_width": None,
    "window_height": None,
    "skip_assets": False,
    "offline_skin": "default",
}

# UI 历史上写过 "maximize"，启动链早期只认 "fullscreen"，两边对不上导致全屏静默失效。
# 以 "maximize" 为准，另一个作为别名容错。
FULLSCREEN_MODES = ("maximize", "fullscreen")


def _file(instance, version_id) -> Path:
    return instance.versions_dir() / version_id / FILE_NAME


def load(instance, version_id) -> dict:
    data = dict(DEFAULTS)
    stored = utils.read_json(_file(instance, version_id), None)
    if isinstance(stored, dict):
        data.update(stored)
    iso = data.get("isolation") or CONFIG.get("default_isolation") or ISOLATION_NONE
    if iso not in ISOLATION_LABELS:
        iso = ISOLATION_NONE
    data["isolation"] = iso
    return data


def save(instance, version_id, data: dict) -> dict:
    cur = load(instance, version_id)
    cur.update(data or {})
    if cur.get("isolation") not in ISOLATION_LABELS:
        cur["isolation"] = ISOLATION_NONE
    utils.write_json(_file(instance, version_id), cur)
    return cur


def game_dir(instance, version_id, settings=None) -> Path:
    settings = settings or load(instance, version_id)
    iso = settings.get("isolation") or ISOLATION_NONE
    if iso in (ISOLATION_ALL, ISOLATION_SAVES, ISOLATION_MODS):
        return instance.versions_dir() / version_id
    return Path(instance.path)


def mods_dir(instance, version_id, settings=None) -> Path:
    root = game_dir(instance, version_id, settings)
    return root / "mods"


def _junction(link: Path, target: Path):
    target = Path(target)
    link = Path(link)
    if link.exists() or link.is_symlink():
        if link.is_dir() and not link.is_symlink() and any(link.iterdir()):
            return
        try:
            if link.is_symlink() or link.is_file():
                link.unlink()
            elif link.is_dir() and not any(link.iterdir()):
                link.rmdir()
        except OSError:
            return
    utils.ensure_dir(target)
    utils.ensure_dir(link.parent)
    if os.name == "nt":
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pass


def apply_isolation(instance, version_id, settings=None) -> Path:
    """按隔离模式准备游戏目录。返回 game_dir。"""
    settings = settings or load(instance, version_id)
    gdir = game_dir(instance, version_id, settings)
    utils.ensure_dir(gdir)
    iso = settings.get("isolation") or ISOLATION_NONE
    if iso == ISOLATION_SAVES:
        for name in SHARED_LINKS:
            _junction(gdir / name, Path(instance.path) / name)
        utils.ensure_dir(gdir / "saves")
    elif iso == ISOLATION_MODS:
        for name in SAVES_LINKS + ("resourcepacks", "shaderpacks", "screenshots"):
            _junction(gdir / name, Path(instance.path) / name)
        for name in ("mods", "config"):
            utils.ensure_dir(gdir / name)
    elif iso == ISOLATION_ALL:
        for name in ("mods", "config", "saves", "resourcepacks", "shaderpacks"):
            utils.ensure_dir(gdir / name)
    return gdir
