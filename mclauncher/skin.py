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


# ---------------------------------------------------------------- 皮肤原图获取
# 3D 预览需要皮肤纹理 PNG 本体（不是渲染服务出的成品图）。
MOJANG_SESSION_PROFILE = "https://sessionserver.mojang.com/session/minecraft/profile/{uuid}"


def _profile_skin(profile_url: str, timeout: float = 12) -> dict:
    """从 yggdrasil 会话档案解出皮肤纹理 {url, model}；默认皮肤返回 {}。"""
    import base64
    import json
    import requests
    resp = requests.get(profile_url, timeout=timeout)
    if resp.status_code != 200:
        return {}
    for prop in resp.json().get("properties") or []:
        if prop.get("name") != "textures":
            continue
        try:
            payload = json.loads(base64.b64decode(prop.get("value") or ""))
        except (ValueError, TypeError):
            continue
        entry = (payload.get("textures") or {}).get("SKIN") or {}
        url = entry.get("url")
        if url:
            model = ("slim" if (entry.get("metadata") or {}).get("model") == "slim"
                     else "classic")
            return {"url": str(url), "model": model}
    return {}


def _texture_cache_path(url: str) -> Path:
    import hashlib
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()
    return utils.ROOT / "cache" / "skin_textures" / f"{digest}.png"


def fetch_skin_texture(account: dict | None, timeout: float = 12) -> dict:
    """任意账号的皮肤纹理 PNG → 本地文件 {file, model}。

    离线账号直接用本地皮肤文件；微软 / 皮肤站 / 统一通行证账号先查
    会话档案拿纹理 URL，再按 URL 缓存下载（纹理 URL 内容寻址，命中
    缓存零网络）。默认皮肤或失败返回 {}，调用方退回原有预览方式。
    """
    acc = account or {}
    local = local_skin(acc)
    if local:
        return {"file": local["local_file"], "model": local["model"]}
    kind = acc.get("type")
    uuid = str(acc.get("uuid") or "").replace("-", "")
    if not uuid:
        return {}
    if kind == "microsoft":
        profile_url = MOJANG_SESSION_PROFILE.format(uuid=uuid)
    elif kind in ("authlib", "nide8") and acc.get("api"):
        api = str(acc["api"]).rstrip("/")
        profile_url = f"{api}/sessionserver/session/minecraft/profile/{uuid}"
    else:
        return {}
    try:
        info = _profile_skin(profile_url, timeout=timeout)
    except Exception as exc:
        utils.log.debug("查询皮肤档案失败 %s: %s", profile_url, exc)
        return {}
    if not info:
        return {}
    cache = _texture_cache_path(info["url"])
    if not cache.is_file():
        import requests
        try:
            resp = requests.get(info["url"], timeout=timeout)
        except Exception as exc:
            utils.log.debug("下载皮肤纹理失败 %s: %s", info["url"], exc)
            return {}
        if resp.status_code != 200 or not resp.content.startswith(b"\x89PNG"):
            return {}
        utils.ensure_dir(cache.parent)
        tmp = cache.with_suffix(".png.part")
        tmp.write_bytes(resp.content)
        tmp.replace(cache)
    return {"file": str(cache), "model": info["model"]}
