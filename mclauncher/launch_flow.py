# -*- coding: utf-8 -*-
"""启动前组装：隔离、全局 Mod、版本设置、启动脚本。"""
from __future__ import annotations

import os
import subprocess

from . import global_mods, gpu, utils, version_settings
from .argsplit import split_args
from .config import CONFIG


def auto_memory_mb(total_mb=0, avail_mb=0) -> int:
    """按物理内存自动分配 Xmx（对标 PCL2「自动设置内存」）。

    取可用内存的 60%，下限 1024 MB，上限 min(总内存一半, 12288 MB)，
    向下 256 对齐。读不到内存信息时回落 4096。
    """
    try:
        total = int(total_mb or 0)
        avail = int(avail_mb or 0)
    except (TypeError, ValueError):
        return 4096
    if total <= 0 or avail <= 0:
        return 4096
    cap = min(total // 2, 12288)
    mem = min(int(avail * 0.6), cap)
    mem = (mem // 256) * 256
    return max(1024, mem)


def resolve_memory(settings: dict, requested_mb=None) -> tuple[int, str]:
    """内存优先级：版本设置 > 全局自动分配（auto_memory）> 手动值。

    返回 (memory_mb, source)，source ∈ {"version", "auto", "manual"}。
    """
    if (settings or {}).get("memory_mb"):
        return int(settings["memory_mb"]), "version"
    if CONFIG.get("auto_memory"):
        from .sysinfo import memory_info
        try:
            info = memory_info() or {}
        except Exception:
            info = {}
        return auto_memory_mb(info.get("total_mb"), info.get("avail_mb")), "auto"
    return int(requested_mb or 0), "manual"


def prepare(instance, version_id, extra_game_args=None, memory_mb=None):
    settings = version_settings.load(instance, version_id)
    gdir = version_settings.apply_isolation(instance, version_id, settings)
    n = global_mods.apply(gdir / "mods")
    from . import game_options
    game_lang = game_options.ensure_lang(
        gdir, mc_version=game_options.base_mc_version(instance, version_id))
    mem, mem_src = resolve_memory(settings, memory_mb)
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
        "memory_mb": mem,
        "memory_source": mem_src,
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
        "wrapper": str(settings.get("wrapper") or "").strip(),
        "game_lang": game_lang,
        "gpu_mode": gpu.resolve_mode(settings),
        "renderer": gpu.resolve_renderer(settings),
        "show_log": resolve_show_log(settings),
        "env_vars": parse_env_vars(settings.get("env_vars") or ""),
        "use_system_glfw": bool(settings.get("use_system_glfw")),
        "use_system_openal": bool(settings.get("use_system_openal")),
        "natives_dir": str(settings.get("natives_dir") or "").strip(),
    }


def parse_env_vars(text) -> dict:
    """把版本设置「环境变量」文本解析成 dict（HMCL 同款语义）。

    按引号感知分词（和 JVM 参数一样），每个词按第一个 = 拆键值；
    没有 = 的词视为值为空串的变量。如: A=1 "B=hello world" C
    """
    out = {}
    for token in split_args(text):
        key, _sep, value = token.partition("=")
        key = key.strip()
        if key:
            out[key] = value
    return out


#: HMCL LibraryAnalyzer 同款：按库坐标推断已装加载器，导出 INST_* 标记
_LOADER_ENV_MARKS = (
    ("INST_CLEANROOM", ("com.cleanroommc:cleanroom",)),
    ("INST_NEOFORGE", ("net.neoforged:neoforge", "net.neoforged.fancymodloader")),
    ("INST_FORGE", ("net.minecraftforge:forge", "net.minecraftforge:fmlloader")),
    ("INST_LITELOADER", ("com.mumfrey:liteloader",)),
    ("INST_FABRIC", ("net.fabricmc:fabric-loader",)),
    ("INST_QUILT", ("org.quiltmc:quilt-loader",)),
    ("INST_OPTIFINE", ("optifine:optifine",)),
)


def loader_env_flags(resolved) -> list:
    """从 resolved 版本 json 的库列表推断 INST_FORGE/INST_FABRIC 等标记名。"""
    names = " ".join(
        str(lib.get("name") or "").lower()
        for lib in (resolved or {}).get("libraries") or []
    )
    return [flag for flag, marks in _LOADER_ENV_MARKS
            if any(m in names for m in marks)]


def game_env(prep, gpu_env=None, *, instance=None, version_id="",
             java_exe="", game_dir=None, resolved=None):
    """组装游戏进程环境：INST_*（HMCL 同款）→ 显卡增量 → 版本设置「环境变量」。

    用户显式写的变量优先级最高（HMCL putAll 在最后）。没有任何增量时
    返回 None，调用方按「继承启动器环境」处理。"""
    merged = {}
    if instance is not None and version_id:
        vdir = instance.versions_dir() / version_id
        merged.update({
            "INST_NAME": str(version_id),
            "INST_ID": str(version_id),
            "INST_DIR": str(vdir),
            "INST_MC_DIR": str(game_dir or instance.path),
            "INST_JAVA": str(java_exe or ""),
        })
        for flag in loader_env_flags(resolved):
            merged[flag] = "1"
    merged.update(gpu_env or {})
    merged.update((prep or {}).get("env_vars") or {})
    if not merged:
        return None
    env = os.environ.copy()
    env.update(merged)
    return env


def resolve_show_log(settings: dict | None) -> bool:
    """启动时是否自动弹日志窗口（HMCL「显示日志」同款）。

    版本设置 "on"/"off" 覆盖全局；空串跟随全局 show_log_window。"""
    v = str((settings or {}).get("show_log") or "").strip().lower()
    if v in ("on", "true", "1"):
        return True
    if v in ("off", "false", "0"):
        return False
    return bool(CONFIG.get("show_log_window", False))


def apply_wrapper(cmd, wrapper: str) -> list:
    """包装器命令（HMCL 同款）：如 gamemoderun / prime-run / optirun，
    拆参后前缀到启动命令，让包装器进程带起 Java。空串原样返回。"""
    w = str(wrapper or "").strip()
    if not w:
        return list(cmd)
    return split_args(w) + list(cmd)


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
