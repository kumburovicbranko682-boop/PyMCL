# -*- coding: utf-8 -*-
"""磁盘空间检查（对标 HMCL / PCL2 的「磁盘空间不足」提示）。

满盘时下载/解压会在半路抛出难懂的 "No space left on device"，
还可能留下半截文件。装版本 / 装整合包前先看一眼剩余空间，
不够就用人话报错（启动侧的磁盘检查在 preflight 里已有）。
"""

from __future__ import annotations

import shutil
from pathlib import Path

from . import utils

# 装一个现代版本（assets + libraries + natives）实际要 300MB-1GB；
# 低于这个数基本装不完，直接拦。启动侧的检查在 preflight 里已有。
MIN_INSTALL_FREE = 500 * 1024 * 1024


class DiskSpaceError(Exception):
    pass


def free_bytes(path) -> int:
    """path 所在磁盘的剩余字节数；拿不到（路径不存在等）返回 -1。"""
    p = Path(path)
    while not p.exists():
        parent = p.parent
        if parent == p:
            return -1
        p = parent
    try:
        return shutil.disk_usage(p).free
    except OSError:
        return -1


def ensure_free(path, need_bytes: int = MIN_INSTALL_FREE, what: str = "安装"):
    """剩余空间不足 need_bytes 时抛 DiskSpaceError。返回剩余字节数。"""
    free = free_bytes(path)
    if free < 0:
        return free
    if free < need_bytes:
        raise DiskSpaceError(
            f"磁盘空间不足，无法{what}：{Path(path)} 所在磁盘仅剩 "
            f"{utils.format_size(free)}（至少需要 {utils.format_size(need_bytes)}）。"
            f"请清理磁盘后重试。")
    return free
