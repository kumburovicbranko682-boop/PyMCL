# -*- coding: utf-8 -*-
"""统一通行证（Nide8）登录。Yggdrasil + nide8auth.jar。"""
from __future__ import annotations

import re
import time
from pathlib import Path
from urllib.parse import urlparse

import requests

from . import utils
from .auth import AuthError
from .downloader import DownloadManager

NIDE8_AUTH = "https://auth.mc-user.com:233"
NIDE8_JAR_URL = "https://login.mc-user.com:233/index/jar"
_SID_RE = re.compile(r"([0-9a-fA-F]{32})")


def normalize_server_id(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        raise AuthError("请填写统一通行证服务器 ID")
    m = _SID_RE.search(text)
    if m:
        return m.group(1).lower()
    parsed = urlparse(text if "://" in text else "https://" + text)
    m = _SID_RE.search(parsed.path or "")
    if m:
        return m.group(1).lower()
    raise AuthError("服务器 ID 应为 32 位十六进制，或含该 ID 的链接")


def api_url(server_id: str) -> str:
    return f"{NIDE8_AUTH}/{normalize_server_id(server_id)}"


def jar_path() -> Path:
    return utils.ROOT / "nide8auth.jar"


def ensure_jar(dm: DownloadManager | None = None, on_note=None) -> Path:
    dest = jar_path()
    if dest.is_file() and dest.stat().st_size > 8_000:
        return dest
    dm = dm or DownloadManager(threads=2)
    if on_note:
        on_note("下载 nide8auth")
    dm.download(NIDE8_JAR_URL, dest, timeout=60)
    if not dest.is_file() or dest.stat().st_size < 8_000:
        raise AuthError("无法下载 nide8auth.jar")
    return dest


def javaagent_arg(server_id: str) -> str:
    sid = normalize_server_id(server_id)
    return f"-javaagent:{jar_path()}={sid}"


def _post(sid: str, path: str, payload: dict) -> dict:
    url = api_url(sid) + path
    try:
        resp = requests.post(url, json=payload, timeout=20)
    except requests.RequestException as exc:
        raise AuthError(f"统一通行证无法连接: {exc}") from exc
    try:
        data = resp.json()
    except ValueError:
        data = {}
    if resp.status_code >= 400:
        err = data.get("errorMessage") or data.get("error") or resp.text[:200]
        raise AuthError(f"统一通行证登录失败: {err}")
    return data


def _build_account(sid: str, username: str, data: dict, profile: dict) -> dict:
    return {
        "type": "nide8",
        "name": profile.get("name"),
        "uuid": utils.dashed_uuid(profile.get("id") or ""),
        "access_token": data.get("accessToken") or "0",
        "client_token": data.get("clientToken") or "",
        "server_id": sid,
        "api": api_url(sid),
        "username": username,
        "expires_at": time.time() + 7 * 24 * 3600,
        "updated_at": time.time(),
    }


def login(server_id: str, username: str, password: str, profile_id: str = "") -> dict:
    """统一通行证登录。多角色且未指定 profile_id 时返回 pending 结构。"""
    sid = normalize_server_id(server_id)
    username = (username or "").strip()
    if not username or not password:
        raise AuthError("请输入统一通行证账号和密码")
    data = _post(sid, "/authserver/authenticate", {
        "agent": {"name": "Minecraft", "version": 1},
        "username": username,
        "password": password,
        "requestUser": True,
    })
    profile = data.get("selectedProfile") or {}
    profiles = [p for p in (data.get("availableProfiles") or []) if isinstance(p, dict)]
    if profile_id:
        hit = next((p for p in profiles if str(p.get("id")) == str(profile_id)), None)
        if hit:
            return select_profile(sid, data.get("accessToken") or "",
                                  data.get("clientToken") or "", hit, username)
    if profile.get("name"):
        return _build_account(sid, username, data, profile)
    if len(profiles) == 1:
        return select_profile(sid, data.get("accessToken") or "",
                              data.get("clientToken") or "", profiles[0], username)
    if len(profiles) > 1:
        return {
            "pending": True,
            "kind": "nide8",
            "server_id": sid,
            "username": username,
            "access_token": data.get("accessToken") or "",
            "client_token": data.get("clientToken") or "",
            "profiles": [{"id": p.get("id"), "name": p.get("name")} for p in profiles],
        }
    raise AuthError("该通行证没有可用角色")


def select_profile(server_id: str, access_token: str, client_token: str,
                   profile: dict, username: str = "") -> dict:
    """把令牌绑定到选中的角色（/authserver/refresh + selectedProfile）。"""
    sid = normalize_server_id(server_id)
    payload = {
        "accessToken": access_token,
        "requestUser": True,
        "selectedProfile": {"id": profile.get("id"), "name": profile.get("name")},
    }
    if client_token:
        payload["clientToken"] = client_token
    data = _post(sid, "/authserver/refresh", payload)
    if not data.get("accessToken"):
        data = dict(data)
        data["accessToken"] = access_token
        data.setdefault("clientToken", client_token)
    prof = data.get("selectedProfile") or profile
    return _build_account(sid, username, data, prof)


def refresh(account: dict) -> dict:
    sid = normalize_server_id(account.get("server_id") or account.get("api") or "")
    token = account.get("access_token")
    client = account.get("client_token") or ""
    if not token:
        raise AuthError("统一通行证令牌缺失，请重新登录")
    url = api_url(sid) + "/authserver/refresh"
    try:
        resp = requests.post(url, json={
            "accessToken": token,
            "clientToken": client,
            "requestUser": True,
        }, timeout=20)
    except requests.RequestException as exc:
        raise AuthError(f"统一通行证刷新失败: {exc}") from exc
    if resp.status_code >= 400:
        raise AuthError("统一通行证令牌已过期，请重新登录")
    data = resp.json() if resp.content else {}
    profile = data.get("selectedProfile") or {}
    account = dict(account)
    account["access_token"] = data.get("accessToken") or token
    if profile.get("name"):
        account["name"] = profile.get("name")
    if profile.get("id"):
        account["uuid"] = utils.dashed_uuid(profile.get("id"))
    account["expires_at"] = time.time() + 7 * 24 * 3600
    return account
