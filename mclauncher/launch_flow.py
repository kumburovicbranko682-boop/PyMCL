# -*- coding: utf-8 -*-
"""启动前组装：隔离、全局 Mod、版本设置、游戏语言、启动脚本。"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from . import global_mods, utils, version_settings
from .argsplit import split_args
from .config import CONFIG

_MC_VER = re.compile(r"(?<![\d.])1\.(\d{1,2})(?:\.\d{1,2})?(?![\d])")


def _mc_minor(version_id: str) -> int | None:
    """从版本 id 里解析 MC 次版本号（"1.8.9-forge…" -> 8），拿不到返回 None。"""
    m = _MC_VER.search(str(version_id or ""))
    return int(m.group(1)) if m else None


def _lang_code_for(version_id: str, lang: str) -> str:
    """1.10 及以前 options.txt 的语言代码带大写地区（zh_CN），1.11+ 全小写。"""
    minor = _mc_minor(version_id)
    if minor is not None and minor < 11:
        head, _, region = lang.partition("_")
        if region:
            return f"{head}_{region.upper()}"
    return lang


def ensure_game_language(game_dir, version_id: str) -> str | None:
    """首次启动写 options.txt 的 lang（对标 PCL2/HMCL：新版本第一次
    进游戏就是启动器语言，而不是英文）。

    只在 options.txt 不存在时写，绝不覆盖玩家在游戏里改过的设置。
    config game_lang: auto=跟随启动器 / zh_cn / en_us / off=不写入。
    返回写入的语言代码；没写返回 None。
    """
    pref = str(CONFIG.get("game_lang") or "auto").strip().lower()
    if pref in ("off", "none"):
        return None
    lang = pref
    if lang == "auto":
        from .i18n import current_language
        lang = "zh_cn" if str(current_language()).lower().startswith("zh") else ""
    if not lang or lang == "en_us":
        return None   # 原版默认就是英文，不需要写
    path = Path(game_dir) / "options.txt"
    if path.exists():
        return None
    lang = _lang_code_for(version_id, lang)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"lang:{lang}\n", encoding="utf-8")
    except OSError as e:
        utils.log.warning("写入游戏语言失败 %s: %s", path, e)
        return None
    return lang


def prepare(instance, version_id, extra_game_args=None, memory_mb=None):
    settings = version_settings.load(instance, version_id)
    gdir = version_settings.apply_isolation(instance, version_id, settings)
    n = global_mods.apply(gdir / "mods")
    game_lang = ensure_game_language(gdir, version_id)
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
        # 用户可能直接填 host:port，拆开传，避免 --server host:port 连不上
        srv = str(settings["server"]).strip()
        host, _, port = srv.partition(":")
        extras += ["--server", host or srv]
        extras += ["--port", str(port.strip() or settings.get("port") or 25565)]
    mode = settings.get("window_mode") or CONFIG.get("window_mode") or "window"
    if mode in version_settings.FULLSCREEN_MODES and "--fullscreen" not in extras:
        extras.append("--fullscreen")
    from . import gc as gc_mod
    jvm = gc_mod.apply(settings.get("gc") or CONFIG.get("gc_preset") or "auto",
                       settings.get("jvm_args") or "")
    return {
        "settings": settings,
        "game_dir": gdir,
        "game_lang": game_lang,
        "memory_mb": mem,
        "memory_auto": mem_auto,
        "extra_game_args": extras,
        "jvm_args": jvm,
        "priority": settings.get("process_priority") or CONFIG.get("default_priority") or "normal",
        "global_mods": n,
        "pre_launch_wait": settings.get("pre_launch_wait", True),
        "wrapper": split_args(settings.get("wrapper") or ""),
        "env": parse_env_vars(settings.get("env_vars") or ""),
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


def parse_env_vars(text: str) -> dict:
    """解析「每行一个 KEY=VALUE」的环境变量文本。

    忽略空行与 # 注释；VALUE 里允许再出现 =。
    """
    env = {}
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key:
            env[key] = value.strip()
    return env


def wrap_command(cmd, wrapper) -> list:
    """把包裹命令（如 optirun / mangohud）前缀到启动命令前。"""
    tokens = [str(t) for t in (wrapper or []) if str(t).strip()]
    return tokens + list(cmd) if tokens else list(cmd)


def build_env(overrides) -> "dict | None":
    """有自定义环境变量时返回合并后的完整环境，否则 None（进程用默认环境）。"""
    if not overrides:
        return None
    env = os.environ.copy()
    env.update({str(k): str(v) for k, v in overrides.items()})
    return env


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
