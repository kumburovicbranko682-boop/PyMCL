# -*- coding: utf-8 -*-
"""多游戏目录管理：HMCL「游戏目录列表」/ PCL2「文件夹列表」同款。

每个「游戏目录」都是一个实例根目录（下面放着若干独立 .minecraft 实例）。
列表存在全局配置 game_dirs 里：[{"name": 显示名, "path": 原始路径}]。
当前生效目录仍然只由 instances_dir 决定——本模块负责记住、命名、切换，
移除只是从列表拿掉，绝不动磁盘上的文件。
"""
from __future__ import annotations

import os
from pathlib import Path

from . import utils
from .config import CONFIG

# 默认目录在配置里存相对名，跟随启动器主目录（便携模式搬家不失效）
DEFAULT_RAW = ".minecraft"
DEFAULT_NAME = "默认目录"


class GameDirError(Exception):
    pass


def resolve(raw) -> Path:
    """把配置里的原始值（相对/绝对均可）解析成绝对路径。"""
    raw = str(raw or "").strip() or DEFAULT_RAW
    p = Path(raw).expanduser()
    # pathlib: 基底 / 绝对路径 = 绝对路径，相对路径则挂在启动器主目录下
    return (utils.ROOT / p).resolve()


def _norm(raw) -> str:
    """去重/比较用的键：解析后按平台大小写规则归一。"""
    return os.path.normcase(str(resolve(raw)))


def _stored() -> list[dict]:
    raw = CONFIG.get("game_dirs")
    out = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict) and str(item.get("path") or "").strip():
                out.append({
                    "name": str(item.get("name") or "").strip(),
                    "path": str(item["path"]).strip(),
                })
    return out


def _save(stored: list[dict]):
    CONFIG.set("game_dirs", stored)
    CONFIG.save()


def active_raw() -> str:
    return str(CONFIG.get("instances_dir") or DEFAULT_RAW)


def entries() -> list[dict]:
    """目录列表。始终包含默认目录；当前生效目录若不在列表里也会补上。

    每项: {name, path(绝对), raw(配置原始值), active, exists, removable}
    """
    items = [{"name": DEFAULT_NAME, "path": DEFAULT_RAW}] + _stored()
    cur = active_raw()
    if not any(_norm(it["path"]) == _norm(cur) for it in items):
        items.append({"name": "", "path": cur})
    seen: set[str] = set()
    out: list[dict] = []
    for it in items:
        key = _norm(it["path"])
        if key in seen:
            continue
        seen.add(key)
        p = resolve(it["path"])
        out.append({
            "name": it["name"] or p.name or str(p),
            "path": str(p),
            "raw": it["path"],
            "active": key == _norm(cur),
            "exists": p.is_dir(),
            "removable": key != _norm(DEFAULT_RAW),
        })
    return out


def register(path, name: str = "") -> dict:
    """把目录加入列表（不切换）。已存在则只在给了新名字时改名。"""
    raw = str(path or "").strip()
    if not raw:
        raise GameDirError("目录路径不能为空")
    p = resolve(raw)
    if p.exists() and not p.is_dir():
        raise GameDirError(f"不是文件夹: {p}")
    try:
        utils.ensure_dir(p)
    except OSError as e:
        raise GameDirError(f"无法创建目录 {p}: {e}")
    name = str(name or "").strip()
    key = _norm(raw)
    if key == _norm(DEFAULT_RAW):
        return {"name": DEFAULT_NAME, "path": str(p), "raw": DEFAULT_RAW}
    stored = _stored()
    for it in stored:
        if _norm(it["path"]) == key:
            if name and name != it["name"]:
                it["name"] = name
                _save(stored)
            return {"name": it["name"] or p.name, "path": str(p), "raw": it["path"]}
    entry = {"name": name or p.name or str(p), "path": str(p)}
    stored.append(entry)
    _save(stored)
    return {"name": entry["name"], "path": str(p), "raw": entry["path"]}


def remove(path) -> bool:
    """从列表移除（不删文件）。默认目录与当前生效目录不可移除。"""
    key = _norm(path)
    if key == _norm(DEFAULT_RAW):
        raise GameDirError("默认目录不能移除")
    if key == _norm(active_raw()):
        raise GameDirError("不能移除正在使用的目录，请先切换到其他目录")
    stored = _stored()
    kept = [it for it in stored if _norm(it["path"]) != key]
    if len(kept) == len(stored):
        return False
    _save(kept)
    return True


def rename(path, name: str) -> bool:
    name = str(name or "").strip()
    if not name:
        raise GameDirError("名称不能为空")
    key = _norm(path)
    if key == _norm(DEFAULT_RAW):
        raise GameDirError("默认目录不能改名")
    stored = _stored()
    for it in stored:
        if _norm(it["path"]) == key:
            if it["name"] != name:
                it["name"] = name
                _save(stored)
            return True
    return False


def activate(path) -> str:
    """切换当前生效目录：先登记进列表，再写 instances_dir。返回解析后的绝对路径。"""
    register(path)
    raw = str(path or "").strip()
    p = Path(raw).expanduser()
    if _norm(raw) == _norm(DEFAULT_RAW):
        CONFIG.set("instances_dir", DEFAULT_RAW)
    else:
        CONFIG.set("instances_dir", str(p) if p.is_absolute() else raw)
    CONFIG.save()
    return str(CONFIG.instances_dir)
