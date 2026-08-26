# -*- coding: utf-8 -*-
"""NeoForge 版本前缀推导：installer 与 loader_meta 共用的唯一实现。"""
from __future__ import annotations

from .manifest import mc_version_tuple

# 1.20.1 时代 NeoForge 沿用 Forge 的 47.1.x 编号
_LEGACY_PREFIXES = {"1.20.1": "47.1"}


def neoforge_version_prefix(mc_version: str) -> str | None:
    """MC 版本 -> NeoForge maven 版本前缀。

    1.20.1          -> "47.1"（历史特例）
    1.20.2 – 1.21.x -> 去掉开头的 "1."：1.21.1 -> "21.1"、1.21 -> "21.0"、1.20.4 -> "20.4"
    年式 26.1/26.2  -> 原样（保留第一段）
    更老 / 无法解析 -> None
    """
    mc = str(mc_version or "").strip()
    if not mc:
        return None
    if mc in _LEGACY_PREFIXES:
        return _LEGACY_PREFIXES[mc]
    core = mc.split("-")[0].split("+")[0]
    tup = mc_version_tuple(core)
    if not tup:
        return None
    if tup[0] == 1:
        if tup < (1, 20, 2):
            return None
        parts = core.split(".")[1:]
        if len(parts) == 1:
            parts.append("0")
        return ".".join(parts[:2])
    if tup[0] >= 20:
        # 年式版本号：26.1 -> "26.1"，26.1.2 -> "26.1.2"
        return core
    return None
