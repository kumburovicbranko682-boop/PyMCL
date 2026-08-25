# -*- coding: utf-8 -*-
"""离线账号本地皮肤（对标 HMCL）。

原理与 HMCL 相同：本机起一个只监听 127.0.0.1 的迷你 Yggdrasil 服务，
提供带 RSA 签名的 textures 属性；启动游戏时用 authlib-injector 指向它，
游戏就会加载玩家自选的本地皮肤 PNG。不联网、不依赖任何第三方皮肤站。
"""
from __future__ import annotations

import base64
import hashlib
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import APP_VERSION, utils

KEY_FILE = utils.ROOT / "offline_skin_key.pem"
SKIN_DIR = utils.ROOT / "offline_skins"


class OfflineSkinError(Exception):
    pass


def _crypto():
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding, rsa
        return hashes, serialization, padding, rsa
    except ImportError as exc:  # pragma: no cover - requirements 已带 cryptography
        raise OfflineSkinError(
            "离线皮肤需要 cryptography 库，请运行: pip install cryptography") from exc


def load_or_create_key():
    """本机专用 RSA 密钥，首次生成后落盘复用（游戏校验 textures 签名用）。"""
    hashes, serialization, padding, rsa = _crypto()
    if KEY_FILE.is_file():
        try:
            return serialization.load_pem_private_key(
                KEY_FILE.read_bytes(), password=None)
        except Exception:
            utils.log.warning("离线皮肤密钥损坏，重新生成: %s", KEY_FILE)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    utils.ensure_dir(KEY_FILE.parent)
    KEY_FILE.write_bytes(key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()))
    return key


def public_key_pem(key) -> str:
    _hashes, serialization, _padding, _rsa = _crypto()
    return key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo).decode("ascii")


def sign_property(key, value: str) -> str:
    """Yggdrasil 规范：对 base64 文本本身做 SHA1withRSA，再 base64。"""
    hashes, _serialization, padding, _rsa = _crypto()
    sig = key.sign(value.encode("ascii"), padding.PKCS1v15(), hashes.SHA1())
    return base64.b64encode(sig).decode("ascii")


def store_skin(src_path) -> Path:
    """把用户选的皮肤 PNG 复制进启动器目录（按内容哈希命名，天然去重）。"""
    from .skin import read_skin_file
    data = read_skin_file(src_path)
    utils.ensure_dir(SKIN_DIR)
    dest = SKIN_DIR / f"{hashlib.sha256(data).hexdigest()[:24]}.png"
    if not dest.is_file():
        dest.write_bytes(data)
    return dest


class OfflineSkinServer:
    """127.0.0.1 上的迷你 Yggdrasil：元数据 / profile / hasJoined / 皮肤文件。"""

    def __init__(self):
        self.key = load_or_create_key()
        self._by_uuid = {}      # nodash uuid -> profile dict
        self._by_name = {}      # lower name  -> profile dict
        self._textures = {}     # sha256 -> bytes
        self._httpd = None
        self._thread = None

    # ------------------------------------------------------------ 注册
    def register(self, name: str, uuid: str, skin_file, model: str = "classic"):
        nodash = (uuid or "").replace("-", "").lower()
        if not name or not nodash:
            raise OfflineSkinError("离线皮肤需要角色名和 UUID")
        p = Path(str(skin_file or "")).expanduser()
        if not p.is_file():
            raise OfflineSkinError(f"皮肤文件不存在: {p}")
        data = p.read_bytes()
        tex_hash = hashlib.sha256(data).hexdigest()
        self._textures[tex_hash] = data
        prof = {"name": name, "uuid": nodash, "skin_hash": tex_hash,
                "model": "slim" if str(model or "").lower() == "slim" else "classic"}
        self._by_uuid[nodash] = prof
        self._by_name[name.lower()] = prof

    # ------------------------------------------------------------ 运行
    def start(self):
        if self._httpd:
            return self
        server = self

        class _Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, code, body=b"", ctype="application/json; charset=utf-8"):
                self.send_response(code)
                if body:
                    self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if body:
                    self.wfile.write(body)

            def _json(self, obj, code=200):
                self._send(code, json.dumps(obj).encode("utf-8"))

            def do_GET(self):
                parsed = urlparse(self.path)
                path = parsed.path.rstrip("/") or "/"
                if path == "/":
                    return self._json(server.metadata())
                if path.startswith("/sessionserver/session/minecraft/profile/"):
                    pid = path.rsplit("/", 1)[-1].replace("-", "").lower()
                    prof = server._by_uuid.get(pid)
                    if not prof:
                        return self._send(204)
                    return self._json(server.profile_response(prof))
                if path == "/sessionserver/session/minecraft/hasJoined":
                    q = parse_qs(parsed.query)
                    username = (q.get("username") or [""])[0]
                    prof = server._by_name.get(username.lower())
                    if not prof:
                        return self._send(204)
                    return self._json(server.profile_response(prof))
                if path.startswith("/textures/"):
                    data = server._textures.get(path.rsplit("/", 1)[-1])
                    if not data:
                        return self._send(404)
                    return self._send(200, data, ctype="image/png")
                return self._json({"error": "Not Found"}, 404)

            def do_POST(self):
                parsed = urlparse(self.path)
                path = parsed.path.rstrip("/")
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                if path == "/api/profiles/minecraft":
                    try:
                        names = json.loads(raw or b"[]")
                    except ValueError:
                        names = []
                    out = []
                    for n in names if isinstance(names, list) else []:
                        prof = server._by_name.get(str(n).lower())
                        if prof:
                            out.append({"id": prof["uuid"], "name": prof["name"]})
                    return self._json(out)
                if path == "/sessionserver/session/minecraft/join":
                    return self._send(204)
                return self._json({"error": "Not Found"}, 404)

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._httpd.daemon_threads = True
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, name="offline-skin-server", daemon=True)
        self._thread.start()
        return self

    def stop(self):
        httpd, self._httpd = self._httpd, None
        if httpd:
            try:
                httpd.shutdown()
                httpd.server_close()
            except Exception:
                pass
        self._thread = None

    @property
    def port(self) -> int:
        if not self._httpd:
            raise OfflineSkinError("离线皮肤服务未启动")
        return self._httpd.server_address[1]

    def api_root(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    # ------------------------------------------------------------ 响应体
    def metadata(self) -> dict:
        return {
            "meta": {
                "serverName": "PyMCL",
                "implementationName": "pymcl-offline-skin",
                "implementationVersion": APP_VERSION,
                "feature.non_email_login": True,
            },
            "skinDomains": ["127.0.0.1", "localhost"],
            "signaturePublickey": public_key_pem(self.key),
        }

    def textures_value(self, prof: dict) -> str:
        textures = {"SKIN": {"url": f"{self.api_root()}/textures/{prof['skin_hash']}"}}
        if prof.get("model") == "slim":
            textures["SKIN"]["metadata"] = {"model": "slim"}
        payload = {
            "timestamp": int(time.time() * 1000),
            "profileId": prof["uuid"],
            "profileName": prof["name"],
            "textures": textures,
        }
        return base64.b64encode(
            json.dumps(payload).encode("utf-8")).decode("ascii")

    def profile_response(self, prof: dict) -> dict:
        value = self.textures_value(prof)
        return {
            "id": prof["uuid"],
            "name": prof["name"],
            "properties": [{
                "name": "textures",
                "value": value,
                "signature": sign_property(self.key, value),
            }],
        }


def serve_for_account(name: str, uuid: str, skin_file,
                      model: str = "classic") -> OfflineSkinServer:
    """为一次启动起服务：注册该账号并返回已启动的服务实例。"""
    srv = OfflineSkinServer()
    srv.register(name, uuid, skin_file, model=model)
    return srv.start()
