# -*- coding: utf-8 -*-
"""下载镜像：远程刷新 GitHub 前缀，并转交给 source 策略。"""

from __future__ import annotations

import threading
import time
from urllib.parse import urlsplit

GITHUB_PROXY_PREFIXES = (
    "https://ghfast.top/",
    "https://gh.llkk.cc/",
    "https://ghproxy.vip/",
    "https://gh-proxy.com/",
    "https://v6.gh-proxy.org/",
    "https://cdn.gh-proxy.com/",
)
# 已失效的历史代理（gitproxy.mrhjx.cn 返回 403）。用户旧 config 里出现时会被过滤。
DEAD_PROXY_PREFIXES = (
    "https://gitproxy.mrhjx.cn/",
)
REMOTE_SOURCE_URLS = (
    "https://raw.githubusercontent.com/LQS660/pymcl-download-sources/main/sources.txt",
    "https://cdn.jsdelivr.net/gh/LQS660/pymcl-download-sources@main/sources.txt",
)
REMOTE_SOURCE_TTL = 12 * 60 * 60
REMOTE_RETRY_TTL = 10 * 60
REMOTE_FETCH_TIMEOUT = 5.0

_remote_lock = threading.Lock()
_remote_fetch_lock = threading.Lock()
_remote_warmup_lock = threading.Lock()
_remote_prefixes: tuple[str, ...] | None = None
_remote_fetched_at = 0.0
_remote_fetch_succeeded = False
_remote_refresh_running = False


def parse_source_urls(text: str) -> tuple[str, ...]:
    """解析每行一个 HTTPS URL 的公开源列表，忽略空行和注释。"""
    out, seen = [], set()
    for raw in str(text or "").splitlines():
        url = raw.strip()
        if not url or url.startswith("#"):
            continue
        try:
            parsed = urlsplit(url)
        except ValueError:
            continue
        if (
            parsed.scheme.lower() != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            continue
        if url not in seen:
            seen.add(url)
            out.append(url)
    return tuple(out)


def _normalize_prefix(value) -> str:
    return str(value or "").strip().rstrip("/") + "/"


def _is_github_proxy(url: str) -> bool:
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return False
    return (
        host.startswith(("gh.", "ghproxy.", "gh-proxy.", "ghfast."))
        or ".gh-proxy." in host
        or "githubproxy" in host
    )


def _merge_prefixes(*groups) -> tuple[str, ...]:
    dead = {_normalize_prefix(prefix).lower() for prefix in DEAD_PROXY_PREFIXES}
    out, seen = [], set()
    for group in groups:
        for value in group or ():
            prefix = _normalize_prefix(value)
            key = prefix.lower()
            if prefix == "/" or key in dead or key in seen:
                continue
            seen.add(key)
            out.append(prefix)
    return tuple(out)


def _github_proxy_prefixes(urls) -> tuple[str, ...]:
    return _merge_prefixes(url for url in urls if _is_github_proxy(url))


def _fetch_source_text(url: str) -> str:
    import requests

    from . import APP_NAME, APP_VERSION
    from .net import apply_proxy_to_session

    session = requests.Session()
    apply_proxy_to_session(session)
    response = session.get(
        url,
        timeout=REMOTE_FETCH_TIMEOUT,
        headers={"User-Agent": f"{APP_NAME}/{APP_VERSION} (download source refresh)"},
    )
    response.raise_for_status()
    text = response.text or ""
    if len(text) > 64 * 1024:
        raise ValueError("remote source list is unexpectedly large")
    return text


def _remote_cache_is_fresh(now: float | None = None) -> bool:
    now = time.monotonic() if now is None else now
    with _remote_lock:
        ttl = REMOTE_SOURCE_TTL if _remote_fetch_succeeded else REMOTE_RETRY_TTL
        return bool(_remote_fetched_at and now - _remote_fetched_at < ttl)


def refresh_remote_sources(force: bool = False) -> tuple[str, ...]:
    """刷新远程源；网络失败时保留上次结果或内置默认值。"""
    global _remote_fetched_at, _remote_fetch_succeeded, _remote_prefixes
    if not force and _remote_cache_is_fresh():
        with _remote_lock:
            return _remote_prefixes or GITHUB_PROXY_PREFIXES

    with _remote_fetch_lock:
        if not force and _remote_cache_is_fresh():
            with _remote_lock:
                return _remote_prefixes or GITHUB_PROXY_PREFIXES

        fetched = None
        for url in REMOTE_SOURCE_URLS:
            try:
                prefixes = _github_proxy_prefixes(parse_source_urls(_fetch_source_text(url)))
            except Exception:
                continue
            if prefixes:
                fetched = prefixes
                break

        with _remote_lock:
            if fetched:
                _remote_prefixes = fetched
                _remote_fetch_succeeded = True
            else:
                _remote_fetch_succeeded = False
            _remote_fetched_at = time.monotonic()
            return _remote_prefixes or GITHUB_PROXY_PREFIXES


def warmup_async():
    """后台刷新公开链接列表，不阻塞启动或下载候选生成。"""
    global _remote_refresh_running
    if _remote_cache_is_fresh():
        return
    with _remote_warmup_lock:
        if _remote_refresh_running or _remote_cache_is_fresh():
            return
        _remote_refresh_running = True

    def worker():
        global _remote_refresh_running
        try:
            refresh_remote_sources()
        finally:
            with _remote_warmup_lock:
                _remote_refresh_running = False

    threading.Thread(target=worker, name="pymcl-mirror-refresh", daemon=True).start()


def _active_prefixes() -> tuple[str, ...]:
    with _remote_lock:
        return _remote_prefixes or GITHUB_PROXY_PREFIXES


def _prefixes():
    try:
        from .config import CONFIG
        extra = [str(p) for p in (CONFIG.get("github_proxy_prefixes") or []) if p]
        if extra:
            alive = _merge_prefixes(extra)
            if len(alive) != len(extra):
                # 旧默认表里混着死代理：剔除死项并并入新默认，保留用户自加的其它前缀
                return _merge_prefixes(alive, _active_prefixes())
            return tuple(alive)
    except Exception:
        pass
    return _active_prefixes()


def github_candidates(url: str) -> list[str]:
    warmup_async()
    out, seen = [], set()
    for prefix in _prefixes():
        u = prefix + url
        if u not in seen:
            seen.add(u)
            out.append(u)
    if url not in seen:
        out.append(url)
    return out


def expand_download_urls(url):
    from . import source
    return source.expand_download_urls(url)
