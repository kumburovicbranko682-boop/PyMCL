# -*- coding: utf-8 -*-
"""独显/核显启动（PCL2「尝试使用独立显卡运行游戏」/ HMCL 双显卡 offload 同款）。

三种模式：
- auto       不干预，交给系统默认策略
- discrete   强制独显：Windows 给 java.exe 写 GpuPreference=2（高性能）
             注册表；Linux 注入 NVIDIA PRIME render offload（专有驱动）
             或 DRI_PRIME=1（Mesa/AMD/nouveau）
- integrated 强制核显：Windows 写 GpuPreference=1（省电）；Linux 显式
             把 offload 变量关掉（DRI_PRIME=0）

macOS 没有等价的进程级开关（系统自己按 NSSupportsAutomaticGraphicsSwitching
决定），这里不做处理。"""
from __future__ import annotations

from pathlib import Path

from . import utils

MODES = ("auto", "discrete", "integrated")
MODE_LABELS = {
    "auto": "自动（系统默认）",
    "discrete": "强制独立显卡（高性能）",
    "integrated": "强制核芯显卡（省电）",
}

# Windows 注册表：HKCU 下按可执行文件路径记录 GPU 偏好，
# GpuPreference=1 省电（核显）/ 2 高性能（独显）。PCL2 同款做法。
_WIN_REG_PATH = r"Software\Microsoft\DirectX\UserGpuPreferences"
_WIN_PREF = {"discrete": "GpuPreference=2;", "integrated": "GpuPreference=1;"}

# Linux NVIDIA 专有驱动的 PRIME render offload 三件套（HMCL 同款）
_NV_OFFLOAD = {
    "__NV_PRIME_RENDER_OFFLOAD": "1",
    "__GLX_VENDOR_LIBRARY_NAME": "nvidia",
    "__VK_LAYER_NV_optimus": "NVIDIA_only",
}


def normalize_mode(mode) -> str:
    m = str(mode or "auto").strip().lower()
    return m if m in MODES else "auto"


def resolve_mode(settings: dict | None) -> str:
    """版本设置优先，其次全局配置，都空则 auto。"""
    from .config import CONFIG
    m = (settings or {}).get("gpu") or CONFIG.get("gpu_mode") or "auto"
    return normalize_mode(m)


def classify(name) -> str:
    """按显卡名称粗分厂商：nvidia / amd / intel / other。"""
    low = str(name or "").lower()
    if "nvidia" in low or "geforce" in low or "quadro" in low or "rtx" in low \
            or "gtx" in low:
        return "nvidia"
    if "amd" in low or "radeon" in low or "ati " in low or low.startswith("ati"):
        return "amd"
    if "intel" in low or "iris" in low or " uhd" in low or low.startswith("uhd") \
            or " hd graphics" in low:
        return "intel"
    return "other"


def list_gpus() -> list[dict]:
    """枚举本机 GPU（复用 sysinfo 的探测与缓存），附厂商分类。"""
    try:
        from .sysinfo import _gpu_info
        rows = _gpu_info() or []
    except Exception:
        rows = []
    out = []
    for row in rows:
        name = str((row or {}).get("name") or "").strip()
        if not name:
            continue
        out.append({"name": name, "vendor": classify(name)})
    return out


def has_dual_gpu(gpus: list[dict] | None = None) -> bool:
    rows = gpus if gpus is not None else list_gpus()
    return len(rows) >= 2


def offload_env(mode: str, gpus: list[dict] | None = None) -> dict:
    """Linux 下按模式给出要注入的环境变量增量。"""
    mode = normalize_mode(mode)
    if mode == "integrated":
        return {"DRI_PRIME": "0", "__NV_PRIME_RENDER_OFFLOAD": "0"}
    if mode != "discrete":
        return {}
    rows = gpus if gpus is not None else list_gpus()
    if any(g.get("vendor") == "nvidia" for g in rows):
        return dict(_NV_OFFLOAD)
    return {"DRI_PRIME": "1"}


def apply_windows_preference(java_exe, mode: str) -> str:
    """Windows：给 Java 可执行文件写/清 GpuPreference 注册表。返回日志说明。"""
    mode = normalize_mode(mode)
    exe = str(java_exe or "").strip()
    if not exe:
        return ""
    try:
        target = str(Path(exe).resolve())
    except OSError:
        target = exe
    try:
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _WIN_REG_PATH) as key:
            if mode == "auto":
                try:
                    winreg.DeleteValue(key, target)
                except FileNotFoundError:
                    pass
                return ""
            winreg.SetValueEx(key, target, 0, winreg.REG_SZ, _WIN_PREF[mode])
        kind = "高性能独显" if mode == "discrete" else "省电核显"
        return f"显卡偏好: 已为 {target} 写入 {kind}（{_WIN_PREF[mode]}）"
    except Exception as e:  # 注册表失败不拦启动
        utils.log.warning("写显卡偏好注册表失败: %s", e)
        return f"显卡偏好写入失败（不影响启动）: {e}"


def launch_env(mode, java_exe=None) -> tuple[dict, str]:
    """按模式给出 (环境变量增量, 日志说明)。auto 或不支持的平台返回空。"""
    mode = normalize_mode(mode)
    if mode == "auto":
        return {}, ""
    if utils.IS_WINDOWS:
        return {}, apply_windows_preference(java_exe, mode)
    if utils.IS_MAC:
        return {}, "显卡偏好: macOS 由系统自动切换，此设置无效"
    env = offload_env(mode)
    if not env:
        return {}, ""
    pairs = " ".join(f"{k}={v}" for k, v in sorted(env.items()))
    label = "独显" if mode == "discrete" else "核显"
    return env, f"显卡偏好: 强制{label}（{pairs}）"
