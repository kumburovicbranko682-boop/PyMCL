# -*- coding: utf-8 -*-
"""下载镜像：GitHub 前缀 + 转交给 source 策略。"""

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


def _prefixes():
    try:
        from .config import CONFIG
        extra = [str(p) for p in (CONFIG.get("github_proxy_prefixes") or []) if p]
        if extra:
            alive = [p for p in extra if p not in DEAD_PROXY_PREFIXES]
            if len(alive) != len(extra):
                # 旧默认表里混着死代理：剔除死项并并入新默认，保留用户自加的其它前缀
                merged = alive + [p for p in GITHUB_PROXY_PREFIXES if p not in alive]
                return tuple(merged)
            return tuple(alive)
    except Exception:
        pass
    return GITHUB_PROXY_PREFIXES


def github_candidates(url: str) -> list[str]:
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
