# -*- coding: utf-8 -*-
"""皮肤：头像 / 全身预览 URL + 正版与皮肤站的皮肤上传、重置、披风管理。"""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import quote, urlparse

from . import utils

STEVE = "https://mc-heads.net/avatar/Steve/128"
BODY = "https://mc-heads.net/body/{}/180"

# 微软正版皮肤 / 披风管理端点（可注入 base 便于测试）
MS_PROFILE_API = "https://api.minecraftservices.com/minecraft/profile"

# 正版玩家公开查询端点（可注入便于测试）
MOJANG_UUID_API = "https://api.mojang.com/users/profiles/minecraft"
MOJANG_SESSION_API = "https://sessionserver.mojang.com/session/minecraft/profile"

_NAME_RE = re.compile(r"^[A-Za-z0-9_]{1,16}$")

# 皮肤文件宽松上限：官方皮肤 64x64 PNG 通常只有几 KB
_MAX_SKIN_BYTES = 512 * 1024
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


class SkinError(Exception):
    """皮肤操作失败，消息可直接展示给用户。"""


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


# ---------------------------------------------------------------- 皮肤文件校验

def png_size(data: bytes) -> tuple[int, int]:
    """解析 PNG IHDR 拿宽高；不是 PNG 就抛 SkinError。"""
    if len(data) < 24 or not data.startswith(_PNG_MAGIC) or data[12:16] != b"IHDR":
        raise SkinError("皮肤必须是 PNG 图片")
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    return width, height


def read_skin_file(path) -> bytes:
    """读取并校验本地皮肤文件：PNG、64×64 或 64×32（旧版）、大小合理。"""
    p = Path(str(path or "")).expanduser()
    if not p.is_file():
        raise SkinError(f"找不到皮肤文件: {p}")
    if p.stat().st_size > _MAX_SKIN_BYTES:
        raise SkinError("皮肤文件过大（超过 512 KB），请使用标准 64×64 皮肤 PNG")
    data = p.read_bytes()
    width, height = png_size(data)
    if (width, height) not in ((64, 64), (64, 32)):
        raise SkinError(f"皮肤尺寸必须是 64×64 或 64×32，当前为 {width}×{height}")
    return data


def normalize_variant(variant: str) -> str:
    """把 UI 传来的模型叫法统一成 API 词汇：classic / slim。"""
    v = str(variant or "").strip().lower()
    return "slim" if v in ("slim", "alex", "纤细") else "classic"


# ---------------------------------------------------------------- 微软正版皮肤

def _ms_headers(access_token: str) -> dict:
    return {"Authorization": f"Bearer {access_token}"}


def _raise_ms(resp, action: str):
    if resp.status_code in (200, 204):
        return
    if resp.status_code == 401:
        raise SkinError("正版令牌已失效，请重新登录微软账号后再试")
    if resp.status_code == 429:
        raise SkinError("请求过于频繁，Mojang 暂时限流了，请稍等几分钟再试")
    detail = ""
    try:
        body = resp.json()
        detail = body.get("errorMessage") or body.get("error") or ""
    except Exception:
        detail = (resp.text or "")[:200]
    raise SkinError(f"{action}失败（HTTP {resp.status_code}）: {detail or '未知错误'}")


def parse_ms_profile(data: dict) -> dict:
    """把 Mojang profile 响应整理成 UI 好用的结构。"""
    skins = []
    variant = "classic"
    for s in data.get("skins") or []:
        entry = {
            "id": s.get("id") or "",
            "url": s.get("url") or "",
            "variant": str(s.get("variant") or "CLASSIC").lower(),
            "active": (s.get("state") or "").upper() == "ACTIVE",
        }
        if entry["active"]:
            variant = entry["variant"]
        skins.append(entry)
    capes = []
    active_cape = ""
    for c in data.get("capes") or []:
        entry = {
            "id": c.get("id") or "",
            "alias": c.get("alias") or c.get("id") or "",
            "url": c.get("url") or "",
            "active": (c.get("state") or "").upper() == "ACTIVE",
        }
        if entry["active"]:
            active_cape = entry["id"]
        capes.append(entry)
    return {
        "name": data.get("name") or "",
        "uuid": data.get("id") or "",
        "skins": skins,
        "capes": capes,
        "variant": variant,
        "active_cape": active_cape,
    }


def fetch_ms_profile(access_token: str, api_base: str = MS_PROFILE_API,
                     timeout: int = 15) -> dict:
    import requests
    resp = requests.get(api_base, headers=_ms_headers(access_token), timeout=timeout)
    _raise_ms(resp, "获取皮肤信息")
    return parse_ms_profile(resp.json())


def upload_ms_skin(access_token: str, path, variant: str = "classic",
                   api_base: str = MS_PROFILE_API, timeout: int = 30) -> dict:
    import requests
    data = read_skin_file(path)
    resp = requests.post(
        f"{api_base}/skins",
        headers=_ms_headers(access_token),
        data={"variant": normalize_variant(variant)},
        files={"file": ("skin.png", data, "image/png")},
        timeout=timeout,
    )
    _raise_ms(resp, "上传皮肤")
    try:
        return parse_ms_profile(resp.json())
    except (ValueError, AttributeError):
        return {}


def reset_ms_skin(access_token: str, api_base: str = MS_PROFILE_API,
                  timeout: int = 30):
    import requests
    resp = requests.delete(
        f"{api_base}/skins/active", headers=_ms_headers(access_token), timeout=timeout)
    _raise_ms(resp, "重置皮肤")


def set_ms_cape(access_token: str, cape_id: str, api_base: str = MS_PROFILE_API,
                timeout: int = 30) -> dict:
    """cape_id 为空表示隐藏披风。"""
    import requests
    if cape_id:
        resp = requests.put(
            f"{api_base}/capes/active", headers=_ms_headers(access_token),
            json={"capeId": cape_id}, timeout=timeout)
        _raise_ms(resp, "更换披风")
    else:
        resp = requests.delete(
            f"{api_base}/capes/active", headers=_ms_headers(access_token),
            timeout=timeout)
        _raise_ms(resp, "隐藏披风")
    try:
        return parse_ms_profile(resp.json())
    except (ValueError, AttributeError):
        return {}


# ---------------------------------------------------------------- 皮肤站（authlib-injector）

def _ygg_texture_url(api: str, uuid: str) -> str:
    root = str(api or "").rstrip("/")
    plain = utils.dashed_uuid(uuid or "").replace("-", "")
    if not root or not plain:
        raise SkinError("皮肤站账号缺少 API 地址或 UUID，无法上传皮肤")
    return f"{root}/api/user/profile/{plain}/skin"


def _raise_ygg(resp, action: str):
    if resp.status_code in (200, 204):
        return
    if resp.status_code == 401:
        raise SkinError("皮肤站令牌已失效，请重新登录后再试")
    if resp.status_code in (403, 404, 405, 501):
        raise SkinError("该皮肤站不支持从启动器上传皮肤，请到皮肤站网页操作")
    detail = ""
    try:
        detail = resp.json().get("errorMessage") or ""
    except Exception:
        detail = (resp.text or "")[:200]
    raise SkinError(f"{action}失败（HTTP {resp.status_code}）: {detail or '未知错误'}")


def upload_ygg_skin(api: str, access_token: str, uuid: str, path,
                    variant: str = "classic", timeout: int = 30):
    """authlib-injector 规范的材质上传：PUT /api/user/profile/{uuid}/skin。"""
    import requests
    data = read_skin_file(path)
    model = "slim" if normalize_variant(variant) == "slim" else ""
    resp = requests.put(
        _ygg_texture_url(api, uuid),
        headers={"Authorization": f"Bearer {access_token}"},
        data={"model": model},
        files={"file": ("skin.png", data, "image/png")},
        timeout=timeout,
    )
    _raise_ygg(resp, "上传皮肤")


def reset_ygg_skin(api: str, access_token: str, uuid: str, timeout: int = 30):
    import requests
    resp = requests.delete(
        _ygg_texture_url(api, uuid),
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=timeout,
    )
    _raise_ygg(resp, "重置皮肤")


# ---------------------------------------------------------------- 皮肤纹理获取（本地渲染用）

def fetch_ygg_texture_info(api: str, uuid: str, timeout: int = 15) -> dict:
    """从 Yggdrasil sessionserver 拿皮肤纹理 URL 与模型。"""
    import base64 as b64
    import json as json_mod
    import requests
    root = str(api or "").rstrip("/")
    plain = utils.dashed_uuid(uuid or "").replace("-", "")
    if not root or not plain:
        raise SkinError("缺少皮肤站 API 地址或 UUID")
    resp = requests.get(
        f"{root}/sessionserver/session/minecraft/profile/{plain}",
        timeout=timeout)
    if resp.status_code != 200:
        raise SkinError(f"查询皮肤纹理失败（HTTP {resp.status_code}）")
    props = (resp.json() or {}).get("properties") or []
    payload = next((p.get("value") for p in props
                    if p.get("name") == "textures"), "")
    if not payload:
        raise SkinError("该角色没有皮肤纹理")
    data = json_mod.loads(b64.b64decode(payload).decode("utf-8", "replace"))
    skin_info = (data.get("textures") or {}).get("SKIN") or {}
    url = skin_info.get("url") or ""
    if not url:
        raise SkinError("该角色没有皮肤纹理")
    model = ((skin_info.get("metadata") or {}).get("model") or "").lower()
    return {"url": url, "variant": "slim" if model == "slim" else "classic"}


def fetch_skin_texture(account: dict, timeout: int = 15) -> dict:
    """下载账号当前皮肤的原始 64x64 PNG，供本地渲染预览。

    返回 {"png": bytes, "variant": "classic"/"slim"}。
    只支持微软 / 皮肤站账号；其它类型抛 SkinError（调用方回退第三方渲染）。
    """
    import requests
    acc = account or {}
    kind = acc.get("type") or "offline"
    if kind == "microsoft":
        profile = fetch_ms_profile(acc.get("access_token") or "", timeout=timeout)
        active = next((s for s in profile["skins"] if s.get("active")),
                      profile["skins"][0] if profile["skins"] else None)
        if not active or not active.get("url"):
            raise SkinError("该账号没有皮肤")
        url, variant = active["url"], active.get("variant") or "classic"
    elif kind == "authlib":
        info = fetch_ygg_texture_info(acc.get("api") or "", acc.get("uuid") or "",
                                      timeout=timeout)
        url, variant = info["url"], info["variant"]
    else:
        raise SkinError("离线 / 通行证账号没有云端皮肤纹理")
    resp = requests.get(url, timeout=timeout)
    if resp.status_code != 200:
        raise SkinError(f"下载皮肤纹理失败（HTTP {resp.status_code}）")
    png = resp.content
    width, height = png_size(png)
    if width != 64 or height not in (32, 64):
        raise SkinError(f"皮肤纹理尺寸异常: {width}×{height}")
    return {"png": png, "variant": variant}


# ---------------------------------------------------------------- 正版玩家查询（对标 PCL 百宝箱）

def lookup_player(name: str, timeout: int = 15) -> dict:
    """按玩家 ID 查正版档案：UUID、皮肤 URL / 模型、披风 URL。"""
    import base64 as b64
    import json as json_mod
    import requests
    ident = str(name or "").strip()
    if not _NAME_RE.match(ident):
        raise SkinError("玩家名只能是 1–16 位字母、数字或下划线")
    resp = requests.get(f"{MOJANG_UUID_API}/{quote(ident)}", timeout=timeout)
    if resp.status_code in (204, 404):
        raise SkinError(f"正版玩家不存在: {ident}")
    if resp.status_code == 429:
        raise SkinError("请求过于频繁，Mojang 暂时限流了，请稍等几分钟再试")
    if resp.status_code != 200:
        raise SkinError(f"查询玩家失败（HTTP {resp.status_code}）")
    body = resp.json() or {}
    uuid = body.get("id") or ""
    real_name = body.get("name") or ident
    if not uuid:
        raise SkinError(f"正版玩家不存在: {ident}")
    resp = requests.get(f"{MOJANG_SESSION_API}/{uuid}", timeout=timeout)
    if resp.status_code != 200:
        raise SkinError(f"查询皮肤信息失败（HTTP {resp.status_code}）")
    data = resp.json() or {}
    payload = next((p.get("value") for p in (data.get("properties") or [])
                    if p.get("name") == "textures"), "")
    skin_url = cape_url = ""
    variant = "classic"
    if payload:
        textures = (json_mod.loads(b64.b64decode(payload).decode("utf-8", "replace"))
                    .get("textures") or {})
        skin = textures.get("SKIN") or {}
        skin_url = skin.get("url") or ""
        model = ((skin.get("metadata") or {}).get("model") or "").lower()
        variant = "slim" if model == "slim" else "classic"
        cape_url = (textures.get("CAPE") or {}).get("url") or ""
    return {"name": real_name, "uuid": uuid, "skin_url": skin_url,
            "variant": variant, "cape_url": cape_url}


def fetch_player_skin(name: str, timeout: int = 15) -> dict:
    """下载正版玩家皮肤。有自定义皮肤时结果带 "png" 字节；默认皮肤则没有。"""
    import requests
    info = lookup_player(name, timeout=timeout)
    if not info.get("skin_url"):
        return info
    resp = requests.get(info["skin_url"], timeout=timeout)
    if resp.status_code != 200:
        raise SkinError(f"下载皮肤失败（HTTP {resp.status_code}）")
    png = resp.content
    width, height = png_size(png)
    if width != 64 or height not in (32, 64):
        raise SkinError(f"皮肤纹理尺寸异常: {width}×{height}")
    info["png"] = png
    return info
