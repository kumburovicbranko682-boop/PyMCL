# -*- coding: utf-8 -*-
"""皮肤更换 / 重置。

- 微软正版：Minecraft Services API（POST /minecraft/profile/skins，
  DELETE /minecraft/profile/skins/active）。
- 皮肤站（authlib-injector）：Yggdrasil 材质接口
  （PUT/DELETE /api/user/profile/{uuid}/skin）。
"""
from __future__ import annotations

import struct
from pathlib import Path

import requests

from . import utils

MS_SKIN_URL = "https://api.minecraftservices.com/minecraft/profile/skins"
MS_SKIN_RESET_URL = "https://api.minecraftservices.com/minecraft/profile/skins/active"

# Mojang 官方皮肤上限 24 KiB
MS_MAX_BYTES = 24576

VARIANTS = ("classic", "slim")


class SkinError(Exception):
    pass


# ---------------------------------------------------------------- 校验

def read_png_size(data: bytes) -> tuple[int, int]:
    """解析 PNG IHDR 得到 (宽, 高)；不是 PNG 则抛 SkinError。"""
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        raise SkinError("皮肤必须是 PNG 图片")
    if data[12:16] != b"IHDR":
        raise SkinError("PNG 文件损坏（缺少 IHDR）")
    width, height = struct.unpack(">II", data[16:24])
    return int(width), int(height)


def load_skin_file(path: str, strict_mojang: bool = True) -> bytes:
    """读取并校验皮肤 PNG。

    strict_mojang: 微软正版要求 64x64 / 64x32 且 ≤24KB；
    皮肤站允许 HD 皮肤（比例 1:1 或 2:1，宽 ≥64），大小交给服务端判断。
    """
    p = Path(path or "")
    if not p.is_file():
        raise SkinError(f"皮肤文件不存在: {path}")
    data = p.read_bytes()
    width, height = read_png_size(data)
    if strict_mojang:
        if (width, height) not in ((64, 64), (64, 32)):
            raise SkinError(f"皮肤尺寸 {width}x{height} 不受支持（需 64x64 或 64x32）")
        if len(data) > MS_MAX_BYTES:
            raise SkinError(f"皮肤文件 {len(data)} 字节，超过官方 24KB 上限")
    else:
        if width < 64 or (width != height and width != height * 2):
            raise SkinError(f"皮肤尺寸 {width}x{height} 不受支持（宽高比需 1:1 或 2:1，宽 ≥64）")
    return data


def normalize_variant(variant: str) -> str:
    v = str(variant or "").strip().lower()
    if v in ("slim", "alex", "纤细"):
        return "slim"
    return "classic"


# ---------------------------------------------------------------- 能力判定

def change_support(account: dict | None) -> dict:
    """账号是否支持在启动器内更换皮肤。返回 {ok, reason, note}。"""
    acc = account or {}
    kind = acc.get("type") or ""
    if kind == "microsoft":
        return {"ok": True, "reason": "", "note": ""}
    if kind == "authlib":
        return {"ok": True, "reason": "", "note": ""}
    if kind == "nide8":
        return {"ok": False, "reason": "统一通行证账号请到对应服务器的通行证网站更换皮肤",
                "note": ""}
    if kind == "offline":
        return {"ok": True, "reason": "",
                "note": "离线皮肤由 PyMCL 启动时在本机提供（authlib-injector），仅本机可见"}
    return {"ok": False, "reason": "请先登录微软正版或皮肤站账号", "note": ""}


# ---------------------------------------------------------------- 请求

def _error_detail(resp: requests.Response) -> str:
    try:
        data = resp.json()
    except ValueError:
        data = {}
    if isinstance(data, dict):
        msg = data.get("errorMessage") or data.get("error") or data.get("message")
        if msg:
            return str(msg)
    return (resp.text or "")[:200]


def _check(resp: requests.Response, action: str):
    if resp.status_code in (200, 204):
        return
    if resp.status_code == 401:
        raise SkinError(f"{action}失败：登录令牌已失效，请重新登录该账号")
    detail = _error_detail(resp)
    raise SkinError(f"{action}失败（HTTP {resp.status_code}）: {detail}")


def _bearer(account: dict) -> dict:
    token = account.get("access_token") or ""
    if not token or token == "0":
        raise SkinError("该账号没有可用的登录令牌，请重新登录")
    return {"Authorization": f"Bearer {token}"}


def _ygg_texture_url(account: dict) -> str:
    from .authlib import normalize_api
    api = normalize_api(account.get("api") or "")
    uuid = utils.dashed_uuid(account.get("uuid") or "").replace("-", "")
    if not uuid:
        raise SkinError("账号缺少 UUID，请重新登录")
    return f"{api}/api/user/profile/{uuid}/skin"


def upload_skin(account: dict, path: str, variant: str = "classic", timeout: int = 30) -> str:
    """上传皮肤。返回成功提示文本。"""
    support = change_support(account)
    if not support["ok"]:
        raise SkinError(support["reason"])
    variant = normalize_variant(variant)
    kind = account.get("type")
    if kind == "offline":
        # 本地皮肤：写进账号记录（调用方负责 accounts.save()），
        # 启动时由 offline_skin 的本地 Yggdrasil 服务提供
        from . import offline_skin
        return offline_skin.apply_to_account(account, path, variant)
    if kind == "microsoft":
        data = load_skin_file(path, strict_mojang=True)
        try:
            resp = requests.post(
                MS_SKIN_URL,
                headers=_bearer(account),
                files={"file": ("skin.png", data, "image/png")},
                data={"variant": variant},
                timeout=timeout,
            )
        except requests.RequestException as exc:
            raise SkinError(f"上传皮肤失败：无法连接 Minecraft 服务: {exc}") from exc
        _check(resp, "上传皮肤")
    else:  # authlib
        data = load_skin_file(path, strict_mojang=False)
        try:
            resp = requests.put(
                _ygg_texture_url(account),
                headers=_bearer(account),
                files={"file": ("skin.png", data, "image/png")},
                data={"model": "slim" if variant == "slim" else ""},
                timeout=timeout,
            )
        except requests.RequestException as exc:
            raise SkinError(f"上传皮肤失败：无法连接皮肤站: {exc}") from exc
        if resp.status_code == 405 or resp.status_code == 501:
            raise SkinError("该皮肤站不支持在启动器内上传皮肤，请到皮肤站网站更换")
        _check(resp, "上传皮肤")
    model = "纤细 (Alex)" if variant == "slim" else "经典 (Steve)"
    return f"皮肤已更换（{model} 模型）。游戏内立即生效，预览图可能延迟几分钟刷新"


def reset_skin(account: dict, timeout: int = 30) -> str:
    """重置为默认皮肤。返回成功提示文本。"""
    support = change_support(account)
    if not support["ok"]:
        raise SkinError(support["reason"])
    kind = account.get("type")
    if kind == "offline":
        from . import offline_skin
        return offline_skin.clear_account(account)
    if kind == "microsoft":
        try:
            resp = requests.delete(MS_SKIN_RESET_URL, headers=_bearer(account), timeout=timeout)
        except requests.RequestException as exc:
            raise SkinError(f"重置皮肤失败：无法连接 Minecraft 服务: {exc}") from exc
        _check(resp, "重置皮肤")
    else:  # authlib
        try:
            resp = requests.delete(_ygg_texture_url(account), headers=_bearer(account), timeout=timeout)
        except requests.RequestException as exc:
            raise SkinError(f"重置皮肤失败：无法连接皮肤站: {exc}") from exc
        if resp.status_code == 405 or resp.status_code == 501:
            raise SkinError("该皮肤站不支持在启动器内重置皮肤，请到皮肤站网站操作")
        _check(resp, "重置皮肤")
    return "皮肤已重置为默认。预览图可能延迟几分钟刷新"
