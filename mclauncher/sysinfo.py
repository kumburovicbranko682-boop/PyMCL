# -*- coding: utf-8 -*-
"""采集本机配置：系统 / CPU / 内存 / 显卡 / 磁盘 / Java / 实例。心跳与反馈共用。"""

from __future__ import annotations

import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import APP_VERSION, utils
from .config import CONFIG

_LOCK = threading.Lock()
_CACHE = {"t": 0.0, "data": None}

# CPU / GPU 探测要各拉一个 PowerShell（WMI），一块硬件不会热插拔，
# 进程内缓存半小时足够。反馈心跳以前每 2 分钟就重探一遍——常驻
# CPU/磁盘 churn 就是这么来的。
_STATIC_TTL = 1800.0
_STATIC_LOCK = threading.Lock()
_STATIC = {"cpu": None, "cpu_t": 0.0, "gpus": None, "gpus_t": 0.0}


def _run(cmd, timeout=7) -> str:
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if utils.IS_WINDOWS else 0
        proc = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout,
            creationflags=flags,
        )
        raw = (proc.stdout or b"") + (proc.stderr or b"")
        return raw.decode("utf-8", errors="replace").strip()
    except Exception:
        return ""


def _ps(script: str, timeout=8) -> str:
    if not utils.IS_WINDOWS:
        return ""
    return _run(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy", "Bypass",
            "-Command",
            "[Console]::OutputEncoding=[Text.UTF8Encoding]::new();"
            "$OutputEncoding=[Console]::OutputEncoding;"
            + script,
        ],
        timeout=timeout,
    )


def _ps_json(script: str, timeout=8):
    text = _ps(script, timeout=timeout)
    if not text:
        return None
    start = text.find("[")
    brace = text.find("{")
    if start < 0 and brace < 0:
        return None
    if start < 0 or (brace >= 0 and brace < start):
        start = brace
    try:
        return json.loads(text[start:])
    except Exception:
        return None


def _winreg_value(hive, path, name, default=""):
    if not utils.IS_WINDOWS:
        return default
    try:
        import winreg
        key = winreg.OpenKey(hive, path)
        try:
            val, _ = winreg.QueryValueEx(key, name)
            return str(val or default)
        finally:
            winreg.CloseKey(key)
    except Exception:
        return default


def _cpu_info() -> dict:
    now = time.time()
    with _STATIC_LOCK:
        if _STATIC["cpu"] is not None and now - _STATIC["cpu_t"] < _STATIC_TTL:
            return dict(_STATIC["cpu"])
    info = _probe_cpu()
    with _STATIC_LOCK:
        _STATIC["cpu"] = info
        _STATIC["cpu_t"] = time.time()
    return dict(info)


def _gpu_info() -> list:
    now = time.time()
    with _STATIC_LOCK:
        if _STATIC["gpus"] is not None and now - _STATIC["gpus_t"] < _STATIC_TTL:
            return list(_STATIC["gpus"])
    gpus = _probe_gpus()
    with _STATIC_LOCK:
        _STATIC["gpus"] = gpus
        _STATIC["gpus_t"] = time.time()
    return list(gpus)


def _os_info() -> dict:
    display = platform.platform()
    build = platform.version() or ""
    release = platform.release() or ""
    product = ""
    if utils.IS_WINDOWS:
        import winreg
        product = _winreg_value(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows NT\CurrentVersion",
            "ProductName",
        )
        disp = _winreg_value(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows NT\CurrentVersion",
            "DisplayVersion",
        )
        build_n = _winreg_value(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows NT\CurrentVersion",
            "CurrentBuild",
        ) or _winreg_value(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows NT\CurrentVersion",
            "CurrentBuildNumber",
        )
        ubr = _winreg_value(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows NT\CurrentVersion",
            "UBR",
        )
        bits = []
        if product:
            bits.append(product)
        if disp:
            bits.append(disp)
        if build_n:
            bits.append(build_n + (f".{ubr}" if ubr else ""))
        if bits:
            display = " ".join(bits)
        build = build_n or build
        release = disp or release
    return {
        "name": utils.OS_NAME,
        "platform": platform.platform(),
        "system": platform.system(),
        "release": release,
        "version": build,
        "display": display,
        "arch": utils.ARCH,
        "machine": platform.machine() or "",
    }


def _probe_cpu() -> dict:
    name = platform.processor() or ""
    if utils.IS_WINDOWS:
        import winreg
        name = _winreg_value(
            winreg.HKEY_LOCAL_MACHINE,
            r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
            "ProcessorNameString",
            name,
        ) or name
    elif utils.IS_MAC:
        name = _run(["sysctl", "-n", "machdep.cpu.brand_string"], timeout=3) or name
    else:
        try:
            txt = Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="replace")
            for line in txt.splitlines():
                if line.lower().startswith("model name"):
                    name = line.split(":", 1)[-1].strip()
                    break
        except OSError:
            pass
    logical = os.cpu_count() or 0
    physical = 0
    if utils.IS_WINDOWS:
        data = _ps_json(
            "Get-CimInstance Win32_Processor | "
            "Select-Object Name,NumberOfCores,NumberOfLogicalProcessors | "
            "ConvertTo-Json -Compress",
            timeout=6,
        )
        rows = data if isinstance(data, list) else ([data] if isinstance(data, dict) else [])
        cores = 0
        logicals = 0
        for row in rows:
            if not isinstance(row, dict):
                continue
            cores += int(row.get("NumberOfCores") or 0)
            logicals += int(row.get("NumberOfLogicalProcessors") or 0)
            if row.get("Name"):
                name = str(row["Name"]).strip() or name
        physical = cores
        if logicals:
            logical = logicals
    elif utils.IS_MAC:
        physical = int(_run(["sysctl", "-n", "hw.physicalcpu"], timeout=2) or 0)
        logical = int(_run(["sysctl", "-n", "hw.logicalcpu"], timeout=2) or logical or 0)
    else:
        try:
            physical = len({
                line.split(":")[-1].strip()
                for line in Path("/proc/cpuinfo").read_text(
                    encoding="utf-8", errors="replace"
                ).splitlines()
                if line.startswith("physical id")
            })
        except OSError:
            physical = 0
    return {
        "name": " ".join((name or "").split()),
        "cores_logical": logical,
        "cores_physical": physical,
    }


def memory_info() -> dict:
    """公开入口：系统内存信息 {total_mb, avail_mb, load_percent, …}。"""
    return _memory_info()


def _memory_info() -> dict:
    total = avail = 0
    load = 0
    if utils.IS_WINDOWS:
        import ctypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
            total = int(stat.ullTotalPhys)
            avail = int(stat.ullAvailPhys)
            load = int(stat.dwMemoryLoad)
    elif utils.IS_MAC:
        page = int(_run(["sysctl", "-n", "hw.pagesize"], timeout=2) or 4096)
        total = int(_run(["sysctl", "-n", "hw.memsize"], timeout=2) or 0)
        vm = _run(["vm_stat"], timeout=3)
        free_pages = 0
        for line in vm.splitlines():
            if "Pages free" in line or "Pages speculative" in line:
                num = "".join(ch for ch in line.split(":")[-1] if ch.isdigit())
                free_pages += int(num or 0)
        avail = free_pages * page
        if total:
            load = int(round((total - avail) * 100 / total))
    else:
        try:
            txt = Path("/proc/meminfo").read_text(encoding="utf-8", errors="replace")
            kv = {}
            for line in txt.splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    num = "".join(ch for ch in v if ch.isdigit())
                    kv[k] = int(num or 0) * 1024
            total = kv.get("MemTotal", 0)
            avail = kv.get("MemAvailable") or kv.get("MemFree", 0)
            if total:
                load = int(round((total - avail) * 100 / total))
        except OSError:
            pass
    return {
        "total_mb": int(total // (1024 * 1024)) if total else 0,
        "avail_mb": int(avail // (1024 * 1024)) if avail else 0,
        "load_percent": load,
        "total_bytes": total,
        "avail_bytes": avail,
    }


def memory_info() -> dict:
    """物理内存快照：total_mb / avail_mb / load_percent（供自动内存分配等使用）。"""
    return _memory_info()


_VIRTUAL_GPU = (
    "virtual", "basic render", "basic display", "remote desktop",
    "mumu", "parsec", "spacedesk", "usb display",
)


def _prefer_physical_gpus(gpus: list) -> list:
    def rank(gpu):
        name = (gpu.get("name") or "").lower()
        return 1 if any(key in name for key in _VIRTUAL_GPU) else 0
    return sorted(gpus, key=rank)


def _probe_gpus() -> list:
    gpus = []
    if utils.IS_WINDOWS:
        data = _ps_json(
            "Get-CimInstance Win32_VideoController | "
            "Where-Object { $_.Name } | "
            "Select-Object Name,DriverVersion,AdapterRAM,PNPDeviceID | "
            "ConvertTo-Json -Compress",
            timeout=8,
        )
        rows = data if isinstance(data, list) else ([data] if isinstance(data, dict) else [])
        for row in rows:
            if not isinstance(row, dict):
                continue
            name = str(row.get("Name") or "").strip()
            if not name:
                continue
            vram = int(row.get("AdapterRAM") or 0)
            gpus.append({
                "name": name,
                "driver": str(row.get("DriverVersion") or ""),
                "vram_mb": int(vram // (1024 * 1024)) if vram > 0 else 0,
                "pnp": str(row.get("PNPDeviceID") or ""),
            })
        return _prefer_physical_gpus(gpus)
    if utils.IS_MAC:
        text = _run(["system_profiler", "SPDisplaysDataType"], timeout=10)
        current = {}
        for line in text.splitlines():
            s = line.strip()
            if s.startswith("Chipset Model:"):
                if current.get("name"):
                    gpus.append(current)
                current = {"name": s.split(":", 1)[-1].strip(), "driver": "", "vram_mb": 0}
            elif s.startswith("VRAM") and current:
                num = "".join(ch for ch in s.split(":", 1)[-1] if ch.isdigit())
                current["vram_mb"] = int(num or 0)
            elif s.startswith("Vendor:") and current:
                current["driver"] = s.split(":", 1)[-1].strip()
        if current.get("name"):
            gpus.append(current)
        return _prefer_physical_gpus(gpus)
    text = _run(["lspci", "-mm"], timeout=5) or _run(["lspci"], timeout=5)
    for line in text.splitlines():
        low = line.lower()
        if "vga" in low or "3d controller" in low or "display" in low:
            name = line.split(":", 2)[-1].strip() if ":" in line else line.strip()
            gpus.append({"name": name, "driver": "", "vram_mb": 0})
    return _prefer_physical_gpus(gpus)


def _disk_info() -> list:
    disks = []
    seen = set()
    candidates = [Path(utils.ROOT)]
    if utils.IS_WINDOWS:
        anchor = Path(utils.ROOT).anchor
        if anchor:
            candidates.append(Path(anchor))
        candidates.append(Path("C:\\"))
    else:
        candidates.append(Path("/"))
        home = Path.home()
        if str(home):
            candidates.append(home)
    for path in candidates:
        try:
            usage = shutil.disk_usage(path)
        except OSError:
            continue
        key = str(path.anchor or path)
        if key in seen:
            continue
        seen.add(key)
        disks.append({
            "path": key,
            "total_gb": round(usage.total / (1024 ** 3), 1),
            "free_gb": round(usage.free / (1024 ** 3), 1),
        })
    return disks


def _display_info() -> dict:
    width = height = 0
    screens = 1
    if utils.IS_WINDOWS:
        try:
            import ctypes
            user32 = ctypes.windll.user32
            width = int(user32.GetSystemMetrics(0) or 0)
            height = int(user32.GetSystemMetrics(1) or 0)
            screens = int(user32.GetSystemMetrics(80) or 1)
        except Exception:
            pass
    if not width:
        try:
            import tkinter
            root = tkinter.Tk()
            root.withdraw()
            width = int(root.winfo_screenwidth() or 0)
            height = int(root.winfo_screenheight() or 0)
            root.destroy()
        except Exception:
            pass
    return {"width": width, "height": height, "screens": screens}


def _java_info(scan_system: bool) -> list:
    rows = []
    try:
        from . import java as java_mod
        javas = java_mod.all_javas() if scan_system else java_mod.list_installed_javas()
        for item in javas[:16]:
            rows.append({
                "name": item.get("name") or "",
                "major": item.get("major"),
                "path": item.get("exe") or item.get("path") or "",
            })
    except Exception:
        pass
    return rows


def _instance_info() -> list:
    rows = []
    try:
        from .instances import Instance, list_instances
        from . import mods as mods_mod
        for name in list_instances()[:24]:
            inst = Instance(name)
            versions = inst.installed_ids()[:16]
            mod_count = 0
            try:
                mod_count = len(mods_mod.list_instance_mods(inst))
            except Exception:
                pass
            rows.append({
                "name": name,
                "versions": versions,
                "mod_count": mod_count,
                "java": inst.java_pref(),
            })
    except Exception:
        pass
    return rows


def summarize(info: dict) -> str:
    osd = info.get("os") or {}
    cpu = ((info.get("cpu") or {}).get("name") or "").strip()
    mem = info.get("memory") or {}
    total_mb = int(mem.get("total_mb") or 0)
    ram = f"{round(total_mb / 1024)}GB" if total_mb else ""
    gpus = info.get("gpus") or []
    gpu = (gpus[0].get("name") if gpus else "") or ""
    parts = [
        osd.get("display") or osd.get("platform") or "",
        cpu,
        ram,
        gpu,
    ]
    return " · ".join(p for p in parts if p)


def collect(force: bool = False, scan_system_java: bool = False, max_age: float = 120) -> dict:
    """返回本机配置快照。失败字段留空，不抛异常。

    max_age：缓存有效期。心跳这类高频低价值调用传大值（如 600），
    避免反复起 PowerShell 探硬件、扫实例目录。
    """
    now = time.time()
    with _LOCK:
        cached = _CACHE.get("data")
        if (not force) and cached and now - float(_CACHE.get("t") or 0) < max_age:
            return dict(cached)
    info = {
        "collected_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "hostname": socket.gethostname() or "",
        "os": _os_info(),
        "cpu": _cpu_info(),
        "memory": _memory_info(),
        "gpus": _gpu_info(),
        "disks": _disk_info(),
        "display": _display_info(),
        "java": _java_info(scan_system_java),
        "launcher": {
            "name": "PyMCL",
            "version": APP_VERSION,
            "frozen": bool(getattr(sys, "frozen", False)),
            "python": platform.python_version(),
            "root": str(utils.ROOT),
            "memory_mb": int(CONFIG.get("memory_mb") or 0),
            "download_threads": int(CONFIG.get("download_threads") or 0),
            "download_source": CONFIG.get("download_source") or "auto",
            "community_source": CONFIG.get("community_source") or "auto",
        },
        "instances": _instance_info(),
    }
    info["summary"] = summarize(info)
    with _LOCK:
        _CACHE["t"] = time.time()
        _CACHE["data"] = info
    return dict(info)


def get_smart_recommendation() -> dict:
    """根据硬件配置提供推荐值。"""
    rec = {
        "memory_mb": 4096,
        "java_major": 17,
        "window_width": 854,
        "window_height": 480,
        "gc_preset": "auto",
        "cpu_count": 4,
        "total_ram_gb": 8.0,
    }
    try:
        info = collect()
        mem = info.get("memory") or {}
        total_bytes = mem.get("total_bytes") or 0
        if total_bytes:
            total_gb = total_bytes / (1024 ** 3)
            if total_gb >= 32:
                rec["memory_mb"] = 12288
            elif total_gb >= 16:
                rec["memory_mb"] = 8192
            elif total_gb >= 8:
                rec["memory_mb"] = 4096
            else:
                rec["memory_mb"] = 2048
            safe = int(total_gb * 0.75 * 1024)
            if rec["memory_mb"] > safe:
                rec["memory_mb"] = max(1024, safe)
            rec["total_ram_gb"] = round(total_gb, 1)
        cpu = info.get("cpu") or {}
        rec["cpu_count"] = cpu.get("cores_logical") or cpu.get("cores_physical") or 4
    except Exception:
        pass
    return rec


def format_text(info: dict | None = None) -> str:
    data = info or collect()
    lines = [data.get("summary") or ""]
    cpu = data.get("cpu") or {}
    mem = data.get("memory") or {}
    disp = data.get("display") or {}
    lines.append(
        f"CPU {cpu.get('name') or '?'}  "
        f"{cpu.get('cores_physical') or '?'}C/{cpu.get('cores_logical') or '?'}T"
    )
    lines.append(
        f"内存 {mem.get('total_mb') or 0} MB  "
        f"可用 {mem.get('avail_mb') or 0} MB  "
        f"占用 {mem.get('load_percent') or 0}%"
    )
    for gpu in data.get("gpus") or []:
        vram = f"  {gpu.get('vram_mb')} MB" if gpu.get("vram_mb") else ""
        drv = f"  驱动 {gpu.get('driver')}" if gpu.get("driver") else ""
        lines.append(f"显卡 {gpu.get('name')}{vram}{drv}")
    if not (data.get("gpus") or []):
        lines.append("显卡 未检测到")
    if disp.get("width"):
        lines.append(f"分辨率 {disp.get('width')}×{disp.get('height')}  屏幕 {disp.get('screens') or 1}")
    for disk in data.get("disks") or []:
        lines.append(f"磁盘 {disk.get('path')}  {disk.get('free_gb')} / {disk.get('total_gb')} GB 可用")
    javas = data.get("java") or []
    if javas:
        lines.append("Java " + ", ".join(
            f"{j.get('major') or '?'}({Path(j.get('path') or '').name})" for j in javas[:6]
        ))
    launch = data.get("launcher") or {}
    lines.append(
        f"启动器 {launch.get('version')}  "
        f"Python {launch.get('python')}  "
        f"内存默认 {launch.get('memory_mb')} MB"
    )
    for inst in data.get("instances") or []:
        vers = ", ".join(inst.get("versions") or []) or "无版本"
        lines.append(f"实例 {inst.get('name')}  {vers}  mods={inst.get('mod_count') or 0}")
    return "\n".join(line for line in lines if line)
