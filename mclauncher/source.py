# -*- coding: utf-8 -*-
"""PCL 同款下载源：官方 / BMCLAPI / 自动测速；社区资源官方优先、MCIM 兜底。"""

from __future__ import annotations

import threading
import time

BMCLAPI = "https://bmclapi2.bangbang93.com"
MCIM = "https://mod.mcimirror.top"
MODRINTH_API = "https://api.modrinth.com"
MODRINTH_CDN = "https://cdn.modrinth.com"
CF_OFFICIAL = "https://api.curseforge.com/v1"
PROBE_LIMIT = 4.0
PROBE_TTL = 600
_OFFICIAL_PROBE = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"

_FILE_MIRRORS = (
    ("https://piston-meta.mojang.com/", f"{BMCLAPI}/"),
    ("https://launchermeta.mojang.com/", f"{BMCLAPI}/"),
    ("https://piston-data.mojang.com/", f"{BMCLAPI}/"),
    ("https://resources.download.minecraft.net/", f"{BMCLAPI}/assets/"),
    ("https://libraries.minecraft.net/", f"{BMCLAPI}/maven/"),
    ("https://maven.minecraftforge.net/", f"{BMCLAPI}/maven/"),
    ("https://files.minecraftforge.net/maven/", f"{BMCLAPI}/maven/"),
    ("https://maven.neoforged.net/releases/", f"{BMCLAPI}/maven/"),
    ("https://maven.fabricmc.net/", f"{BMCLAPI}/maven/"),
    ("https://maven.quiltmc.org/repository/release/", f"{BMCLAPI}/maven/"),
    ("https://maven.quiltmc.org/", f"{BMCLAPI}/maven/"),
    ("https://meta.fabricmc.net/", f"{BMCLAPI}/fabric-meta/"),
    ("https://meta.quiltmc.org/", f"{BMCLAPI}/quilt-meta/"),
)

_lock = threading.Lock()
_probe_run = threading.Lock()
_probe = None  # (ok_fast: bool, fetched_at: float)


def _cfg(key, default=None):
    from .config import CONFIG
    return CONFIG.get(key, default)


def download_mode() -> str:
    mode = str(_cfg("download_source", "auto") or "auto").strip().lower()
    return mode if mode in ("auto", "official", "bmclapi") else "auto"


def community_mode() -> str:
    mode = str(_cfg("community_source", "auto") or "auto").strip().lower()
    return mode if mode in ("auto", "official", "mcim") else "auto"


def invalidate_probe():
    global _probe
    with _lock:
        _probe = None


def official_is_fast() -> bool:
    """官方握手 <4s 为快。与 PCL「优先官方，缓慢改镜像」相同。"""
    global _probe
    now = time.monotonic()
    with _lock:
        if _probe and now - _probe[1] < PROBE_TTL:
            return _probe[0]
    with _probe_run:
        with _lock:
            if _probe and time.monotonic() - _probe[1] < PROBE_TTL:
                return _probe[0]
        fast = _probe_official()
        with _lock:
            _probe = (fast, time.monotonic())
        return fast


def _probe_official() -> bool:
    import requests
    from . import APP_NAME, APP_VERSION
    t0 = time.monotonic()
    try:
        session = requests.Session()
        if not _cfg("use_system_proxy", True):
            session.trust_env = False
            session.proxies = {"http": None, "https": None}
        resp = session.get(
            _OFFICIAL_PROBE, timeout=PROBE_LIMIT,
            headers={"User-Agent": f"{APP_NAME}/{APP_VERSION} (python; +minecraft launcher)"},
        )
        elapsed = time.monotonic() - t0
        return resp.status_code < 400 and elapsed < PROBE_LIMIT
    except Exception:
        return False


def file_mirror_first() -> bool:
    mode = download_mode()
    if mode == "bmclapi":
        return True
    if mode == "official":
        return False
    return not official_is_fast()


def community_mirror_first() -> bool:
    """社区资源：自动=官方优先、MCIM 兜底（对齐 PCL 主线与 MCIM 官方建议）。

    community_source=mcim 时才镜像优先；official 仅官方。
    """
    return community_mode() == "mcim"


def warmup_async():
    threading.Thread(target=official_is_fast, name="pymcl-source-probe", daemon=True).start()


def _order(primary: list[str], secondary: list[str], secondary_first: bool, exclusive: str | None):
    # official = 只要官方。镜像优先/仅 BMCL/MCIM = 镜像在前，官方始终垫底，避免镜像 404 直接失败。
    if exclusive == "official":
        seq = primary
    elif exclusive in ("bmclapi", "mcim") or secondary_first:
        seq = secondary + primary
    else:
        seq = primary + secondary
    out, seen = [], set()
    for u in seq:
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out


def version_manifest_urls() -> list[str]:
    official = [
        "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json",
        "https://launchermeta.mojang.com/mc/game/version_manifest_v2.json",
    ]
    mirror = [f"{BMCLAPI}/mc/game/version_manifest_v2.json"]
    return _order(official, mirror, file_mirror_first(), download_mode())


def rewrite_to_bmcl(url: str) -> str | None:
    for official, mirror in _FILE_MIRRORS:
        if url.startswith(official):
            return mirror + url[len(official):]
    return None


def rewrite_to_mcim(url: str) -> str | None:
    if "api.modrinth.com" in url:
        return url.replace("https://api.modrinth.com", f"{MCIM}/modrinth")
    if MODRINTH_CDN in url:
        return url.replace(MODRINTH_CDN, MCIM)
    if url.startswith("https://api.curseforge.com"):
        return url.replace("https://api.curseforge.com", f"{MCIM}/curseforge")
    for host in ("https://edge.forgecdn.net", "https://mediafilez.forgecdn.net"):
        if url.startswith(host):
            return MCIM + url[len(host):]
    return None


def modrinth_api_bases() -> list[str]:
    official = [f"{MODRINTH_API}/v2"]
    mirror = [f"{MCIM}/modrinth/v2"]
    return _order(official, mirror, community_mirror_first(), community_mode())


def cf_api_bases() -> list[str]:
    official = [CF_OFFICIAL]
    mirror = [f"{MCIM}/curseforge/v1"]
    return _order(official, mirror, community_mirror_first(), community_mode())


def modrinth_file_urls(urls) -> list[str]:
    if isinstance(urls, str):
        urls = [urls]
    official, mirror = [], []
    for u in urls or []:
        if not u:
            continue
        official.append(u)
        m = rewrite_to_mcim(u)
        if m:
            mirror.append(m)
    return _order(official, mirror, community_mirror_first(), community_mode())


def is_github_url(url: str) -> bool:
    low = (url or "").lower()
    return "github.com" in low or "githubusercontent.com" in low


def expand_download_urls(url):
    """按当前下载源策略展开候选。自动模式：官方慢则镜像在前。"""
    if not url:
        return []
    if isinstance(url, (list, tuple)):
        out, seen = [], set()
        for u in url:
            for e in expand_download_urls(u):
                if e not in seen:
                    seen.add(e)
                    out.append(e)
        return out
    url = str(url)

    if (
        "api.modrinth.com" in url
        or MODRINTH_CDN in url
        or "forgecdn.net" in url
        or url.startswith(f"{MCIM}/files/")
    ):
        return modrinth_file_urls(url)

    if is_github_url(url):
        from .mirrors import github_candidates
        if download_mode() == "official":
            return [url]
        return github_candidates(url)

    mirrored = rewrite_to_bmcl(url)
    if mirrored:
        return _order([url], [mirrored], file_mirror_first(), download_mode())
    return [url]


def describe() -> str:
    file_mode = download_mode()
    comm = community_mode()
    if file_mode == "auto":
        file_txt = "自动·镜像优先" if file_mirror_first() else "自动·官方优先"
    elif file_mode == "bmclapi":
        file_txt = "BMCLAPI优先"
    else:
        file_txt = "仅官方"
    comm_txt = {
        "auto": "官方优先·MCIM兜底",
        "mcim": "MCIM优先",
        "official": "仅官方",
    }.get(comm, comm)
    return f"文件:{file_txt} / 社区:{comm_txt}"
