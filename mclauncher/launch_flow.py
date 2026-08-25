# -*- coding: utf-8 -*-
"""启动前组装：隔离、全局 Mod、版本设置、启动脚本。"""
from __future__ import annotations

import os
import subprocess

from . import global_mods, utils, version_settings
from .argsplit import split_args
from .config import CONFIG


def supports_quickplay_multiplayer(instance, version_id) -> bool:
    """1.20+ 的版本 JSON 声明了 quickPlayMultiplayer；同时原版移除了 --server。"""
    import json as _json
    from . import manifest as manifest_mod
    try:
        vjson = instance.version_json(version_id)
    except Exception:
        return False
    if not isinstance(vjson, dict):
        return False
    try:
        resolved = manifest_mod.resolve_inherits(vjson, lambda pid: instance.version_json(pid))
    except Exception:
        resolved = vjson
    game_args = (resolved.get("arguments") or {}).get("game") or []
    return "quickPlayMultiplayer" in _json.dumps(game_args)


def _extract_server(extras: list) -> tuple[str, str, list]:
    """把 extras 里的 --server/--port 摘出来，返回 (host, port, 余下参数)。"""
    host = port = ""
    rest = []
    i = 0
    while i < len(extras):
        arg = extras[i]
        if arg == "--server" and i + 1 < len(extras):
            host = str(extras[i + 1])
            i += 2
            continue
        if arg == "--port" and i + 1 < len(extras):
            port = str(extras[i + 1])
            i += 2
            continue
        rest.append(arg)
        i += 1
    return host, port, rest


def server_join_args(instance, version_id, host, port="") -> list:
    """直连服务器参数：1.20+ 用 --quickPlayMultiplayer，旧版用 --server/--port。"""
    host = str(host or "").strip()
    if not host:
        return []
    port = str(port or "").strip()
    if ":" in host and host.count(":") == 1:
        left, _, tail = host.rpartition(":")
        if tail.isdigit():
            host, port = left, port or tail
    if supports_quickplay_multiplayer(instance, version_id):
        addr = f"{host}:{port}" if port else host
        return ["--quickPlayMultiplayer", addr]
    return ["--server", host, "--port", port or "25565"]


def prepare(instance, version_id, extra_game_args=None, memory_mb=None):
    settings = version_settings.load(instance, version_id)
    gdir = version_settings.apply_isolation(instance, version_id, settings)
    n = global_mods.apply(gdir / "mods")
    # 首次启动把游戏语言对齐启动器语言（玩家改过就不碰）
    from . import game_options
    game_options.ensure_language(gdir, version_id)
    mem = settings.get("memory_mb") or memory_mb
    extras = [str(a) for a in (extra_game_args or []) if a not in (None, "")]
    extras += split_args(settings.get("game_args"))
    # 直连服务器：调用方传的 --server 优先，其次版本设置；按版本翻译成正确参数
    host, port, extras = _extract_server(extras)
    if not host and settings.get("server"):
        host, port = str(settings["server"]), str(settings.get("port") or "")
    if host and "--quickPlayMultiplayer" not in extras:
        extras += server_join_args(instance, version_id, host, port)
    mode = settings.get("window_mode") or CONFIG.get("window_mode") or "window"
    if mode in version_settings.FULLSCREEN_MODES and "--fullscreen" not in extras:
        extras.append("--fullscreen")
    from . import gc as gc_mod
    jvm = gc_mod.apply(settings.get("gc") or CONFIG.get("gc_preset") or "auto",
                       settings.get("jvm_args") or "")
    # 包装器：版本设置优先，空则用全局配置（对齐 HMCL 的「包裹命令」）
    wrapper = split_args(settings.get("wrapper") or CONFIG.get("wrapper_command") or "")
    return {
        "settings": settings,
        "game_dir": gdir,
        "memory_mb": mem,
        "extra_game_args": extras,
        "jvm_args": jvm,
        "wrapper": wrapper,
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
