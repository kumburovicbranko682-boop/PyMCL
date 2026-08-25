# -*- coding: utf-8 -*-
"""网络策略（HMCL 代理设置同款）。

四种模式：
  system  跟随系统代理（默认，和 PCL 一样）
  direct  忽略一切代理直连
  http    自定义 HTTP 代理（host/port，可带账号密码）
  socks5  自定义 SOCKS5 代理（需要 PySocks）

proxy_mode 为空时沿用旧的 use_system_proxy 布尔开关，老配置无感迁移。
"""

from __future__ import annotations

import os

from . import utils

_PROXY_KEYS = (
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "SOCKS_PROXY",
    "http_proxy", "https_proxy", "all_proxy", "socks_proxy",
)
_NO_PROXY_KEYS = ("NO_PROXY", "no_proxy")

_direct = False
# 第一次动 os.environ / urllib 前拍快照，切回 system 模式时按原样还原
_saved_env: dict | None = None
_saved_urllib: dict | None = None


def proxy_mode() -> str:
    try:
        from .config import CONFIG
        mode = str(CONFIG.get("proxy_mode") or "").strip().lower()
        if mode in ("system", "direct", "http", "socks5"):
            return mode
        return "system" if CONFIG.get("use_system_proxy", True) else "direct"
    except Exception:
        return "system"


def proxy_url() -> str:
    """自定义代理 URL（http/socks5 模式）。配置不全返回空串。"""
    from .config import CONFIG
    mode = proxy_mode()
    if mode not in ("http", "socks5"):
        return ""
    host = str(CONFIG.get("proxy_host") or "").strip()
    try:
        port = int(CONFIG.get("proxy_port") or 0)
    except (TypeError, ValueError):
        port = 0
    if not host or not (0 < port < 65536):
        return ""
    auth = ""
    user = str(CONFIG.get("proxy_user") or "").strip()
    if user:
        from urllib.parse import quote
        pw = str(CONFIG.get("proxy_pass") or "")
        auth = quote(user, safe="")
        if pw:
            auth += ":" + quote(pw, safe="")
        auth += "@"
    # socks5h：域名也走代理解析，不然内网 DNS 泄漏且被墙域名照样解析失败
    scheme = "socks5h" if mode == "socks5" else "http"
    return f"{scheme}://{auth}{host}:{port}"


def use_system_proxy() -> bool:
    return proxy_mode() == "system"


def force_direct() -> bool:
    return proxy_mode() == "direct" or _direct


def apply_proxy_to_session(session):
    """把当前代理策略打进一个 requests.Session。system 模式不动它。"""
    mode = proxy_mode()
    if mode == "direct":
        session.trust_env = False
        session.proxies = {"http": None, "https": None}
    elif mode in ("http", "socks5"):
        url = proxy_url()
        if url:
            session.trust_env = False
            session.proxies = {"http": url, "https": url}


# 旧名字保留：早期只有直连一种策略，各处 session 建好都调它
apply_direct_to_session = apply_proxy_to_session


def apply_proxy_policy():
    """按设置应用代理策略（幂等，可在运行时来回切）。"""
    global _direct
    mode = proxy_mode()
    _snapshot_env()
    if mode == "system":
        _direct = False
        _restore_env()
        _restore_urllib()
        return
    if mode == "direct":
        _install_direct()
        return
    # 自定义代理
    _direct = False
    url = proxy_url()
    if not url:
        utils.log.warning("代理设置不完整（主机/端口），临时按系统代理处理")
        _restore_env()
        _restore_urllib()
        return
    if mode == "socks5":
        try:
            import socks  # noqa: F401  (PySocks)
        except ImportError:
            utils.log.warning("SOCKS5 代理需要 PySocks（pip install pysocks），临时按系统代理处理")
            _restore_env()
            _restore_urllib()
            return
    _restore_urllib()
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                "http_proxy", "https_proxy", "all_proxy"):
        os.environ[key] = url
    # 本机回环别过代理：桥接服务、陶瓦联机、本地皮肤服务都在 127.0.0.1
    os.environ["NO_PROXY"] = "localhost,127.0.0.1"
    os.environ["no_proxy"] = "localhost,127.0.0.1"
    _ensure_requests_patch()


def test_proxy(timeout: float = 8.0) -> dict:
    """按当前策略试连一次，返回 {ok, latency_ms, message}。给设置页「测试」按钮用。"""
    import time

    import requests

    session = requests.Session()
    apply_proxy_to_session(session)
    urls = ("https://bmclapi2.bangbang93.com/mc/game/version_manifest.json",
            "https://api.modrinth.com/")
    last_err = ""
    for url in urls:
        start = time.time()
        try:
            resp = session.head(url, timeout=timeout, allow_redirects=True)
            if resp.status_code < 500:
                return {"ok": True,
                        "latency_ms": int((time.time() - start) * 1000),
                        "message": f"HTTP {resp.status_code}"}
            last_err = f"HTTP {resp.status_code}"
        except Exception as exc:
            last_err = str(exc)
    return {"ok": False, "latency_ms": -1, "message": last_err}


# ---------------------------------------------------------------- 内部

def _snapshot_env():
    global _saved_env
    if _saved_env is not None:
        return
    _saved_env = {k: os.environ.get(k) for k in _PROXY_KEYS + _NO_PROXY_KEYS}


def _restore_env():
    if _saved_env is None:
        return
    for key, val in _saved_env.items():
        if val is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = val


def _restore_urllib():
    if _saved_urllib is None:
        return
    import urllib.request
    for name, fn in _saved_urllib.items():
        setattr(urllib.request, name, fn)


def _patch_requests():
    """把「按当前模式应用策略」打进 requests.Session（幂等，动态读模式）。"""
    import requests
    orig = requests.Session.__init__
    if getattr(orig, "_pymcl_proxy", False):
        return

    def _init(self, *args, **kwargs):
        orig(self, *args, **kwargs)
        apply_proxy_to_session(self)

    _init._pymcl_proxy = True
    requests.Session.__init__ = _init


class _RequestsProxySpy:
    """requests 真正被导入时再打 Session 补丁，别为策略提前拉起整个 requests。"""

    def find_spec(self, name, path=None, target=None):
        if name != "requests":
            return None
        import sys
        try:
            sys.meta_path.remove(self)
        except ValueError:
            pass
        import importlib
        module = importlib.import_module("requests")
        _patch_requests()
        return module.__spec__


def _ensure_requests_patch():
    import sys
    if "requests" in sys.modules:
        _patch_requests()
        return
    if not any(isinstance(m, _RequestsProxySpy) for m in sys.meta_path):
        sys.meta_path.insert(0, _RequestsProxySpy())


def _install_direct():
    global _direct, _saved_urllib
    _direct = True
    for key in _PROXY_KEYS:
        os.environ.pop(key, None)
    os.environ["NO_PROXY"] = "*"
    os.environ["no_proxy"] = "*"

    import urllib.request
    if _saved_urllib is None:
        _saved_urllib = {
            name: getattr(urllib.request, name)
            for name in ("getproxies", "getproxies_environment", "getproxies_registry")
            if hasattr(urllib.request, name)
        }
    urllib.request.getproxies = lambda: {}
    if hasattr(urllib.request, "getproxies_environment"):
        urllib.request.getproxies_environment = lambda: {}
    if hasattr(urllib.request, "getproxies_registry"):
        urllib.request.getproxies_registry = lambda: {}
    _ensure_requests_patch()
