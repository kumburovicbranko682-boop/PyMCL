# -*- coding: utf-8 -*-
"""内存自动分配（对标 PCL2「自动设置内存」/ HMCL 自动分配）。

启动时按系统当前可用内存动态决定 -Xmx，不用用户手动拖滑条。
memory_mb <= 0 在各门面里统一表示「自动」。
"""
from __future__ import annotations

# 自动分配策略参数
_FRACTION = 0.6          # 取可用内存的六成
_SYSTEM_RESERVE_MB = 2048  # 永远给系统留 2 GB
_MIN_MB = 1024
_MAX_MB = 12288          # 再大对 MC 没有收益，GC 反而变慢
_STEP_MB = 256
_FALLBACK_MB = 4096      # 读不到系统内存时的兜底


def auto_memory(total_mb: int | None = None, avail_mb: int | None = None) -> dict:
    """计算自动分配的内存。返回 {memory_mb, total_mb, avail_mb, fallback}。

    total_mb / avail_mb 不传时读系统真实值；读不到就回 4096 并标记 fallback。
    """
    if total_mb is None or avail_mb is None:
        from . import sysinfo
        info = sysinfo.memory_info()
        total_mb = int(info.get("total_mb") or 0)
        avail_mb = int(info.get("avail_mb") or 0)
    total_mb = int(total_mb or 0)
    avail_mb = int(avail_mb or 0)
    if avail_mb <= 0:
        return {"memory_mb": _FALLBACK_MB, "total_mb": total_mb,
                "avail_mb": avail_mb, "fallback": True}
    value = avail_mb * _FRACTION
    if total_mb > 0:
        value = min(value, total_mb - _SYSTEM_RESERVE_MB)
    value = min(value, _MAX_MB)
    value = max(value, _MIN_MB)
    value = int(value // _STEP_MB * _STEP_MB) or _MIN_MB
    return {"memory_mb": value, "total_mb": total_mb,
            "avail_mb": avail_mb, "fallback": False}


def auto_memory_mb(total_mb: int | None = None,
                   avail_mb: int | None = None) -> int:
    return auto_memory(total_mb, avail_mb)["memory_mb"]
