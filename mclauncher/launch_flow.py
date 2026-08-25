# -*- coding: utf-8 -*-
"""启动前组装：隔离、全局 Mod、版本设置、启动脚本。"""
from __future__ import annotations

import os
import subprocess

from . import global_mods, utils, version_settings
from .argsplit import split_args
from .config import CONFIG


def prepare(instance, version_id, extra_game_args=None, memory_mb=None):
    settings = version_settings.load(instance, version_id)
    gdir = version_settings.apply_isolation(instance, version_id, settings)
    n = global_mods.apply(gdir / "mods")
    mem = settings.get("memory_mb") or memory_mb
    mem_auto = None
    if not mem or int(mem) <= 0:
        # memory_mb <= 0 表示自动：按系统当前可用内存动态分配
        from . import memory as memory_mod
        mem_auto = memory_mod.auto_memory()
        mem = mem_auto["memory_mb"]
    extras = [str(a) for a in (extra_game_args or []) if a not in (None, "")]
    extras += split_args(settings.get("game_args"))
    if settings.get("server") and "--server" not in extras:
        extras += ["--server", str(settings["server"])]
        extras += ["--port", str(settings.get("port") or 25565)]
    mode = settings.get("window_mode") or CONFIG.get("window_mode") or "window"
    if mode in version_settings.FULLSCREEN_MODES and "--fullscreen" not in extras:
        extras.append("--fullscreen")
    from . import gc as gc_mod
    jvm = gc_mod.apply(settings.get("gc") or CONFIG.get("gc_preset") or "auto",
                       settings.get("jvm_args") or "")
    return {
        "settings": settings,
        "game_dir": gdir,
        "memory_mb": mem,
        "memory_auto": mem_auto,
        "extra_game_args": extras,
        "jvm_args": jvm,
        "priority": settings.get("process_priority") or CONFIG.get("default_priority") or "normal",
        "global_mods": n,
        "pre_launch_wait": settings.get("pre_launch_wait", True),
        "login_account": settings.get("login_account") or "",
        "nide8_id": settings.get("nide8_id") or "",
        "auth_server": settings.get("auth_server") or "",
        "window_mode": mode,
        "window_width": _positive(settings.get("window_width")),
        "window_height": _positive(settings.get("window_height")),
        "window_title": (settings.get("window_title") or "").strip(),
    }


def _positive(value):
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def resolve_resolution(prep, width=None, height=None):
    """版本设置的窗口大小优先于调用方传入的全局分辨率；全屏时兜底到 1280x720。"""
    w = prep.get("window_width") or width
    h = prep.get("window_height") or height
    if (prep.get("window_mode") or "window") in version_settings.FULLSCREEN_MODES:
        w = max(int(w or 0), 1280)
        h = max(int(h or 0), 720)
    return w, h


def run_hook(command: str, cwd, log=None, wait: bool = True) -> int:
    cmd = (command or "").strip()
    if not cmd:
        return 0
    if log:
        log(f"运行启动脚本: {cmd}")
    if not wait:
        subprocess.Popen(
            cmd, cwd=str(cwd), shell=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )
        return 0
    proc = subprocess.run(
        cmd, cwd=str(cwd), shell=True,
        capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    if log and proc.stdout:
        for line in proc.stdout.splitlines()[:40]:
            log(line)
    if proc.returncode and log:
        log(f"脚本退出码 {proc.returncode}: {(proc.stderr or '')[:300]}")
    return proc.returncode
