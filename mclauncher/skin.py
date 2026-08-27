# -*- coding: utf-8 -*-
"""皮肤头像 / 全身预览 URL + 微软账号皮肤/披风管理。"""
from __future__ import annotations

import re
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

# 微软正版皮肤 / 披风管理端点（可注入 base 便于测试，皮肤分支 API）
MS_PROFILE_API = "https://api.minecraftservices.com/minecraft/profile"

# 正版玩家公开查询端点（可注入便于测试）
MOJANG_UUID_API = "https://api.mojang.com/users/profiles/minecraft"
MOJANG_SESSION_API = "https://sessionserver.mojang.com/session/minecraft/profile"

# 皮肤分支的宽松上限（read_skin_file 用）
_MAX_SKIN_BYTES = 512 * 1024


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


# ---------------------------------------------------------------- 玩家档案查询
# PCL2 百宝箱「IGN / UUID 查询」同款：任意正版玩家名或 UUID → 档案 + 皮肤。

_NAME_RE = re.compile(r"[A-Za-z0-9_]{1,16}$")
_HEX_RE = re.compile(r"[0-9a-fA-F]{32}$")


def lookup_player(query: str, session=None, timeout=15) -> dict:
    """按正版玩家名或 UUID（带不带连字符都行）查询档案。

    返回 {name, uuid, skin_url, cape_url, variant, avatar, body}。
    找不到 / 输入不合法抛 SkinError。
    """
    import base64
    import json as _json

    q = str(query or "").strip()
    if not q:
        raise SkinError("请输入玩家名或 UUID")
    s = session or _api_session()
    hexq = q.replace("-", "")
    if _HEX_RE.fullmatch(hexq):
        raw_uuid = hexq.lower()
    else:
        if not _NAME_RE.fullmatch(q):
            raise SkinError("玩家名只能是 1~16 位字母 / 数字 / 下划线")
        resp = s.get(f"{MOJANG_UUID_API}/{quote(q)}", timeout=timeout)
        if resp.status_code in (204, 404):
            raise SkinError(f"找不到玩家（该正版玩家不存在）: {q}")
        if resp.status_code == 429:
            raise SkinError("查询太频繁，请稍后再试")
        if resp.status_code != 200:
            raise SkinError(f"查询失败（HTTP {resp.status_code}）")
        raw_uuid = str((resp.json() or {}).get("id") or "").lower()
        if not raw_uuid:
            raise SkinError(f"找不到玩家（该正版玩家不存在）: {q}")
    resp = s.get(f"{MOJANG_SESSION_API}/{raw_uuid}", timeout=timeout)
    if resp.status_code in (204, 404):
        raise SkinError(f"找不到该 UUID 的档案: {q}")
    if resp.status_code != 200:
        raise SkinError(f"查询失败（HTTP {resp.status_code}）")
    data = resp.json() or {}
    name = data.get("name") or q
    skin_url = cape_url = ""
    variant = "classic"
    for prop in data.get("properties") or []:
        if prop.get("name") != "textures":
            continue
        try:
            tex = _json.loads(base64.b64decode(prop.get("value") or "").decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            continue
        textures = (tex or {}).get("textures") or {}
        skin = textures.get("SKIN") or {}
        skin_url = skin.get("url") or ""
        if str((skin.get("metadata") or {}).get("model") or "").lower() == "slim":
            variant = "slim"
        cape_url = (textures.get("CAPE") or {}).get("url") or ""
    return {
        "name": name,
        "uuid": utils.dashed_uuid(raw_uuid),
        "skin_url": skin_url,
        "cape_url": cape_url,
        "variant": variant,
        "avatar": f"https://crafatar.com/avatars/{raw_uuid}?overlay=true&size=128",
        "body": f"https://crafatar.com/renders/body/{raw_uuid}?overlay=true&scale=6",
    }


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
    cape_url = ((data.get("textures") or {}).get("CAPE") or {}).get("url") or ""
    return {"url": url, "variant": "slim" if model == "slim" else "classic",
            "cape_url": cape_url}


def fetch_skin_texture(account: dict, timeout: int = 15) -> dict:
    """下载账号当前皮肤的原始 64x64 PNG，供本地渲染预览。

    返回 {"png": bytes, "variant": "classic"/"slim"}；有披风时额外带
    "cape_png"（披风拿不到不影响皮肤，静默跳过）。
    支持微软 / 皮肤站账号；离线账号读本地自选皮肤；
    其它类型抛 SkinError（调用方回退第三方渲染）。
    """
    import requests
    acc = account or {}
    kind = acc.get("type") or "offline"
    if kind == "offline" and acc.get("skin_file"):
        p = Path(str(acc["skin_file"])).expanduser()
        if not p.is_file():
            raise SkinError(f"离线皮肤文件不存在: {p}")
        png = p.read_bytes()
        width, height = png_size(png)
        if width != 64 or height not in (32, 64):
            raise SkinError(f"皮肤纹理尺寸异常: {width}×{height}")
        out = {"png": png,
               "variant": "slim" if acc.get("skin_model") == "slim" else "classic"}
        cape_p = Path(str(acc.get("cape_file") or "")).expanduser()
        if acc.get("cape_file") and cape_p.is_file():
            out["cape_png"] = cape_p.read_bytes()
        return out
    cape_url = ""
    if kind == "microsoft":
        profile = fetch_ms_profile(acc.get("access_token") or "", timeout=timeout)
        active = next((s for s in profile["skins"] if s.get("active")),
                      profile["skins"][0] if profile["skins"] else None)
        if not active or not active.get("url"):
            raise SkinError("该账号没有皮肤")
        url, variant = active["url"], active.get("variant") or "classic"
        cape = next((c for c in profile.get("capes") or [] if c.get("active")), None)
        cape_url = (cape or {}).get("url") or ""
    elif kind == "authlib":
        info = fetch_ygg_texture_info(acc.get("api") or "", acc.get("uuid") or "",
                                      timeout=timeout)
        url, variant = info["url"], info["variant"]
        cape_url = info.get("cape_url") or ""
    else:
        raise SkinError("离线 / 通行证账号没有云端皮肤纹理")
    resp = requests.get(url, timeout=timeout)
    if resp.status_code != 200:
        raise SkinError(f"下载皮肤纹理失败（HTTP {resp.status_code}）")
    png = resp.content
    width, height = png_size(png)
    if width != 64 or height not in (32, 64):
        raise SkinError(f"皮肤纹理尺寸异常: {width}×{height}")
    out = {"png": png, "variant": variant}
    if cape_url:
        try:
            cape_resp = requests.get(cape_url, timeout=timeout)
            if cape_resp.status_code == 200 and cape_resp.content:
                out["cape_png"] = cape_resp.content
        except requests.RequestException:
            pass  # 披风只是锦上添花，拿不到不挡皮肤预览
    return out


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
