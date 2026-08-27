# -*- coding: utf-8 -*-
"""账号系统：离线模式 + 微软正版登录（设备代码流）。"""
import base64
import hashlib
import os
import stat
import time
import webbrowser

from . import utils

# requests 只在真正联网（微软登录）时才 import；AccountManager 本身只读写
# 本地 JSON，backend 在 GUI 启动路径上就会构造它，不能被 requests 拖慢。

# 微软 OAuth 端点
MS_DEVICE_CODE_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/devicecode"
MS_TOKEN_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"
XBL_AUTH_URL = "https://user.auth.xboxlive.com/user/authenticate"
XSTS_AUTH_URL = "https://xsts.auth.xboxlive.com/xsts/authorize"
MC_LOGIN_URL = "https://api.minecraftservices.com/authentication/login_with_xbox"
MC_ENTITLEMENTS_URL = "https://api.minecraftservices.com/entitlements/mcstore"
MC_PROFILE_URL = "https://api.minecraftservices.com/minecraft/profile"

XSTS_ERRORS = {
    2148916227: "账号已被封禁。",
    2148916233: "该微软账号没有 Xbox Live 账户，请先注册 Xbox。",
    2148916235: "所在地区不支持 Xbox Live。",
    2148916236: "需要成年账户才能进行身份验证。",
    2148916237: "需要成年账户（家长同意缺失）。",
    2148916238: "该账户是儿童账户，需要家长将其添加到家庭组。",
}

ACCOUNTS_FILE = utils.ROOT / "accounts.json"
_TOKEN_KEYS = ("access_token", "refresh_token")
_DPAPI_PREFIX = "dpapi:"
_KEYRING_PREFIX = "keyring:"
_UNAVAILABLE_PREFIX = "unavailable:"
_KEYRING_SERVICE = "PyMCL launcher tokens"
_OWNED_ITEMS = frozenset({
    "game_minecraft", "product_minecraft", "product_minecraft_java",
})


def _dpapi_protect(plain: bytes) -> bytes:
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(plain, len(plain))
    blob_in = DATA_BLOB(len(plain), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = DATA_BLOB()
    if not ctypes.windll.crypt32.CryptProtectData(
            ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)):
        raise OSError("CryptProtectData failed")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def _dpapi_unprotect(blob: bytes) -> bytes:
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(blob, len(blob))
    blob_in = DATA_BLOB(len(blob), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = DATA_BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)):
        raise OSError("CryptUnprotectData failed")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def _keyring_backend():
    """Load keyring lazily so offline/CLI installs still start cleanly."""
    try:
        import keyring  # type: ignore[import-not-found]
        return keyring
    except Exception:
        return None


def _keyring_name(value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return f"token-{digest}"


def _store_in_keyring(value: str) -> str | None:
    backend = _keyring_backend()
    if backend is None:
        return None
    name = _keyring_name(value)
    try:
        backend.set_password(_KEYRING_SERVICE, name, value)
    except Exception as exc:
        utils.log.warning("系统凭据库不可用，令牌不会写入磁盘: %s", exc)
        return None
    return _KEYRING_PREFIX + name


def _read_from_keyring(reference: str) -> str:
    backend = _keyring_backend()
    if backend is None:
        utils.log.warning("系统凭据库不可用，需要重新登录账号。")
        return ""
    name = reference[len(_KEYRING_PREFIX):]
    try:
        return backend.get_password(_KEYRING_SERVICE, name) or ""
    except Exception as exc:
        utils.log.warning("读取系统凭据库失败，需要重新登录账号: %s", exc)
        return ""


def _delete_keyring_reference(value: str) -> None:
    if not str(value or "").startswith(_KEYRING_PREFIX):
        return
    backend = _keyring_backend()
    if backend is None:
        return
    try:
        backend.delete_password(_KEYRING_SERVICE, value[len(_KEYRING_PREFIX):])
    except Exception:
        # A missing item and an unavailable backend both leave no local
        # plaintext behind, so deletion is best effort only.
        pass


def seal_secret(value: str) -> str:
    if not value:
        return value
    value = str(value)
    if value.startswith((_DPAPI_PREFIX, _KEYRING_PREFIX, _UNAVAILABLE_PREFIX)):
        return value
    if os.name == "nt":
        try:
            return _DPAPI_PREFIX + base64.b64encode(_dpapi_protect(value.encode("utf-8"))).decode("ascii")
        except OSError as exc:
            # Do not silently fall back to clear-text credentials.
            utils.log.warning("DPAPI 不可用，令牌不会写入磁盘: %s", exc)
            return _UNAVAILABLE_PREFIX
    reference = _store_in_keyring(value)
    # macOS Keychain / Linux Secret Service failure must not turn into an
    # accounts.json with refresh tokens in clear text. Users can log in again
    # when a system credential backend becomes available.
    return reference or _UNAVAILABLE_PREFIX


def open_secret(value: str) -> str:
    if not value:
        return value
    value = str(value)
    if value.startswith(_UNAVAILABLE_PREFIX):
        return ""
    if value.startswith(_KEYRING_PREFIX):
        return _read_from_keyring(value)
    if not value.startswith(_DPAPI_PREFIX):
        # Legacy non-Windows records were plaintext. Keep them in memory so
        # the next save can migrate them to the platform credential store.
        return value
    if os.name != "nt":
        utils.log.warning("当前系统无法解密 Windows DPAPI 令牌，需要重新登录账号。")
        return ""
    try:
        raw = base64.b64decode(value[len(_DPAPI_PREFIX):].encode("ascii"))
        return _dpapi_unprotect(raw).decode("utf-8")
    except (OSError, ValueError):
        utils.log.warning("无法解密账号令牌，需要重新登录账号。")
        return ""


def seal_account(account: dict) -> dict:
    out = dict(account or {})
    for key in _TOKEN_KEYS:
        if out.get(key):
            out[key] = seal_secret(out[key])
    return out


def open_account(account: dict) -> dict:
    out = dict(account or {})
    for key in _TOKEN_KEYS:
        if out.get(key):
            out[key] = open_secret(out[key])
    return out


def _restrict_accounts_file():
    try:
        os.chmod(ACCOUNTS_FILE, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


class AuthError(Exception):
    pass


# ---------------------------------------------------------------- 微软登录

class MicrosoftAuthenticator:
    def __init__(self, client_id="00000000402b5328", timeout=15):
        import requests
        self.client_id = client_id
        self.timeout = timeout
        self.session = requests.Session()
        from .net import apply_direct_to_session
        apply_direct_to_session(self.session)
        self.session.headers["User-Agent"] = "PyMCL/1.0"

    # ---- 第 1 步：获取设备码
    def get_device_code(self):
        resp = self.session.post(
            MS_DEVICE_CODE_URL,
            data={
                "client_id": self.client_id,
                "scope": "XboxLive.signin offline_access",
            },
            timeout=self.timeout,
        )
        if resp.status_code != 200:
            raise AuthError(f"获取设备码失败 (HTTP {resp.status_code}): {resp.text[:200]}")
        data = resp.json()
        return {
            "device_code": data["device_code"],
            "user_code": data["user_code"],
            "verification_uri": data["verification_uri"],
            "expires_in": int(data["expires_in"]),
            "interval": int(data.get("interval", 5)),
        }

    # ---- 第 2 步：轮询令牌
    def poll_token(self, device_code, interval=5, expires_in=900, on_status=None):
        import requests
        deadline = time.time() + expires_in
        while time.time() < deadline:
            try:
                resp = self.session.post(
                    MS_TOKEN_URL,
                    data={
                        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                        "client_id": self.client_id,
                        "device_code": device_code,
                    },
                    timeout=self.timeout,
                )
                try:
                    data = resp.json()
                except ValueError:
                    data = {}
            except requests.RequestException:
                # 网络抖动：稍后重试
                if on_status:
                    on_status("网络错误，重试中…")
                time.sleep(interval)
                continue
            if resp.status_code == 200:
                return {"access_token": data["access_token"], "refresh_token": data.get("refresh_token")}
            err = data.get("error", "")
            if err == "authorization_pending":
                if on_status:
                    on_status("等待授权中…")
            elif err == "slow_down":
                interval += 5
            elif err in ("expired_token", "authorization_declined", "bad_verification_code"):
                raise AuthError("授权已过期或被拒绝，请重试。")
            else:
                raise AuthError(f"轮询令牌失败: {err} {data.get('error_description', '')}")
            time.sleep(interval)
        raise AuthError("授权超时。")

    # ---- 第 3 步：Xbox Live 认证
    def xbl_authenticate(self, ms_token):
        resp = self.session.post(
            XBL_AUTH_URL,
            json={
                "Properties": {
                    "AuthMethod": "RPS",
                    "SiteName": "user.auth.xboxlive.com",
                    "RpsTicket": "d=" + ms_token,
                },
                "RelyingParty": "http://auth.xboxlive.com",
                "TokenType": "JWT",
            },
            timeout=self.timeout,
        )
        if resp.status_code != 200:
            raise AuthError(f"Xbox Live 认证失败 (HTTP {resp.status_code})")
        return resp.json()["Token"]

    # ---- 第 4 步：XSTS 认证
    def xsts_authenticate(self, xbl_token):
        resp = self.session.post(
            XSTS_AUTH_URL,
            json={
                "Properties": {
                    "SandboxId": "RETAIL",
                    "UserTokens": [xbl_token],
                },
                "RelyingParty": "rp://api.minecraftservices.com/",
                "TokenType": "JWT",
            },
            timeout=self.timeout,
        )
        if resp.status_code == 401:
            data = resp.json()
            xerr = data.get("XErr")
            raise AuthError("XSTS 认证失败: " + XSTS_ERRORS.get(xerr, f"未知错误 {xerr}"))
        if resp.status_code != 200:
            raise AuthError(f"XSTS 认证失败 (HTTP {resp.status_code})")
        data = resp.json()
        token = data["Token"]
        uhs = data["DisplayClaims"]["xui"][0]["uhs"]
        return token, uhs

    # ---- 第 5 步：换取 Minecraft 令牌
    def mc_login(self, uhs, xsts_token):
        resp = self.session.post(
            MC_LOGIN_URL,
            json={"identityToken": f"XBL3.0 x={uhs};{xsts_token}"},
            timeout=self.timeout,
        )
        if resp.status_code != 200:
            raise AuthError(f"Minecraft 登录失败 (HTTP {resp.status_code}): {resp.text[:200]}")
        return resp.json()["access_token"]

    # ---- 第 6 步：检查正版资格
    def check_entitlements(self, mc_token):
        resp = self.session.get(
            MC_ENTITLEMENTS_URL,
            headers={"Authorization": f"Bearer {mc_token}"},
            timeout=self.timeout,
        )
        if resp.status_code != 200:
            raise AuthError(f"检查正版资格失败 (HTTP {resp.status_code})")
        items = resp.json().get("items", [])
        names = {item.get("name") for item in items if isinstance(item, dict)}
        if names & _OWNED_ITEMS:
            return True
        if names:
            utils.log.warning("entitlements 未包含已知 Java 项，改由档案接口判定: %s", names)
        return True

    # ---- 第 7 步：获取玩家档案
    def get_profile(self, mc_token):
        resp = self.session.get(
            MC_PROFILE_URL,
            headers={"Authorization": f"Bearer {mc_token}"},
            timeout=self.timeout,
        )
        if resp.status_code == 404:
            raise AuthError("该账号尚未创建 Minecraft 档案（没有游戏角色名）。")
        if resp.status_code != 200:
            raise AuthError(f"获取玩家档案失败 (HTTP {resp.status_code})")
        data = resp.json()
        return {"uuid": utils.dashed_uuid(data["id"]), "name": data["name"]}

    # ---- 完整登录（设备码流）
    def login(self, on_code=None, on_status=None, open_browser=True):
        """
        on_code: 回调 (user_code, verification_uri) 用于展示给用户
        返回账号 dict。
        """
        code = self.get_device_code()
        if open_browser:
            try:
                webbrowser.open(code["verification_uri"])
            except Exception:
                pass
        if on_code:
            on_code(code["user_code"], code["verification_uri"], code["expires_in"])
        tokens = self.poll_token(
            code["device_code"],
            interval=code["interval"],
            expires_in=code["expires_in"],
            on_status=on_status,
        )
        xbl = self.xbl_authenticate(tokens["access_token"])
        xsts, uhs = self.xsts_authenticate(xbl)
        mc_token = self.mc_login(uhs, xsts)
        self.check_entitlements(mc_token)
        profile = self.get_profile(mc_token)
        return {
            "type": "microsoft",
            "name": profile["name"],
            "uuid": profile["uuid"],
            "access_token": mc_token,
            "refresh_token": tokens.get("refresh_token"),
            "xuid": uhs,
            "expires_at": time.time() + 20 * 3600,  # MC 令牌有效期约 24 小时，提前刷新
            "updated_at": time.time(),
        }

    # ---- 刷新令牌
    def refresh(self, account):
        if not account.get("refresh_token"):
            raise AuthError("缺少刷新令牌，需要重新登录。")
        resp = self.session.post(
            MS_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "client_id": self.client_id,
                "refresh_token": account["refresh_token"],
                "scope": "XboxLive.signin offline_access",
            },
            timeout=self.timeout,
        )
        if resp.status_code != 200:
            raise AuthError("刷新令牌失败，需要重新登录。")
        data = resp.json()
        ms_token = data["access_token"]
        refresh_token = data.get("refresh_token") or account["refresh_token"]
        xbl = self.xbl_authenticate(ms_token)
        xsts, uhs = self.xsts_authenticate(xbl)
        mc_token = self.mc_login(uhs, xsts)
        profile = self.get_profile(mc_token)
        account.update({
            "access_token": mc_token,
            "refresh_token": refresh_token,
            "xuid": uhs,
            "expires_at": time.time() + 20 * 3600,
            "updated_at": time.time(),
        })
        return account


# ---------------------------------------------------------------- 账号管理

class AccountManager:
    def __init__(self):
        self.accounts = []
        self.active = None
        self.load()

    def load(self):
        data = utils.read_json(ACCOUNTS_FILE, None) or {}
        self.accounts = [open_account(a) for a in data.get("accounts", []) if isinstance(a, dict)]
        self.active = data.get("active")

    def save(self):
        sealed = [seal_account(a) for a in self.accounts]
        utils.write_json(ACCOUNTS_FILE, {"active": self.active, "accounts": sealed})
        _restrict_accounts_file()

    def add_account(self, account):
        self._remove_stored_secrets(account.get("name"))
        self.accounts = [a for a in self.accounts if a.get("name") != account.get("name")]
        self.accounts.append(account)
        self.active = account["name"]
        self.save()
        return account

    def remove_account(self, name):
        self._remove_stored_secrets(name)
        self.accounts = [a for a in self.accounts if a.get("name") != name]
        if self.active == name:
            self.active = self.accounts[0]["name"] if self.accounts else None
        self.save()

    def _remove_stored_secrets(self, name):
        """Delete keychain items belonging to an account before replacement."""
        if not name:
            return
        data = utils.read_json(ACCOUNTS_FILE, {}) or {}
        for stored in data.get("accounts", []):
            if not isinstance(stored, dict) or stored.get("name") != name:
                continue
            for key in _TOKEN_KEYS:
                _delete_keyring_reference(stored.get(key) or "")

    def get_account(self, name):
        for a in self.accounts:
            if a.get("name") == name:
                return a
        return None

    def get_active(self):
        if self.active:
            acc = self.get_account(self.active)
            if acc:
                return acc
        return None

    def set_active(self, name):
        if name and self.get_account(name):
            self.active = name
            self.save()
        return self.active

    def offline_account(self, username, skin="default"):
        username = username.strip() or "Player"
        skin = (skin or "default").lower()
        if skin == "steve":
            uuid = "8667ba71-b85a-4004-af54-457a9734eed7"
        elif skin == "alex":
            uuid = "ec561538-f3fd-461d-a7c9-7aa354f5bba9"
        else:
            uuid = utils.offline_uuid(username)
        return {"type": "offline", "name": username, "uuid": uuid, "skin": skin}

    def set_offline_uuid(self, name, raw_uuid: str = "") -> str:
        """离线账号自定义 UUID（HMCL 同款）。

        换启动器 / 进带白名单绑定的离线服时，保持旧 UUID 才能保住
        玩家数据与权限。raw_uuid 留空则重置为按用户名推导的标准
        离线 UUID。返回保存后的带连字符 UUID。
        """
        acc = self.get_account(name)
        if not acc or (acc.get("type") or "offline") != "offline":
            raise AuthError(f"不是离线账号: {name}")
        raw = str(raw_uuid or "").strip()
        if not raw:
            uuid = utils.offline_uuid(acc.get("name") or name)
        else:
            hex_str = raw.replace("-", "").lower()
            if len(hex_str) != 32 or any(c not in "0123456789abcdef" for c in hex_str):
                raise AuthError("UUID 格式不对：需要 32 位十六进制，可带连字符")
            uuid = utils.dashed_uuid(hex_str)
        acc["uuid"] = uuid
        self.save()
        return uuid

    def ensure_valid(self, account):
        """正版 / 皮肤站令牌过期则刷新；失败则抛 AuthError。"""
        if not account:
            return account
        if account.get("type") == "nide8":
            expired = time.time() > float(account.get("expires_at") or 0)
            if not expired and account.get("access_token"):
                return account
            from . import nide8 as nide8_mod
            account = nide8_mod.refresh(account)
            return self.add_account(account)
        if account.get("type") == "authlib":
            expired = time.time() > float(account.get("expires_at") or 0)
            if not expired and account.get("access_token"):
                return account
            from . import authlib as authlib_mod
            account = authlib_mod.refresh(account)
            return self.add_account(account)
        if account.get("type") != "microsoft":
            return account
        expired = time.time() > float(account.get("expires_at") or 0)
        if not expired and account.get("access_token"):
            return account
        if not account.get("refresh_token"):
            raise AuthError("正版令牌已过期且无法刷新，请重新登录。")
        from .config import CONFIG
        client_id = CONFIG.get("microsoft_client_id") or "00000000402b5328"
        account = MicrosoftAuthenticator(client_id=client_id).refresh(account)
        return self.add_account(account)

    def ensure_valid_or_fallback(self, account):
        """启动路径专用：令牌刷新失败（断网 / 认证服务故障）时降级为
        离线凭据，保住单机可玩性（PCL2 / HMCL 同款行为）。

        返回 (account, fallback_reason)。fallback_reason 非空表示已降级：
        返回的是临时离线账号（保留原用户名与 UUID，存档玩家数据不变，
        但进不了正版验证服务器），不写回账号存储。没有用户名的账号
        无法降级，异常原样抛出。
        """
        try:
            return self.ensure_valid(account), ""
        except Exception as e:
            acc = account or {}
            if not str(acc.get("name") or "").strip():
                raise
            utils.log.warning("账号令牌刷新失败，降级为离线身份启动: %s", e)
            fallback = {
                "type": "offline",
                "name": acc.get("name"),
                "uuid": acc.get("uuid") or "",
            }
            return fallback, str(e)

    def launch_props(self, account):
        """转换为启动参数。"""
        if account.get("type") == "microsoft":
            return {
                "name": account["name"],
                "uuid": utils.dashed_uuid(account.get("uuid") or ""),
                "token": account.get("access_token") or "0",
                "user_type": "msa",
                "xuid": account.get("xuid") or "",
            }
        if account.get("type") == "authlib":
            return {
                "name": account.get("name", "Player"),
                "uuid": utils.dashed_uuid(account.get("uuid") or ""),
                "token": account.get("access_token") or "0",
                "user_type": "mojang",
                "xuid": "",
                "authlib_api": account.get("api") or "",
            }
        if account.get("type") == "nide8":
            return {
                "name": account.get("name", "Player"),
                "uuid": utils.dashed_uuid(account.get("uuid") or ""),
                "token": account.get("access_token") or "0",
                "user_type": "mojang",
                "xuid": "",
                "nide8_id": account.get("server_id") or "",
            }
        name = account.get("name", "Player")
        return {
            "name": name,
            "uuid": utils.dashed_uuid(account.get("uuid") or "") or utils.offline_uuid(name),
            "token": "0",
            "user_type": "legacy",
            "xuid": "",
        }
