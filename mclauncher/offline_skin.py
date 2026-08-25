# -*- coding: utf-8 -*-
"""离线账号自定义皮肤（对标 HMCL 的「离线皮肤」路径）。

原理与 HMCL 相同：启动离线账号时在 127.0.0.1 起一个只读的 Yggdrasil
纹理服务，把 authlib-injector 指过去，游戏就能加载本地 PNG 皮肤：

- GET  /                                          API 元数据（含签名公钥）
- GET  /sessionserver/session/minecraft/profile/{uuid}
- GET  /textures/{sha256}
- POST /sessionserver/session/minecraft/join      局域网联机握手
- GET  /sessionserver/session/minecraft/hasJoined
- POST /api/profiles/minecraft                    按名字查角色

材质属性按官方规则签名（SHA1withRSA，见 rsa_lite），密钥首次生成后
持久化在 offline_skins/ 下复用。服务只在游戏运行期间存活。
"""
from __future__ import annotations

import base64
import hashlib
import http.server
import json
import shutil
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import rsa_lite, utils
from .skin_ops import SkinError, load_skin_file, normalize_variant

MODELS = ("classic", "slim")


def skins_dir() -> Path:
    return utils.ROOT / "offline_skins"


def _signing_key() -> dict:
    return rsa_lite.load_or_create(skins_dir() / "signing_key.json")


# ---------------------------------------------------------------- 账号字段

def import_skin(path: str, variant: str = "classic") -> dict:
    """校验并把皮肤 PNG 收进 offline_skins/（按内容寻址），返回账号字段。"""
    data = load_skin_file(path, strict_mojang=False)
    digest = hashlib.sha256(data).hexdigest()
    dest = skins_dir() / f"{digest}.png"
    if not dest.is_file():
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".tmp")
        tmp.write_bytes(data)
        tmp.replace(dest)
    return {"skin_file": str(dest), "skin_model": normalize_variant(variant)}


def apply_to_account(account: dict, path: str, variant: str = "classic") -> str:
    """把本地皮肤写进离线账号记录（调用方负责 accounts.save()）。"""
    if (account or {}).get("type") != "offline":
        raise SkinError("只有离线账号支持本地皮肤文件")
    fields = import_skin(path, variant)
    account.update(fields)
    model = "纤细 (Alex)" if fields["skin_model"] == "slim" else "经典 (Steve)"
    return (f"离线皮肤已设置（{model} 模型）。"
            "皮肤由 PyMCL 启动时在本机提供，仅本机可见")


def clear_account(account: dict) -> str:
    account.pop("skin_file", None)
    account.pop("skin_model", None)
    return "已恢复默认皮肤（按离线 UUID 决定 Steve / Alex）"


def has_custom_skin(account: dict | None) -> bool:
    acc = account or {}
    return acc.get("type") == "offline" and bool(acc.get("skin_file"))


def launch_profile(account: dict) -> dict | None:
    """启动用的角色描述；皮肤文件缺失或损坏时返回 None（回退默认皮肤）。"""
    if not has_custom_skin(account):
        return None
    try:
        data = load_skin_file(account.get("skin_file"), strict_mojang=False)
    except SkinError:
        return None
    name = account.get("name") or "Player"
    uuid = utils.dashed_uuid(account.get("uuid") or "") or utils.offline_uuid(name)
    return {
        "name": name,
        "uuid": uuid.replace("-", "").lower(),
        "skin": data,
        "model": normalize_variant(account.get("skin_model") or "classic"),
    }


# ---------------------------------------------------------------- 本地服务

class OfflineSkinServer:
    """只读 Yggdrasil 纹理服务，绑定 127.0.0.1 随机端口。"""

    def __init__(self, profiles: list[dict], key: dict | None = None):
        self._key = key or _signing_key()
        self._profiles = {}   # uuid(nodash) -> {name, uuid, model, hash}
        self._textures = {}   # sha256 -> png bytes
        self._joins = {}      # serverId -> uuid(nodash)
        self._lock = threading.Lock()
        for p in profiles or []:
            digest = hashlib.sha256(p["skin"]).hexdigest()
            self._textures[digest] = p["skin"]
            self._profiles[p["uuid"]] = {
                "name": p["name"], "uuid": p["uuid"],
                "model": p.get("model") or "classic", "hash": digest,
            }
        self._server = None
        self._thread = None
        self.port = 0

    # ---- 生命周期

    def start(self) -> int:
        self._server = http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0), self._make_handler())
        self.port = self._server.server_port
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True,
            name="pymcl-offline-skin")
        self._thread.start()
        return self.port

    def stop(self):
        server, self._server = self._server, None
        if server is not None:
            try:
                server.shutdown()
                server.server_close()
            except OSError:
                pass

    @property
    def api_root(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    # ---- 响应体

    def _metadata(self) -> dict:
        return {
            "meta": {
                "serverName": "PyMCL 离线皮肤",
                "implementationName": "pymcl-offline-skin",
                "implementationVersion": "1.0",
            },
            "skinDomains": ["127.0.0.1", "localhost"],
            "signaturePublickey": rsa_lite.public_pem(self._key),
        }

    def _texture_value(self, prof: dict) -> str:
        skin: dict = {"url": f"{self.api_root}/textures/{prof['hash']}"}
        if prof["model"] == "slim":
            skin["metadata"] = {"model": "slim"}
        payload = {
            "timestamp": int(time.time() * 1000),
            "profileId": prof["uuid"],
            "profileName": prof["name"],
            "textures": {"SKIN": skin},
        }
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        return base64.b64encode(raw).decode("ascii")

    def _profile_response(self, prof: dict, signed: bool) -> dict:
        value = self._texture_value(prof)
        prop = {"name": "textures", "value": value}
        if signed:
            sig = rsa_lite.sign_sha1(value.encode("ascii"), self._key)
            prop["signature"] = base64.b64encode(sig).decode("ascii")
        return {"id": prof["uuid"], "name": prof["name"], "properties": [prop]}

    def _find_by_name(self, name: str) -> dict | None:
        want = (name or "").lower()
        for prof in self._profiles.values():
            if prof["name"].lower() == want:
                return prof
        return None

    # ---- HTTP

    def _make_handler(self):
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_a):
                pass

            def _json(self, obj, code=200):
                body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _empty(self, code=204):
                self.send_response(code)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_GET(self):
                url = urlparse(self.path)
                parts = [p for p in url.path.split("/") if p]
                if not parts:
                    return self._json(outer._metadata())
                if parts[:3] == ["sessionserver", "session", "minecraft"]:
                    if len(parts) == 5 and parts[3] == "profile":
                        uuid = parts[4].replace("-", "").lower()
                        prof = outer._profiles.get(uuid)
                        if not prof:
                            return self._empty(204)
                        qs = parse_qs(url.query)
                        unsigned = (qs.get("unsigned", ["true"])[0].lower()
                                    != "false")
                        return self._json(
                            outer._profile_response(prof, signed=not unsigned))
                    if len(parts) == 4 and parts[3] == "hasJoined":
                        qs = parse_qs(url.query)
                        username = (qs.get("username") or [""])[0]
                        prof = outer._find_by_name(username)
                        if not prof:
                            return self._empty(204)
                        return self._json(
                            outer._profile_response(prof, signed=True))
                if len(parts) == 2 and parts[0] == "textures":
                    data = outer._textures.get(parts[1])
                    if data is None:
                        return self._empty(404)
                    self.send_response(200)
                    self.send_header("Content-Type", "image/png")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                return self._empty(404)

            def do_POST(self):
                url = urlparse(self.path)
                parts = [p for p in url.path.split("/") if p]
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                try:
                    payload = json.loads(raw.decode("utf-8")) if raw else None
                except (ValueError, UnicodeDecodeError):
                    payload = None
                if parts == ["sessionserver", "session", "minecraft", "join"]:
                    if isinstance(payload, dict) and payload.get("serverId"):
                        selected = str(payload.get("selectedProfile") or "")
                        with outer._lock:
                            outer._joins[str(payload["serverId"])] = \
                                selected.replace("-", "").lower()
                    return self._empty(204)
                if parts == ["api", "profiles", "minecraft"]:
                    names = payload if isinstance(payload, list) else []
                    found = []
                    for n in names:
                        prof = outer._find_by_name(str(n))
                        if prof:
                            found.append({"id": prof["uuid"], "name": prof["name"]})
                    return self._json(found)
                return self._empty(404)

        return Handler


def serve_for_account(account: dict) -> OfflineSkinServer | None:
    """离线账号带自定义皮肤时启动本地纹理服务；否则返回 None。"""
    prof = launch_profile(account)
    if prof is None:
        return None
    server = OfflineSkinServer([prof])
    server.start()
    return server
