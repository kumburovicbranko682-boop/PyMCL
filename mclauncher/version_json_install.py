# -*- coding: utf-8 -*-
"""通过版本 JSON 安装实例（HMCL「通过版本 JSON 安装」同款，GP-5730）。

用户手里常有一份单独的版本 JSON：论坛分享的自定义版本、朋友导出的
加载器配置、不在官方清单里的第三方构建。把它落成 versions/<id>/<id>.json
后，复用 Installer 的补全链（父版本 → 客户端 jar → 依赖库 → 资源）。
"""
from __future__ import annotations

import json
from pathlib import Path

from . import manifest, utils
from .installer import Installer, InstallError
from .version_ops import VersionOpError, sanitize_id

# 版本 JSON 都在几十 KB 量级，8MB 上限足够宽松，还能挡住误选的大文件。
MAX_JSON_BYTES = 8 * 1024 * 1024


def looks_like_version_json(data) -> bool:
    """判断解析后的对象是否像一份 Minecraft 版本 JSON。

    原版 / 加载器 JSON 一定有 mainClass；补丁式 JSON（只写差量）至少有
    inheritsFrom。两者都没有的（普通配置文件等）不认。
    """
    if not isinstance(data, dict):
        return False
    if not str(data.get("id") or "").strip():
        return False
    return bool(str(data.get("mainClass") or "").strip()
                or str(data.get("inheritsFrom") or "").strip())


def read_version_json(path) -> dict:
    """读取并校验一份版本 JSON 文件，返回解析结果；不合格给可读报错。"""
    p = Path(path)
    if not p.is_file():
        raise InstallError(f"文件不存在: {p}")
    try:
        if p.stat().st_size > MAX_JSON_BYTES:
            raise InstallError(f"文件过大，不像版本 JSON: {p.name}")
        data = json.loads(p.read_text(encoding="utf-8-sig"))
    except InstallError:
        raise
    except (OSError, ValueError) as e:
        raise InstallError(f"无法解析 JSON 文件 {p.name}: {e}") from e
    if not isinstance(data, dict):
        raise InstallError(f"{p.name} 不是版本 JSON（顶层不是对象）")
    if not looks_like_version_json(data):
        raise InstallError(
            f"{p.name} 不是 Minecraft 版本 JSON（缺少 id 与 mainClass / inheritsFrom）")
    return data


def default_version_id(path, vjson: dict | None = None) -> str:
    """默认版本名：JSON 里的 id，退而求其次用文件名（去扩展名）。"""
    vid = str((vjson or {}).get("id") or "").strip()
    return vid or Path(path).stem


def install_from_json(installer: Installer, path, name: str = "",
                      force: bool = False) -> str:
    """把一份版本 JSON 装成完整可玩的版本，返回落地后的版本 id。

    name 为空时用 JSON 里的 id。目标名会写回 JSON 的 id 字段，
    保证 versions/<id>/<id>.json 与内容一致。
    """
    inst = installer.instance
    vjson = read_version_json(path)
    try:
        target = sanitize_id(name or default_version_id(path, vjson))
    except VersionOpError as e:
        raise InstallError(str(e)) from e

    if inst.has_version(target) and not force:
        raise InstallError(f"版本 {target} 已存在，请换个名称或先卸载")

    parent = str(vjson.get("inheritsFrom") or "").strip()
    if parent == target:
        raise InstallError(f"版本 JSON 的 inheritsFrom 不能指向自己（{target}）")
    if parent and not inst.has_version(parent):
        # 提前确认父版本可得，避免装到一半才发现继承链断了，
        # 在实例目录里留下残缺的版本文件夹。
        try:
            manifest.get_version_json(installer.dm, parent)
        except manifest.VersionNotFound as e:
            raise InstallError(
                f"该版本 JSON 继承自 {parent}，但官方版本列表里找不到它。"
                f"请先手动安装 {parent} 再导入。") from e

    from . import diskspace
    diskspace.ensure_free(inst.path, what=f"安装版本 {target}")
    inst.ensure_standard_dirs()

    vjson = dict(vjson)
    vjson["id"] = target
    installer._note(f"通过版本 JSON 安装 {target}（来自 {Path(path).name}）")
    installer._install_json(target, vjson, force=force)
    utils.log.info("版本 JSON %s 安装完成 -> %s", path, target)
    return target
