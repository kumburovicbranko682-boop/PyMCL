# -*- coding: utf-8 -*-
"""Java 运行时：检测系统 Java、下载 Mojang 官方运行时 / Adoptium OpenJDK。"""
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

from . import utils
from .config import CONFIG
from .downloader import DownloadManager, DownloadError

# Mojang 官方 Java 运行时清单（与官方启动器一致，两个域名都试）
RUNTIME_MANIFEST_URLS = [
    "https://piston-meta.mojang.com/v1/products/java-runtime/2ec0cc96c44e5a76b9c8b7c39df7210883d12871/all.json",
    "https://launchermeta.mojang.com/v1/products/java-runtime/2ec0cc96c44e5a76b9c8b7c39df7210883d12871/all.json",
]
RUNTIME_MANIFEST_TTL = 24 * 3600

# Adoptium 最新二进制 API
ADOPTIUM_BINARY_URL = "https://api.adoptium.net/v3/binary/latest/{major}/ga/{os}/{arch}/jre/hotspot/normal/eclipse"


def _platform_key() -> str:
    """Mojang 运行时清单的平台键。"""
    osn = utils.OS_NAME
    arch = utils.ARCH
    if osn == "windows":
        return f"windows-{arch}" if arch in ("x64", "arm64") else "windows-x86"
    if osn == "linux":
        return {"x64": "linux", "x86": "linux-i386", "arm64": "linux-arm64"}.get(arch, "linux")
    if osn == "osx":
        return "osx-arm64" if arch == "arm64" else "osx"
    return "linux"


PLATFORM_KEY = _platform_key()


def _adoptium_os() -> str:
    return {"windows": "windows", "osx": "mac", "linux": "linux"}[utils.OS_NAME]


def _adoptium_arch(arch=None) -> str:
    return {"x64": "x64", "x86": "x86", "arm64": "aarch64"}[arch or utils.ARCH]


def adoptium_major(major: int) -> int:
    """把任意 major 映射到 Adoptium 有发布的 LTS 版本。

    MC 26.1+ 需要 Java 25，>21 不能再收口到 21。
    """
    if major <= 8:
        return 8
    if major <= 11:
        return 11
    if major <= 17:
        return 17
    if major <= 21:
        return 21
    return 25


# ---------------------------------------------------------------- 探测

_MAJOR_CACHE = {}
_SYS_JAVA_CACHE = {"t": 0.0, "data": None}
_SYS_JAVA_LOCK = threading.Lock()


def java_version_output(java_exe) -> str:
    """跑一次 java -version，返回合并后的文本（失败为空串）。"""
    if not java_exe:
        return ""
    try:
        proc = subprocess.run(
            [str(java_exe), "-version"],
            capture_output=True, timeout=6,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if utils.IS_WINDOWS else 0,
        )
        raw = (proc.stderr or b"") + (proc.stdout or b"")
        out = raw.decode("utf-8", errors="replace")
        if "version" not in out.lower():
            out = raw.decode("gbk", errors="replace")
        return out
    except Exception:
        return ""


def get_java_major(java_exe) -> int:
    key = str(java_exe)
    if key in _MAJOR_CACHE:
        return _MAJOR_CACHE[key]
    out = java_version_output(java_exe)
    m = re.search(r'version "([^"]+)"', out)
    if not m:
        return None
    v = m.group(1)
    try:
        result = int(v.split(".")[1]) if v.startswith("1.") else int(v.split(".")[0])
    except (TypeError, ValueError):
        return None
    _MAJOR_CACHE[key] = result
    return result


def java_usable_for(version_json, java_exe) -> bool:
    if not java_exe:
        return False
    try:
        if not Path(java_exe).is_file():
            return False
    except OSError:
        return False
    need = required_java_major(version_json)
    got = get_java_major(java_exe)
    return got is not None and got >= need


def resolve_launch_java(version_json, prefer=None, dm=None, on_progress=None, on_note=None):
    """选出真正能启动该版本的 Java；太旧的用户选择会被丢掉。"""
    need = required_java_major(version_json)
    exe = pick_java_for_version(version_json, prefer=prefer)
    if exe and not java_usable_for(version_json, exe):
        if on_note:
            on_note(f"候选 Java {get_java_major(exe) or '?'} 无法启动此版本（需要 {need}+）")
        exe = pick_java_for_version(version_json, prefer=None)
    if (not exe or not java_usable_for(version_json, exe)) and dm is not None:
        if on_note:
            on_note(f"未找到 Java {need}，自动下载中…")
        exe = ensure_java_for_version(version_json, dm, on_progress=on_progress)
    if exe and java_usable_for(version_json, exe):
        return str(Path(exe))
    # 最后兜底：任何已安装的足够新的 Java
    for j in list_installed_javas() + find_system_javas():
        cand = j.get("exe")
        if java_usable_for(version_json, cand):
            return str(Path(cand))
    return exe


def _walk_javas(root: Path, results, seen, depth=0):
    """有限深度扫描目录树中的 java 可执行文件。"""
    if depth > 6 or not root or not root.is_dir():
        return
    try:
        entries = sorted(os.scandir(root), key=lambda e: e.name.lower())
    except OSError:
        return
    for entry in entries:
        name = entry.name
        if name.startswith("."):
            continue
        try:
            is_dir = entry.is_dir(follow_symlinks=False)
        except OSError:
            continue
        if is_dir:
            if name in ("node_modules", "site-packages", "WinSxS", "Windows", "AppData"):
                continue
            bin_dir = Path(entry.path) / "bin"
            matched = False
            for exe_name in ("java.exe", "java", "javaw.exe"):
                exe = bin_dir / exe_name
                if exe.is_file():
                    key = str(exe.resolve())
                    if key not in seen:
                        seen.add(key)
                        results.append(exe)
                    matched = True
                    break
            if name.lower() in ("bin", "lib"):
                continue
            java_hint = any(t in name.lower() for t in ("java", "jdk", "jre", "zulu", "jbr", "temurin", "adoptium"))
            if not matched or java_hint or name == "Contents":
                _walk_javas(Path(entry.path), results, seen, depth + 1)
    return


def find_system_javas():
    """查找系统中已安装的 Java（结果缓存 60 秒）。返回 [{'name', 'exe', 'major'}]"""
    now = time.time()
    if _SYS_JAVA_CACHE["data"] is not None and now - _SYS_JAVA_CACHE["t"] < 60:
        return _SYS_JAVA_CACHE["data"]
    with _SYS_JAVA_LOCK:
        now = time.time()
        if _SYS_JAVA_CACHE["data"] is not None and now - _SYS_JAVA_CACHE["t"] < 60:
            return _SYS_JAVA_CACHE["data"]
        found = []
        seen = set()

        candidates = []
        java_home = os.environ.get("JAVA_HOME")
        if java_home:
            candidates.append(Path(java_home))
        for name in ("java", "javaw"):
            p = shutil.which(name)
            if p:
                candidates.append(Path(p).resolve().parent.parent)

        if utils.IS_WINDOWS:
            roots = [
                Path(os.environ.get("ProgramFiles", r"C:\Program Files")),
                Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")),
                Path(os.environ.get("LOCALAPPDATA", "") or "C:/Users/Public"),
            ]
            for r in roots:
                if not r.is_dir():
                    continue
                for d in r.glob("*"):
                    low = d.name.lower()
                    if "java" in low or "jdk" in low or "jre" in low or "zulu" in low or "jbr" in low:
                        candidates.append(d)
        elif utils.IS_MAC:
            candidates.append(Path("/Library/Java/JavaVirtualMachines"))
            candidates.append(Path("/opt/homebrew/opt"))
            candidates.append(Path("/usr/local/opt"))
        else:
            candidates.append(Path("/usr/lib/jvm"))

        for c in candidates:
            _walk_javas(c, found, seen)

        result = []
        for exe in found[:50]:
            major = get_java_major(exe)
            result.append({"name": f"Java {major or '?'} ({exe})", "exe": str(exe), "major": major})
        result.sort(key=lambda x: (x["major"] is None, x["major"] or 0), reverse=True)
        _SYS_JAVA_CACHE["t"] = time.time()
        _SYS_JAVA_CACHE["data"] = result
    return result


def list_installed_javas():
    """列出启动器自带的 Java 运行时。返回 [{'name', 'exe', 'major', 'dir', 'meta'}]"""
    java_dir = CONFIG.java_dir
    result = []
    if not java_dir.is_dir():
        return result
    for child in sorted(java_dir.iterdir()):
        if not child.is_dir():
            continue
        meta = utils.read_json(child / "runtime.meta.json", None) or {}
        exe = utils.find_executable(child)
        if not exe:
            continue
        # meta 里记了 major 就直接用：get_java_major 要拉起一次
        # `java -version` 子进程（最多 6 秒超时），实例列表这类
        # UI 路径经不起每个运行时都跑一遍。
        major = meta.get("major")
        if not isinstance(major, int):
            major = get_java_major(exe)
        result.append({
            "name": meta.get("name", child.name),
            "exe": str(exe),
            "major": major,
            "dir": str(child),
            "meta": meta,
        })
    return result


def cached_system_javas():
    """只读 60 秒缓存；缓存冷时返回空列表，绝不现场扫盘。

    UI 线程（实例列表 Java 标签、启动页下拉框）必须走这个：
    `find_system_javas()` 冷启动会 glob Program Files 并给每个候选
    跑 `java -version`，秒级起步，放在 UI 线程就是一次假死。
    """
    data = _SYS_JAVA_CACHE.get("data")
    return list(data) if data else []


def cached_all_javas():
    """自带 Java + 已缓存的系统 Java（不触发扫描）。"""
    result = list_installed_javas() + cached_system_javas()
    seen = set()
    unique = []
    for j in result:
        key = j["exe"]
        if key not in seen:
            seen.add(key)
            unique.append(j)
    return unique


def warm_system_javas_async():
    """起后台线程把系统 Java 扫描灌进缓存，供 cached_* 读取。"""
    def _run():
        try:
            find_system_javas()
        except Exception:
            pass
    return threading.Thread(target=_run, name="pymcl-java-warmup", daemon=True).start()


def all_javas():
    """系统 Java + 启动器自带 Java。"""
    result = list_installed_javas() + find_system_javas()
    seen = set()
    unique = []
    for j in result:
        key = j["exe"]
        if key not in seen:
            seen.add(key)
            unique.append(j)
    return unique


def required_java_major(version_json) -> int:
    """这个版本真正需要的 Java 大版本。

    Forge 1.17+ 的 version.json 通常自己不写 javaVersion（写在 inheritsFrom 的原版里）。
    若按缺省当成 Java 8，启动时会把 ``-p``（模块路径）传给 Java 8，报
    Unrecognized option: -p。
    """
    declared = (version_json.get("javaVersion") or {}).get("majorVersion")
    if declared:
        try:
            return int(declared)
        except (TypeError, ValueError):
            pass
    main = str(version_json.get("mainClass") or "").lower()
    # 只认 BootstrapLauncher（1.17+）。1.13–1.16 的 cpw.mods.modlauncher 仍要 Java 8。
    if "bootstraplauncher" in main:
        return 17
    jvm = ((version_json.get("arguments") or {}).get("jvm")) or []
    tokens = []
    for entry in jvm:
        if isinstance(entry, str):
            tokens.append(entry)
        elif isinstance(entry, dict):
            val = entry.get("value", [])
            tokens.extend(val if isinstance(val, list) else [val])
    if any(t in ("-p", "--module-path", "--add-modules") for t in tokens):
        return 17
    return 8


def _prefer_to_exe(prefer):
    if not prefer:
        return None
    p = Path(prefer)
    if p.is_file() or (p.parent / "bin").exists():
        if p.is_file():
            return str(p)
        exe = utils.find_executable(p)
        if exe:
            return str(exe)
    for j in all_javas():
        if j.get("exe") == prefer or j.get("dir") == prefer:
            return j["exe"]
    return None


def pick_java_for_version(version_json, prefer=None):
    """
    为某个 Minecraft 版本挑选 Java：
    1. prefer（用户指定，且版本够新）
    2. 启动器自带 Java 中恰好匹配 major
    3. 系统中匹配 major 的 Java
    返回 java exe 路径或 None。
    """
    required = required_java_major(version_json)
    if prefer:
        exe = _prefer_to_exe(prefer)
        if exe:
            got = get_java_major(exe)
            if got is not None and got >= required:
                return exe
    need_x86 = _is_legacy_32bit(version_json)

    def _match(javas):
        pool = [j for j in javas if j.get("major") == required]
        if need_x86:
            # 远古版本必须 32 位 Java（64 位 Java 加载不了 32 位 natives）
            x86 = [j for j in pool if "x86" in (j.get("dir") or "") or "x86" in (j.get("exe") or "")]
            if not x86:
                return None
            pool = x86
        if pool:
            return pool[0]["exe"]
        newer = [j for j in javas if j.get("major") and j["major"] > required]
        if newer and required >= 17:
            # 新版 MC 可用更高的 Java（21/25 等），旧版必须精确匹配
            return min(newer, key=lambda j: j["major"])["exe"]
        return None

    own = _match(list_installed_javas())
    if own:
        return own
    return _match(find_system_javas())


# ---------------------------------------------------------------- 下载

def get_runtime_manifest(dm: DownloadManager, force=False):
    cache_file = utils.ROOT / "cache" / "java_runtime_manifest.json"
    meta_file = utils.ROOT / "cache" / "java_runtime_manifest.meta"
    if not force:
        meta = utils.read_json(meta_file, None)
        if meta and cache_file.is_file() and time.time() - meta.get("fetched_at", 0) < RUNTIME_MANIFEST_TTL:
            data = utils.read_json(cache_file, None)
            if data:
                return data
    last_err = None
    for url in RUNTIME_MANIFEST_URLS:
        try:
            data = dm.fetch_json(url, timeout=60)
            utils.ensure_dir(cache_file.parent)
            utils.write_json(cache_file, data)
            utils.write_json(meta_file, {"fetched_at": time.time(), "url": url})
            return data
        except Exception as e:
            last_err = e
            utils.log.warning("获取 Java 运行时清单失败 %s: %s", url, e)
    cached = utils.read_json(cache_file, None)
    if cached:
        utils.log.warning("使用缓存中的旧 Java 运行时清单。")
        return cached
    raise DownloadError(f"无法获取 Java 运行时清单: {last_err}")


def _archive_valid(path, suffix) -> bool:
    """判断已下载的压缩包是否完整可用（防止把损坏/空壳的旧包当成已下载）。"""
    import tarfile
    import zipfile
    p = Path(path)
    if not p.is_file() or p.stat().st_size == 0:
        return False
    try:
        if suffix == ".zip":
            return zipfile.is_zipfile(p)
        return tarfile.is_tarfile(p)
    except OSError:
        return False


def _safe_unlink(path):
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        pass


def install_mojang_runtime(dm: DownloadManager, component, on_progress=None, force=False):
    """
    下载 Mojang 官方 Java 运行时（如 java-runtime-gamma）。
    返回 exe 路径；不适用时返回 None。
    """
    java_dir = CONFIG.java_dir
    manifest = get_runtime_manifest(dm)
    platform_runtimes = manifest.get(PLATFORM_KEY)
    if not platform_runtimes:
        utils.log.warning("Mojang 运行时清单中没有平台 %s", PLATFORM_KEY)
        return None
    entries = platform_runtimes.get(component)
    if not entries:
        return None
    entry = entries[-1]  # 取最新
    info = entry.get("manifest") or {}
    url, sha1 = info.get("url"), info.get("sha1")
    if not url:
        return None
    ver = (entry.get("version") or {}).get("name", "?")
    target_dir = java_dir / f"{component}-{PLATFORM_KEY}"
    exe = utils.find_executable(target_dir)
    if exe and not force:
        return exe

    utils.ensure_dir(java_dir)
    _safe_unlink(java_dir / f"{component}-{PLATFORM_KEY}.zip")
    if on_progress:
        on_progress(f"下载 Mojang Java 运行时 {component} ({ver})", 0, 1)
    man_path = java_dir / f"{component}-{PLATFORM_KEY}.manifest.json"
    dm.download(url, man_path, sha1=sha1, size=info.get("size"), force=force)
    files = (utils.read_json(man_path, None) or {}).get("files") or {}
    if not files:
        raise DownloadError(f"Mojang 运行时清单无效: {component}")
    if force:
        utils.remove_tree(target_dir)
    utils.ensure_dir(target_dir)
    tasks = []
    for rel, meta in files.items():
        kind = (meta or {}).get("type")
        dest = target_dir / rel
        if kind == "directory":
            dest.mkdir(parents=True, exist_ok=True)
            continue
        if kind != "file":
            continue
        raw = ((meta or {}).get("downloads") or {}).get("raw") or {}
        file_url, file_sha1, file_size = raw.get("url"), raw.get("sha1"), raw.get("size")
        if not file_url:
            utils.log.warning("Mojang 运行时缺少 raw 下载: %s", rel)
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not force and utils.file_matches(dest, file_sha1, file_size):
            continue
        tasks.append((file_url, dest, file_sha1, file_size))
    if tasks:
        dm.download_all(tasks, message=f"下载 Mojang Java {component}")
    if not utils.IS_WINDOWS:
        for rel, meta in files.items():
            if not (meta or {}).get("executable"):
                continue
            p = target_dir / rel
            if p.is_file():
                os.chmod(p, 0o755)
    exe = utils.find_executable(target_dir)
    if exe:
        utils.write_json(target_dir / "runtime.meta.json", {
            "kind": "mojang", "name": f"Mojang {component} ({ver})",
            "component": component, "version": ver, "major": get_java_major(exe),
        })
        return exe
    utils.log.warning("Mojang 运行时安装后未找到 java 可执行文件: %s", target_dir)
    return None


def install_adoptium(dm: DownloadManager, major: int, on_progress=None, force=False, arch=None):
    """下载 Adoptium Temurin JRE。返回 exe 路径。arch 可强制为 x86/x64/arm64。"""
    major = adoptium_major(major)
    arch = arch or utils.ARCH
    api_arch = _adoptium_arch(arch)  # arm64 -> aarch64（Adoptium API 要求）
    java_dir = CONFIG.java_dir
    target_dir = java_dir / f"adoptium-{major}-{arch}"
    exe = utils.find_executable(target_dir)
    if exe and not force:
        return exe

    url = ADOPTIUM_BINARY_URL.format(major=major, os=_adoptium_os(), arch=api_arch)
    utils.ensure_dir(java_dir)
    suffix = ".zip" if utils.IS_WINDOWS else ".tar.gz"
    archive = java_dir / f"adoptium-{major}-{arch}{suffix}"
    if on_progress:
        on_progress(f"下载 Adoptium Java {major} ({arch}) 运行时", 0, 1)
    # 旧的损坏/空壳压缩包不能复用，强制重下
    if not _archive_valid(archive, suffix):
        dm.download(url, archive, force=True)
    try:
        utils.remove_tree(target_dir)
        utils.ensure_dir(target_dir)
        dm.extract_archive(archive, target_dir)
    except Exception:
        utils.remove_tree(target_dir)
        _safe_unlink(archive)
        raise
    finally:
        _safe_unlink(archive)
    exe = utils.find_executable(target_dir)
    if not exe:
        utils.remove_tree(target_dir)
        raise DownloadError(f"Adoptium Java {major} 解压后未找到 java 可执行文件")
    utils.write_json(target_dir / "runtime.meta.json", {
        "kind": "adoptium", "name": f"Adoptium Java {major} ({arch})",
        "version": str(major), "major": get_java_major(exe), "arch": arch,
    })
    return exe


def _is_legacy_32bit(version_json) -> bool:
    """远古版本（<1.6）在 64 位 Windows 上需要 32 位 Java 8。"""
    if not (utils.IS_WINDOWS and utils.ARCH == "x64"):
        return False
    from .manifest import is_legacy_version
    return is_legacy_version(version_json)


def ensure_java_for_version(version_json, dm: DownloadManager, on_progress=None, force=False):
    """
    确保某个 Minecraft 版本所需的 Java 可用（自动下载）。
    优先使用 Mojang 官方运行时（精确匹配），否则使用 Adoptium。
    返回 exe 路径。
    """
    # 1. 先看本机/自带 Java 是否满足
    existing = pick_java_for_version(version_json)
    if existing and not force:
        return existing

    java_version = version_json.get("javaVersion") or {}
    component = java_version.get("component")
    major = java_version.get("majorVersion") or required_java_major(version_json)

    if component and component != "jre-legacy":
        try:
            exe = install_mojang_runtime(dm, component, on_progress=on_progress, force=force)
            if exe:
                return exe
        except Exception as e:
            utils.log.warning("Mojang 运行时下载失败，回退 Adoptium: %s", e)

    arch = "x86" if _is_legacy_32bit(version_json) else None
    exe = install_adoptium(dm, major, on_progress=on_progress, force=force, arch=arch)
    return exe


def java_for_installer(loader: str, dm: DownloadManager, on_progress=None):
    """为 Forge/NeoForge 安装器挑选 Java。"""
    if loader == "forge-legacy":
        # 1.7.10 ~ 1.16.5 的 Forge 安装器要求 Java 8（更高版本会直接报错）
        for j in list_installed_javas() + find_system_javas():
            if j.get("major") == 8:
                return j["exe"]
        return install_adoptium(dm, 8, on_progress=on_progress)
    major = 17
    for j in list_installed_javas():
        if j.get("major") and j["major"] >= major:
            return j["exe"]
    for j in find_system_javas():
        if j.get("major") and j["major"] >= major:
            return j["exe"]
    return install_adoptium(dm, major, on_progress=on_progress)


# ---------------------------------------------------------------- 多发行版下载

JAVA_VENDORS = {
    "adoptium": "Adoptium Temurin",
    "zulu": "Azul Zulu",
    "microsoft": "Microsoft OpenJDK",
}


def java_vendor_list() -> list[str]:
    return list(JAVA_VENDORS)


def java_vendor_label(vendor: str) -> str:
    return JAVA_VENDORS.get(str(vendor or "").lower(), str(vendor or "adoptium"))


def install_java_vendor(dm: DownloadManager, major: int, vendor: str = "adoptium",
                        on_progress=None, force=False, arch=None):
    """按发行版下载 Java。vendor: adoptium / zulu / microsoft。返回 exe 路径。"""
    vendor = (vendor or "adoptium").lower()
    if vendor == "adoptium":
        return install_adoptium(dm, major, on_progress=on_progress, force=force, arch=arch)
    if vendor == "zulu":
        return _install_zulu(dm, major, on_progress=on_progress, force=force, arch=arch)
    if vendor == "microsoft":
        return _install_microsoft(dm, major, on_progress=on_progress, force=force, arch=arch)
    raise DownloadError(f"未知的 Java 发行版: {vendor}")


def _zulu_os() -> str:
    return {"windows": "windows", "osx": "macos", "linux": "linux"}[utils.OS_NAME]


def _zulu_arch(arch=None) -> str:
    return {"x64": "x64", "x86": "x86", "arm64": "aarch64"}[arch or utils.ARCH]


def _install_zulu(dm: DownloadManager, major: int, on_progress=None, force=False, arch=None):
    """下载 Azul Zulu JRE。"""
    major = adoptium_major(major)
    arch = arch or utils.ARCH
    zos = _zulu_os()
    zarch = _zulu_arch(arch)
    java_dir = CONFIG.java_dir
    target_dir = java_dir / f"zulu-{major}-{arch}"
    exe = utils.find_executable(target_dir)
    if exe and not force:
        return exe

    # Azul API: os=windows, arch=x64, java_version=21, java_package_type=jre
    url = (
        "https://api.azul.com/metadata/v1/zulu/packages"
        f"?java_version={major}&os={zos}&arch={zarch}"
        "&java_package_type=jre&archive_type=zip&latest=true"
    )
    utils.ensure_dir(java_dir)
    if on_progress:
        on_progress(f"查询 Azul Zulu Java {major} ({arch})…", 0, 1)
    try:
        meta = dm.fetch_json(url, timeout=25)
    except Exception:
        meta = None
    if not isinstance(meta, list) or not meta:
        raise DownloadError(f"Azul Zulu 没有 Java {major} ({zos}/{zarch}) 的 JRE 包")
    entry = meta[0]
    download_url = entry.get("download_url")
    if not download_url:
        raise DownloadError("Azul 返回的数据缺少下载地址")
    suffix = ".zip" if utils.IS_WINDOWS else ".tar.gz"
    archive = java_dir / f"zulu-{major}-{arch}{suffix}"
    try:
        utils.remove_tree(target_dir)
        utils.ensure_dir(target_dir)
        if on_progress:
            on_progress(f"下载 Zulu Java {major} ({arch}) 运行时", 0, 1)
        dm.download(download_url, archive, force=True)
        dm.extract_archive(archive, target_dir)
    except Exception:
        utils.remove_tree(target_dir)
        _safe_unlink(archive)
        raise
    finally:
        _safe_unlink(archive)
    exe = utils.find_executable(target_dir)
    if not exe:
        utils.remove_tree(target_dir)
        raise DownloadError(f"Zulu Java {major} 解压后未找到 java 可执行文件")
    utils.write_json(target_dir / "runtime.meta.json", {
        "kind": "zulu", "name": f"Zulu Java {major} ({arch})",
        "version": str(major), "major": get_java_major(exe), "arch": arch,
    })
    return exe


def _install_microsoft(dm: DownloadManager, major: int, on_progress=None, force=False, arch=None):
    """下载 Microsoft OpenJDK。"""
    major = adoptium_major(major)
    arch = arch or utils.ARCH
    java_dir = CONFIG.java_dir
    target_dir = java_dir / f"microsoft-{major}-{arch}"
    exe = utils.find_executable(target_dir)
    if exe and not force:
        return exe

    # Microsoft Build of OpenJDK 下载链接
    ms_arch = {"x64": "x64", "x86": "x86", "arm64": "aarch64"}[arch]
    ms_os = {"windows": "windows", "osx": "mac", "linux": "linux"}[utils.OS_NAME]
    url = (
        "https://aka.ms/download-jdk/"
        f"microsoft-jdk-{major}-{ms_os}-{ms_arch}.zip"
    )
    utils.ensure_dir(java_dir)
    suffix = ".zip" if utils.IS_WINDOWS else ".tar.gz"
    archive = java_dir / f"microsoft-{major}-{arch}{suffix}"
    if on_progress:
        on_progress(f"下载 Microsoft Java {major} ({arch}) 运行时", 0, 1)
    try:
        utils.remove_tree(target_dir)
        utils.ensure_dir(target_dir)
        dm.download(url, archive, force=True)
        dm.extract_archive(archive, target_dir)
    except Exception:
        utils.remove_tree(target_dir)
        _safe_unlink(archive)
        raise
    finally:
        _safe_unlink(archive)
    exe = utils.find_executable(target_dir)
    if not exe:
        utils.remove_tree(target_dir)
        raise DownloadError(f"Microsoft Java {major} 解压后未找到 java 可执行文件")
    utils.write_json(target_dir / "runtime.meta.json", {
        "kind": "microsoft", "name": f"Microsoft Java {major} ({arch})",
        "version": str(major), "major": get_java_major(exe), "arch": arch,
    })
    return exe
