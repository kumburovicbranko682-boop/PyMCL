# -*- coding: utf-8 -*-
"""从官方 Minecraft 启动器迁移版本 / 账号。

官方启动器数据目录：
- Windows: %APPDATA%\\.minecraft
- macOS: ~/Library/Application Support/minecraft
- Linux: ~/.minecraft
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from . import utils
from .instances import Instance, _STANDARD_DIRS


def official_dir() -> Path:
    """返回官方启动器的 .minecraft 目录（若存在）。"""
    if utils.IS_WINDOWS:
        base = Path(os.environ.get("APPDATA", ""))
        candidates = [base / ".minecraft"]
    elif utils.IS_MAC:
        base = Path.home() / "Library" / "Application Support"
        candidates = [base / "minecraft"]
    else:
        candidates = [Path.home() / ".minecraft"]
    for c in candidates:
        if c.is_dir():
            return c
    return Path("")


def detect_official() -> bool:
    """检测是否存在官方启动器数据目录。"""
    d = official_dir()
    return d.is_dir() and (d / "versions").is_dir()


def resolve_game_dir(path) -> Path | None:
    """把用户选的目录解析成实际游戏目录（含 versions/ 的那一层）。

    支持直接选游戏目录，也支持选到上一层（PCL / HMCL / 官方启动器的
    安装目录，里面套 .minecraft 或 minecraft）。不是游戏目录返回 None。
    """
    p = Path(path or "")
    if not p.is_dir():
        return None
    if (p / "versions").is_dir():
        return p
    for sub in (".minecraft", "minecraft"):
        cand = p / sub
        if (cand / "versions").is_dir():
            return cand
    return None


def scan_versions(src: Path) -> list[str]:
    """扫描官方目录下的版本。"""
    vdir = src / "versions"
    if not vdir.is_dir():
        return []
    result = []
    for child in sorted(vdir.iterdir()):
        if child.is_dir() and (child / f"{child.name}.json").is_file():
            result.append(child.name)
    return result


def _copy_version(src: Path, dest: Instance, version_id: str) -> str:
    """复制单个版本到实例。"""
    s = src / "versions" / version_id
    if not s.is_dir():
        return ""
    d = dest.versions_dir() / version_id
    d.mkdir(parents=True, exist_ok=True)
    # 复制 version json 和客户端 jar
    for name in (f"{version_id}.json", f"{version_id}.jar"):
        f = s / name
        if f.is_file():
            shutil.copy2(f, d / name)
    # 复制独立的 libraries 目录（若有，通常是 1.12 之前）
    lib_src = s / "libraries"
    if lib_src.is_dir():
        lib_dest = dest.libraries_dir() / "minecraft" / version_id
        lib_dest.mkdir(parents=True, exist_ok=True)
        for f in lib_src.rglob("*"):
            if f.is_file():
                rel = f.relative_to(lib_src)
                tgt = lib_dest / rel
                tgt.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, tgt)
    # 复制资产目录
    assets_src = src / "assets"
    if assets_src.is_dir():
        _copy_tree(assets_src, dest.assets_dir(), exts={".json", ".png", ".ogg"})
    return version_id


def _copy_tree(src: Path, dest: Path, exts=None):
    for f in src.rglob("*"):
        if not f.is_file():
            continue
        if exts and f.suffix.lower() not in exts:
            continue
        rel = f.relative_to(src)
        tgt = dest / rel
        tgt.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(f, tgt)
        except OSError:
            pass


def import_versions(src: Path, instance_name: str = "default", versions: list[str] | None = None) -> list[str]:
    """从官方目录导入版本到指定实例。返回导入成功的版本列表。"""
    src = Path(src)
    inst = Instance(instance_name)
    if not inst.path.is_dir():
        inst.create()
    wanted = versions or scan_versions(src)
    imported = []
    for vid in wanted:
        try:
            _copy_version(src, inst, vid)
            imported.append(vid)
        except Exception as e:
            utils.log.warning("导入版本失败 %s: %s", vid, e)
    if imported:
        inst.set_meta("mc_version", imported[-1])
    return imported


def import_accounts(src: Path) -> int:
    """从官方启动器导入已登录的微软账号参考。"""
    # 官方启动器把账号 token 放在凭据管理器（Windows）或 launcher_accounts.json，
    # 出于安全我们不直接复制 token，只记录提示。
    src = Path(src)
    count = 0
    launcher_accounts = src / "launcher_accounts.json"
    if launcher_accounts.is_file():
        try:
            data = utils.read_json(launcher_accounts, None)
            if isinstance(data, dict):
                count = len(data.get("accounts", {}))
        except Exception:
            pass
    return count


def migrate(official_root: str, instance_name: str = "default",
            want_versions: bool = True, want_assets: bool = True) -> dict:
    """执行完整迁移。返回统计信息。

    参数不能叫 import_versions：那会遮蔽上面的模块级同名函数，
    函数体里再调用它就成了 `True(...)` —— TypeError。
    """
    src = Path(official_root)
    if not src.is_dir():
        raise FileNotFoundError(f"游戏目录不存在: {src}")
    inst = Instance(instance_name)
    if not inst.path.is_dir():
        inst.create()
    result = {"versions": [], "accounts": 0}

    if want_versions:
        result["versions"] = import_versions(src, instance_name)

    if want_assets:
        # 复制全局 assets
        assets_src = src / "assets"
        if assets_src.is_dir() and assets_src != inst.assets_dir():
            _copy_tree(assets_src, inst.assets_dir())

    result["accounts"] = import_accounts(src)
    return result