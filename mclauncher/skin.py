# -*- coding: utf-8 -*-
"""皮肤头像 / 全身预览 URL + 微软账号皮肤/披风管理。"""
from __future__ import annotations

import struct
from pathlib import Path
from urllib.parse import quote, urlparse

from . import utils

STEVE = "https://mc-heads.net/avatar/Steve/128"
BODY = "https://mc-heads.net/body/{}/180"

PROFILE_URL = "https://api.minecraftservices.com/minecraft/profile"
SKIN_URL = PROFILE_URL + "/skins"
SKIN_ACTIVE_URL = SKIN_URL + "/active"
CAPE_ACTIVE_URL = PROFILE_URL + "/capes/active"

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
# 官方接口对皮肤文件本身有大小限制；本地先拦住明显不对的文件，报错更友好。
MAX_SKIN_BYTES = 128 * 1024
VARIANTS = ("classic", "slim")


class SkinError(Exception):
    """皮肤操作失败（本地校验或远端接口报错）。"""


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


# ---------------------------------------------------------------- 微软皮肤管理
#
# 官方接口（需要 Minecraft 服务令牌）：
#   GET    /minecraft/profile               当前皮肤 / 披风列表
#   POST   /minecraft/profile/skins         上传皮肤（multipart: variant + file）
#   DELETE /minecraft/profile/skins/active  重置为默认皮肤
#   PUT    /minecraft/profile/capes/active  启用某披风 {"capeId": ...}
#   DELETE /minecraft/profile/capes/active  隐藏披风


def _api_session():
    import requests

    from . import net
    session = requests.Session()
    net.apply_direct_to_session(session)
    return session


def validate_skin_png(path) -> tuple[int, int]:
    """本地校验皮肤文件：必须是 64x64 或 64x32 的 PNG。返回 (宽, 高)。"""
    p = Path(path)
    if not p.is_file():
        raise SkinError(f"文件不存在: {p}")
    if p.stat().st_size > MAX_SKIN_BYTES:
        raise SkinError("皮肤文件过大（超过 128 KB），请使用标准 64x64 PNG 皮肤。")
    with open(p, "rb") as f:
        head = f.read(33)
    if len(head) < 33 or not head.startswith(_PNG_MAGIC) or head[12:16] != b"IHDR":
        raise SkinError("不是有效的 PNG 图片，皮肤必须是 PNG 格式。")
    width, height = struct.unpack(">II", head[16:24])
    if (width, height) not in ((64, 64), (64, 32)):
        raise SkinError(f"皮肤尺寸必须是 64x64 或 64x32，当前是 {width}x{height}。")
    return int(width), int(height)


def _explain_http(resp) -> str:
    if resp.status_code == 401:
        return "登录令牌已失效，请重新登录微软账号后再试。"
    detail = ""
    try:
        body = resp.json()
        detail = body.get("errorMessage") or body.get("error") or ""
    except Exception:
        pass
    return f"HTTP {resp.status_code}" + (f": {detail}" if detail else "")


def _profile_or_refetch(resp, access_token, session, timeout):
    """写操作有的返回新档案有的返回空体；空体就再拉一次档案。"""
    try:
        data = resp.json()
        if isinstance(data, dict) and data.get("id"):
            return data
    except Exception:
        pass
    return fetch_profile(access_token, session=session, timeout=timeout)


def fetch_profile(access_token: str, session=None, timeout=15) -> dict:
    """拉取正版档案（含皮肤、披风原始列表）。"""
    s = session or _api_session()
    resp = s.get(PROFILE_URL, headers={"Authorization": f"Bearer {access_token}"},
                 timeout=timeout)
    if resp.status_code == 404:
        raise SkinError("该账号尚未创建 Minecraft 档案。")
    if resp.status_code != 200:
        raise SkinError(f"获取皮肤档案失败（{_explain_http(resp)}）")
    return resp.json()


def upload_skin(access_token: str, png_path, variant: str = "classic",
                session=None, timeout=30) -> dict:
    """上传皮肤 PNG 并设为当前皮肤。variant: classic（粗臂）/ slim（细臂）。"""
    variant = str(variant or "classic").lower()
    if variant not in VARIANTS:
        raise SkinError(f"模型类型必须是 classic 或 slim，收到: {variant}")
    validate_skin_png(png_path)
    data = Path(png_path).read_bytes()
    s = session or _api_session()
    resp = s.post(
        SKIN_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        data={"variant": variant},
        files={"file": (Path(png_path).name or "skin.png", data, "image/png")},
        timeout=timeout,
    )
    if resp.status_code not in (200, 204):
        raise SkinError(f"上传皮肤失败（{_explain_http(resp)}）")
    return _profile_or_refetch(resp, access_token, s, timeout)


def reset_skin(access_token: str, session=None, timeout=15) -> dict:
    """恢复默认皮肤（Steve/Alex，由 UUID 决定）。"""
    s = session or _api_session()
    resp = s.delete(SKIN_ACTIVE_URL, headers={"Authorization": f"Bearer {access_token}"},
                    timeout=timeout)
    if resp.status_code not in (200, 204):
        raise SkinError(f"重置皮肤失败（{_explain_http(resp)}）")
    return _profile_or_refetch(resp, access_token, s, timeout)


def set_cape(access_token: str, cape_id: str = "", session=None, timeout=15) -> dict:
    """启用披风；cape_id 为空则隐藏披风。"""
    s = session or _api_session()
    headers = {"Authorization": f"Bearer {access_token}"}
    if cape_id:
        resp = s.put(CAPE_ACTIVE_URL, headers=headers, json={"capeId": cape_id},
                     timeout=timeout)
        what = "启用披风"
    else:
        resp = s.delete(CAPE_ACTIVE_URL, headers=headers, timeout=timeout)
        what = "隐藏披风"
    if resp.status_code not in (200, 204):
        raise SkinError(f"{what}失败（{_explain_http(resp)}）")
    return _profile_or_refetch(resp, access_token, s, timeout)


def summarize_profile(data: dict) -> dict:
    """把官方档案 JSON 压成 UI 需要的结构。"""
    data = data or {}
    skin_url = ""
    variant = "classic"
    for entry in data.get("skins") or []:
        if entry.get("state") == "ACTIVE":
            skin_url = entry.get("url") or ""
            variant = str(entry.get("variant") or "CLASSIC").lower()
            break
    capes = []
    active_cape = ""
    for entry in data.get("capes") or []:
        cape = {
            "id": entry.get("id") or "",
            "alias": entry.get("alias") or entry.get("id") or "?",
            "url": entry.get("url") or "",
            "active": entry.get("state") == "ACTIVE",
        }
        if cape["active"]:
            active_cape = cape["id"]
        capes.append(cape)
    return {
        "uuid": utils.dashed_uuid(data.get("id") or ""),
        "name": data.get("name") or "",
        "skin_url": skin_url,
        "variant": variant,
        "capes": capes,
        "active_cape": active_cape,
    }


def skin_site_url(account: dict | None) -> str:
    """皮肤站账号：返回站点首页（皮肤在网站上改）。其他类型返回空。"""
    acc = account or {}
    if acc.get("type") == "authlib" and acc.get("api"):
        return _site_origin(acc["api"])
    return ""
