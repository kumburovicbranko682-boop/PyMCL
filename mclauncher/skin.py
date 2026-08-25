# -*- coding: utf-8 -*-
"""皮肤头像 / 全身预览 URL。"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import quote, urlparse

from . import utils

STEVE = "https://mc-heads.net/avatar/Steve/128"
BODY = "https://mc-heads.net/body/{}/180"


def _site_origin(api: str) -> str:
    raw = str(api or "").rstrip("/")
    for suffix in ("/api/yggdrasil", "/yggdrasil"):
        if raw.endswith(suffix):
            return raw[: -len(suffix)]
    parsed = urlparse(raw if "://" in raw else "https://" + raw)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return raw


def avatar_url(account: dict | None) -> str:
    acc = account or {}
    uuid = utils.dashed_uuid(acc.get("uuid") or "").replace("-", "")
    name = acc.get("name") or "Steve"
    kind = acc.get("type") or "offline"
    if kind == "authlib" and acc.get("api"):
        origin = _site_origin(acc["api"])
        if name:
            return f"{origin}/avatar/{quote(name)}"
        if uuid:
            return f"{origin}/avatar/{uuid}"
    if uuid and kind == "microsoft":
        return f"https://crafatar.com/avatars/{uuid}?overlay=true&size=128"
    return f"https://mc-heads.net/avatar/{quote(name)}/128"


def body_url(account: dict | None) -> str:
    acc = account or {}
    uuid = utils.dashed_uuid(acc.get("uuid") or "").replace("-", "")
    name = acc.get("name") or "Steve"
    if acc.get("type") == "authlib" and acc.get("api") and name:
        origin = _site_origin(acc["api"])
        return f"{origin}/preview/{quote(name)}"
    if acc.get("type") == "microsoft" and uuid:
        return f"https://crafatar.com/renders/body/{uuid}?overlay=true&scale=6"
    return BODY.format(quote(name))


def steve_url() -> str:
    return STEVE


def local_skin(account: dict | None) -> dict:
    """离线账号的本地自定义皮肤 {local_file, model}；没有则 {}。

    在线渲染服务不知道本地皮肤，预览必须从这份文件本地渲染。
    """
    acc = account or {}
    if acc.get("type") != "offline" or not acc.get("skin_file"):
        return {}
    p = Path(acc["skin_file"])
    if not p.is_file():
        return {}
    model = "slim" if acc.get("skin_model") == "slim" else "classic"
    return {"local_file": str(p), "model": model}
