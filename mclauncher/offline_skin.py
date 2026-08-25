# -*- coding: utf-8 -*-
"""离线账户皮肤（对标 HMCL）：本地 Yggdrasil 皮肤服务 + authlib-injector 注入。

离线账户在游戏内默认只能是 Steve/Alex。HMCL 的做法是启动器内置一个
Yggdrasil 兼容的本地 HTTP 服务，把皮肤 / 披风材质签名后发给游戏，再用
authlib-injector 把游戏的会话服务指到 127.0.0.1，游戏内就能看到自选皮肤。

本模块实现同样的机制：
- 纯 Python RSA（SHA1withRSA / PKCS#1 v1.5）：给材质属性签名，公钥通过
  API 元数据下发给 authlib-injector 校验。密钥首次生成后持久化。
- SkinServer：ThreadingHTTPServer，实现 authlib-injector 需要的端点
  （API 根元数据、按名查档案、按 UUID 查档案、材质文件、join/hasJoined）。
- fetch_premium_skin：按正版玩家名从 Mojang 公开接口抓取皮肤 / 披风。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import os
import secrets
import shutil
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

from . import APP_NAME, APP_VERSION
from . import utils
from .skin import SkinError, validate_skin_png

MOJANG_NAME_URL = "https://api.mojang.com/users/profiles/minecraft/{name}"
MOJANG_PROFILE_URL = "https://sessionserver.mojang.com/session/minecraft/profile/{uuid}"
MAX_CAPE_BYTES = 256 * 1024
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

# SHA1 DigestInfo 前缀（RFC 8017 EMSA-PKCS1-v1_5）
_SHA1_PREFIX = bytes.fromhex("3021300906052b0e03021a05000414")
# rsaEncryption OID: 1.2.840.113549.1.1.1
_RSA_OID = bytes.fromhex("06092a864886f70d010101")

_SMALL_PRIMES = (
    2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47, 53, 59, 61, 67,
    71, 73, 79, 83, 89, 97, 101, 103, 107, 109, 113, 127, 131, 137, 139, 149,
)


# ---------------------------------------------------------------------------
# 纯 Python RSA（仅用于本地皮肤签名，不承载任何机密通信）
# ---------------------------------------------------------------------------

def _is_probable_prime(n: int, rounds: int = 40) -> bool:
    if n < 2:
        return False
    for p in _SMALL_PRIMES:
        if n == p:
            return True
        if n % p == 0:
            return False
    d = n - 1
    r = 0
    while d % 2 == 0:
        d //= 2
        r += 1
    for _ in range(rounds):
        a = secrets.randbelow(n - 3) + 2
        x = pow(a, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(r - 1):
            x = pow(x, 2, n)
            if x == n - 1:
                break
        else:
            return False
    return True


def _gen_prime(bits: int) -> int:
    while True:
        c = secrets.randbits(bits) | (1 << (bits - 1)) | 1
        if _is_probable_prime(c):
            return c


def generate_keypair(bits: int = 2048) -> dict:
    """生成 RSA 密钥对 {n, e, d}。bits 为模数位数。"""
    e = 65537
    while True:
        p = _gen_prime(bits // 2)
        q = _gen_prime(bits - bits // 2)
        if p == q:
            continue
        n = p * q
        if n.bit_length() < bits:
            continue
        phi = (p - 1) * (q - 1)
        if math.gcd(e, phi) != 1:
            continue
        return {"n": n, "e": e, "d": pow(e, -1, phi)}


def _emsa_pkcs1_v15(data: bytes, k: int) -> bytes:
    t = _SHA1_PREFIX + hashlib.sha1(data).digest()
    if k < len(t) + 11:
        raise SkinError("RSA 密钥太短，无法签名")
    return b"\x00\x01" + b"\xff" * (k - len(t) - 3) + b"\x00" + t


def sign_sha1(data: bytes, key: dict) -> bytes:
    """SHA1withRSA 签名（authlib 校验材质属性用的算法）。"""
    k = (key["n"].bit_length() + 7) // 8
    m = int.from_bytes(_emsa_pkcs1_v15(data, k), "big")
    return pow(m, key["d"], key["n"]).to_bytes(k, "big")


def verify_sha1(data: bytes, signature: bytes, key: dict) -> bool:
    k = (key["n"].bit_length() + 7) // 8
    if len(signature) != k:
        return False
    em = pow(int.from_bytes(signature, "big"), key["e"], key["n"]).to_bytes(k, "big")
    try:
        expected = _emsa_pkcs1_v15(data, k)
    except SkinError:
        return False
    return hmac.compare_digest(em, expected)


def _der(tag: int, content: bytes) -> bytes:
    n = len(content)
    if n < 0x80:
        return bytes([tag, n]) + content
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([tag, 0x80 | len(raw)]) + raw + content


def _der_uint(x: int) -> bytes:
    raw = x.to_bytes((x.bit_length() + 7) // 8 or 1, "big")
    if raw[0] & 0x80:
        raw = b"\x00" + raw
    return _der(0x02, raw)


def public_key_pem(key: dict) -> str:
    """公钥导出为 SubjectPublicKeyInfo PEM，authlib-injector 用它校验签名。"""
    rsa = _der(0x30, _der_uint(key["n"]) + _der_uint(key["e"]))
    algo = _der(0x30, _RSA_OID + b"\x05\x00")
    spki = _der(0x30, algo + _der(0x03, b"\x00" + rsa))
    b64 = base64.b64encode(spki).decode("ascii")
    lines = [b64[i:i + 64] for i in range(0, len(b64), 64)]
    return "-----BEGIN PUBLIC KEY-----\n" + "\n".join(lines) + "\n-----END PUBLIC KEY-----"


def key_path() -> Path:
    return utils.ROOT / "offline_skin_key.json"


_KEY: dict | None = None
_KEY_LOCK = threading.Lock()


def load_or_create_key(path: Path | str | None = None, bits: int = 2048) -> dict:
    """读取或首次生成签名密钥。默认存到启动器数据目录并缓存。"""
    global _KEY
    p = Path(path) if path else key_path()
    if path is None:
        with _KEY_LOCK:
            if _KEY is not None:
                return _KEY
    data = utils.read_json(p, None)
    key = None
    if isinstance(data, dict) and data.get("n") and data.get("d"):
        try:
            key = {"n": int(data["n"], 16), "e": int(data.get("e") or "10001", 16),
                   "d": int(data["d"], 16)}
        except (TypeError, ValueError):
            key = None
    if key is None:
        key = generate_keypair(bits)
        utils.write_json(p, {"n": format(key["n"], "x"), "e": format(key["e"], "x"),
                             "d": format(key["d"], "x")})
        try:
            os.chmod(p, 0o600)
        except OSError:
            pass
    if path is None:
        with _KEY_LOCK:
            _KEY = key
    return key


# ---------------------------------------------------------------------------
# 皮肤 / 披风文件存储
# ---------------------------------------------------------------------------

def skins_dir() -> Path:
    return utils.ROOT / "skins" / "offline"


def validate_cape_png(path) -> tuple[int, int]:
    """披风尺寸历史上有多种（64x32 / 22x17 等），只校验 PNG 头和大小上限。"""
    p = Path(path)
    if not p.is_file():
        raise SkinError(f"文件不存在: {p}")
    if p.stat().st_size > MAX_CAPE_BYTES:
        raise SkinError("披风文件过大（超过 256 KB）。")
    with open(p, "rb") as f:
        head = f.read(33)
    if len(head) < 33 or not head.startswith(_PNG_MAGIC) or head[12:16] != b"IHDR":
        raise SkinError("不是有效的 PNG 图片，披风必须是 PNG 格式。")
    width, height = struct.unpack(">II", head[16:24])
    return int(width), int(height)


def store_skin_file(src, uuid: str, kind: str = "skin", dest_dir: Path | str | None = None) -> str:
    """校验后把皮肤 / 披风 PNG 复制到启动器皮肤目录，返回目标路径。"""
    src = Path(src)
    if kind == "cape":
        validate_cape_png(src)
    else:
        validate_skin_png(src)
    uid = (uuid or "").replace("-", "").lower() or "player"
    folder = Path(dest_dir) if dest_dir else skins_dir()
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / f"{uid}-{kind}.png"
    if src.resolve() != dest.resolve():
        shutil.copyfile(src, dest)
    return str(dest)


def fetch_premium_skin(player_name: str, dest_uuid: str, session=None,
                       dest_dir: Path | str | None = None) -> dict:
    """按正版玩家名抓取皮肤 / 披风，保存到皮肤目录。

    返回 {"skin_file", "skin_model", "cape_file"}；玩家没披风时 cape_file 为空。
    """
    name = (player_name or "").strip()
    if not name:
        raise SkinError("请输入正版玩家名。")
    s = session or _session()
    resp = s.get(MOJANG_NAME_URL.format(name=quote(name)), timeout=15)
    if resp.status_code in (204, 404):
        raise SkinError(f"找不到正版玩家: {name}")
    if resp.status_code != 200:
        raise SkinError(f"查询玩家失败: HTTP {resp.status_code}")
    uid = ((resp.json() or {}).get("id") or "").strip()
    if not uid:
        raise SkinError(f"找不到正版玩家: {name}")
    resp = s.get(MOJANG_PROFILE_URL.format(uuid=uid), timeout=15)
    if resp.status_code != 200:
        raise SkinError(f"获取玩家档案失败: HTTP {resp.status_code}")
    profile = resp.json() or {}
    payload = {}
    for prop in profile.get("properties") or []:
        if prop.get("name") == "textures" and prop.get("value"):
            try:
                payload = json.loads(base64.b64decode(prop["value"]))
            except (ValueError, TypeError):
                payload = {}
            break
    textures = payload.get("textures") or {}
    skin_info = textures.get("SKIN") or {}
    if not skin_info.get("url"):
        raise SkinError(f"玩家 {name} 没有自定义皮肤。")
    model = "slim" if (skin_info.get("metadata") or {}).get("model") == "slim" else "default"

    folder = Path(dest_dir) if dest_dir else skins_dir()
    folder.mkdir(parents=True, exist_ok=True)
    dest_uid = (dest_uuid or "").replace("-", "").lower() or "player"

    def grab(url: str, kind: str) -> str:
        r = s.get(url, timeout=20)
        if r.status_code != 200 or not r.content:
            raise SkinError(f"下载{ '披风' if kind == 'cape' else '皮肤' }失败: HTTP {r.status_code}")
        tmp = folder / f"{dest_uid}-{kind}.png.part"
        tmp.write_bytes(r.content)
        try:
            if kind == "cape":
                validate_cape_png(tmp)
            else:
                validate_skin_png(tmp)
            dest = folder / f"{dest_uid}-{kind}.png"
            os.replace(tmp, dest)
            return str(dest)
        except SkinError:
            tmp.unlink(missing_ok=True)
            raise

    out = {"skin_file": grab(skin_info["url"], "skin"), "skin_model": model, "cape_file": ""}
    cape_info = textures.get("CAPE") or {}
    if cape_info.get("url"):
        try:
            out["cape_file"] = grab(cape_info["url"], "cape")
        except SkinError:
            out["cape_file"] = ""
    return out


def _session():
    import requests
    s = requests.Session()
    try:
        from .net import apply_direct_to_session
        apply_direct_to_session(s)
    except Exception:
        pass
    s.headers["User-Agent"] = f"{APP_NAME}/{APP_VERSION}"
    return s


# ---------------------------------------------------------------------------
# 本地 Yggdrasil 皮肤服务
# ---------------------------------------------------------------------------

def _nodash(uuid: str) -> str:
    return (uuid or "").replace("-", "").lower()


class _Handler(BaseHTTPRequestHandler):
    server_version = "PyMCL-OfflineSkin"
    protocol_version = "HTTP/1.1"

    # 静默：不要往 stderr 刷访问日志
    def log_message(self, fmt, *args):  # noqa: A003
        pass

    @property
    def skin(self) -> "SkinServer":
        return self.server.skin_server  # type: ignore[attr-defined]

    def _json(self, obj, code: int = 200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _empty(self, code: int = 204):
        self.send_response(code)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _not_found(self):
        self._json({"error": "Not Found", "errorMessage": "Not Found"}, 404)

    def do_GET(self):  # noqa: N802
        url = urlsplit(self.path)
        path = url.path.rstrip("/") or "/"
        if path == "/":
            self._json(self.skin.meta_json())
            return
        if path.startswith("/textures/"):
            data = self.skin.texture(path.rsplit("/", 1)[-1])
            if data is None:
                self._not_found()
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if path.startswith("/sessionserver/session/minecraft/profile/"):
            uid = _nodash(path.rsplit("/", 1)[-1])
            query = parse_qs(url.query)
            unsigned = (query.get("unsigned") or ["true"])[0].lower() != "false"
            prof = self.skin.profile_json(uid, signed=not unsigned)
            if prof is None:
                self._empty(204)
                return
            self._json(prof)
            return
        if path == "/sessionserver/session/minecraft/hasJoined":
            query = parse_qs(url.query)
            name = (query.get("username") or [""])[0]
            prof = self.skin.profile_json_by_name(name, signed=True)
            if prof is None:
                self._empty(204)
                return
            self._json(prof)
            return
        self._not_found()

    def do_POST(self):  # noqa: N802
        url = urlsplit(self.path)
        path = url.path.rstrip("/")
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            length = 0
        raw = self.rfile.read(length) if length > 0 else b""
        if path == "/api/profiles/minecraft":
            try:
                names = json.loads(raw or b"[]")
            except ValueError:
                names = []
            if not isinstance(names, list):
                names = []
            rows = []
            for name in names:
                hit = self.skin.lookup_name(str(name))
                if hit:
                    rows.append({"id": hit["uuid"], "name": hit["name"]})
            self._json(rows)
            return
        if path == "/sessionserver/session/minecraft/join":
            self._empty(204)
            return
        self._not_found()


class SkinServer:
    """127.0.0.1 上的最小 Yggdrasil 服务，只服务本机启动的离线账户皮肤。"""

    def __init__(self, key: dict | None = None):
        self._key = key or load_or_create_key()
        self._pem = public_key_pem(self._key)
        self._lock = threading.Lock()
        self._profiles: dict[str, dict] = {}
        self._by_name: dict[str, str] = {}
        self._textures: dict[str, bytes] = {}
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._httpd.skin_server = self  # type: ignore[attr-defined]
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, daemon=True, name="pymcl-offline-skin")
        self._thread.start()

    @property
    def port(self) -> int:
        return int(self._httpd.server_address[1])

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def alive(self) -> bool:
        return self._thread.is_alive()

    def stop(self):
        try:
            self._httpd.shutdown()
            self._httpd.server_close()
        except OSError:
            pass

    # ------------------------------------------------------------ 注册与查询

    def register(self, name: str, uuid: str, skin_path: str = "",
                 model: str = "default", cape_path: str = ""):
        """登记一个离线角色。材质文件读进内存，按 sha256 生成 URL。"""
        uid = _nodash(uuid)
        if not name or not uid:
            raise SkinError("离线角色缺少名称或 UUID。")
        prof = {
            "uuid": uid,
            "name": str(name),
            "model": "slim" if str(model).lower() == "slim" else "default",
            "skin_hash": "",
            "cape_hash": "",
        }
        with self._lock:
            for kind, src in (("skin", skin_path), ("cape", cape_path)):
                if not src:
                    continue
                p = Path(src)
                if not p.is_file():
                    raise SkinError(f"材质文件不存在: {p}")
                data = p.read_bytes()
                digest = hashlib.sha256(data).hexdigest()
                self._textures[digest] = data
                prof[f"{kind}_hash"] = digest
            self._profiles[uid] = prof
            self._by_name[prof["name"].lower()] = uid

    def lookup_name(self, name: str) -> dict | None:
        with self._lock:
            uid = self._by_name.get((name or "").lower())
            return dict(self._profiles[uid]) if uid and uid in self._profiles else None

    def texture(self, digest: str) -> bytes | None:
        with self._lock:
            return self._textures.get((digest or "").lower())

    # ------------------------------------------------------------ 响应构造

    def meta_json(self) -> dict:
        return {
            "meta": {
                "serverName": f"{APP_NAME} 离线皮肤",
                "implementationName": "pymcl-offline-skin",
                "implementationVersion": APP_VERSION,
                "feature.non_email_login": True,
            },
            "skinDomains": ["127.0.0.1", "localhost"],
            "signaturePublickey": self._pem,
        }

    def _textures_payload(self, prof: dict) -> dict:
        payload = {
            "timestamp": int(time.time() * 1000),
            "profileId": prof["uuid"],
            "profileName": prof["name"],
            "textures": {},
        }
        if prof.get("skin_hash"):
            entry = {"url": f"{self.url}/textures/{prof['skin_hash']}"}
            if prof.get("model") == "slim":
                entry["metadata"] = {"model": "slim"}
            payload["textures"]["SKIN"] = entry
        if prof.get("cape_hash"):
            payload["textures"]["CAPE"] = {"url": f"{self.url}/textures/{prof['cape_hash']}"}
        return payload

    def profile_json(self, uuid: str, signed: bool = True) -> dict | None:
        with self._lock:
            prof = self._profiles.get(_nodash(uuid))
            if prof is None:
                return None
            prof = dict(prof)
        value = base64.b64encode(
            json.dumps(self._textures_payload(prof), ensure_ascii=False).encode("utf-8")
        ).decode("ascii")
        prop = {"name": "textures", "value": value}
        if signed:
            prop["signature"] = base64.b64encode(
                sign_sha1(value.encode("ascii"), self._key)).decode("ascii")
        return {"id": prof["uuid"], "name": prof["name"], "properties": [prop]}

    def profile_json_by_name(self, name: str, signed: bool = True) -> dict | None:
        hit = self.lookup_name(name)
        if hit is None:
            return None
        return self.profile_json(hit["uuid"], signed=signed)


_SERVER: SkinServer | None = None
_SERVER_LOCK = threading.Lock()


def ensure_server() -> SkinServer:
    """进程内单例皮肤服务；随启动器进程存活（daemon 线程）。"""
    global _SERVER
    with _SERVER_LOCK:
        if _SERVER is None or not _SERVER.alive():
            _SERVER = SkinServer()
        return _SERVER


def has_custom_skin(account: dict | None) -> bool:
    acc = account or {}
    if (acc.get("type") or "offline") != "offline":
        return False
    return bool(acc.get("skin_file") or acc.get("cape_file"))


def prepare_injection(account: dict | None, server: SkinServer | None = None) -> str:
    """为配置了皮肤的离线账户准备本地皮肤服务。

    返回可传给 authlib-injector 的 API 根地址；账户没配皮肤或材质文件
    丢失时返回空字符串（此时按普通离线启动，不注入）。
    """
    acc = account or {}
    if not has_custom_skin(acc):
        return ""
    skin_file = acc.get("skin_file") or ""
    cape_file = acc.get("cape_file") or ""
    if skin_file and not Path(skin_file).is_file():
        utils.log.warning("离线皮肤文件丢失，跳过注入: %s", skin_file)
        skin_file = ""
    if cape_file and not Path(cape_file).is_file():
        utils.log.warning("离线披风文件丢失: %s", cape_file)
        cape_file = ""
    if not skin_file and not cape_file:
        return ""
    srv = server or ensure_server()
    srv.register(
        name=acc.get("name") or "Player",
        uuid=acc.get("uuid") or "",
        skin_path=skin_file,
        model=acc.get("skin_model") or "default",
        cape_path=cape_file,
    )
    return srv.url
