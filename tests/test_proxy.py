# -*- coding: utf-8 -*-
"""自定义代理（HMCL 设置同款）。

覆盖：proxy_mode 新旧开关兼容、proxy_url 构造（含认证转义与 socks5h）、
apply_proxy_to_session 三态、apply_proxy_policy 的环境变量进退场、
test_proxy 试连结果、两个门面的设置键回写与钳位。
"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from mclauncher import net, utils
from mclauncher.config import CONFIG, DEFAULT_CONFIG


class _Isolated(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        data = {k: (list(v) if isinstance(v, list) else dict(v) if isinstance(v, dict) else v)
                for k, v in DEFAULT_CONFIG.items()}
        for p in (patch.object(utils, "ROOT", Path(self._td.name)),
                  patch.object(CONFIG, "data", data),
                  patch.object(CONFIG, "save", lambda: None)):
            p.start()
            self.addCleanup(p.stop)

    def _set(self, **kw):
        for k, v in kw.items():
            CONFIG.set(k, v)


class DefaultsTests(unittest.TestCase):
    def test_defaults(self):
        self.assertEqual(DEFAULT_CONFIG.get("proxy_mode"), "")
        self.assertEqual(DEFAULT_CONFIG.get("proxy_host"), "")
        self.assertEqual(DEFAULT_CONFIG.get("proxy_port"), 0)
        self.assertEqual(DEFAULT_CONFIG.get("proxy_user"), "")
        self.assertEqual(DEFAULT_CONFIG.get("proxy_pass"), "")


class ModeTests(_Isolated):
    def test_legacy_fallback(self):
        # proxy_mode 为空沿用旧布尔开关：True=system / False=direct
        self._set(proxy_mode="", use_system_proxy=True)
        self.assertEqual(net.proxy_mode(), "system")
        self._set(use_system_proxy=False)
        self.assertEqual(net.proxy_mode(), "direct")

    def test_explicit_modes_win(self):
        for mode in ("system", "direct", "http", "socks5"):
            self._set(proxy_mode=mode, use_system_proxy=(mode != "system"))
            self.assertEqual(net.proxy_mode(), mode)

    def test_invalid_mode_falls_back(self):
        self._set(proxy_mode="ftp", use_system_proxy=False)
        self.assertEqual(net.proxy_mode(), "direct")


class UrlTests(_Isolated):
    def test_http_url(self):
        self._set(proxy_mode="http", proxy_host="127.0.0.1", proxy_port=7890)
        self.assertEqual(net.proxy_url(), "http://127.0.0.1:7890")

    def test_socks5_uses_socks5h(self):
        # socks5h：域名交给代理解析，被墙域名本地解析必失败
        self._set(proxy_mode="socks5", proxy_host="10.0.0.2", proxy_port=1080)
        self.assertEqual(net.proxy_url(), "socks5h://10.0.0.2:1080")

    def test_auth_is_percent_encoded(self):
        self._set(proxy_mode="http", proxy_host="p.example", proxy_port=8080,
                  proxy_user="u@ser", proxy_pass="p:a/ss")
        self.assertEqual(net.proxy_url(), "http://u%40ser:p%3Aa%2Fss@p.example:8080")

    def test_incomplete_returns_empty(self):
        self._set(proxy_mode="http", proxy_host="", proxy_port=7890)
        self.assertEqual(net.proxy_url(), "")
        self._set(proxy_host="127.0.0.1", proxy_port=0)
        self.assertEqual(net.proxy_url(), "")
        self._set(proxy_port=70000)
        self.assertEqual(net.proxy_url(), "")
        self._set(proxy_mode="system", proxy_port=7890)
        self.assertEqual(net.proxy_url(), "")


class SessionTests(_Isolated):
    class _FakeSession:
        def __init__(self):
            self.trust_env = True
            self.proxies = {}

    def test_direct_disables_env(self):
        self._set(proxy_mode="direct")
        s = self._FakeSession()
        net.apply_proxy_to_session(s)
        self.assertFalse(s.trust_env)
        self.assertEqual(s.proxies, {"http": None, "https": None})

    def test_custom_sets_proxies(self):
        self._set(proxy_mode="http", proxy_host="127.0.0.1", proxy_port=7890)
        s = self._FakeSession()
        net.apply_proxy_to_session(s)
        self.assertFalse(s.trust_env)
        self.assertEqual(s.proxies,
                         {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"})

    def test_system_leaves_session_alone(self):
        self._set(proxy_mode="system")
        s = self._FakeSession()
        net.apply_proxy_to_session(s)
        self.assertTrue(s.trust_env)
        self.assertEqual(s.proxies, {})

    def test_custom_incomplete_leaves_session_alone(self):
        self._set(proxy_mode="http", proxy_host="", proxy_port=0)
        s = self._FakeSession()
        net.apply_proxy_to_session(s)
        self.assertTrue(s.trust_env)


class PolicyTests(_Isolated):
    """apply_proxy_policy 的 os.environ / urllib 进退场。"""

    def setUp(self):
        super().setUp()
        keys = net._PROXY_KEYS + net._NO_PROXY_KEYS
        self._env_backup = {k: os.environ.get(k) for k in keys}
        # 强制模块重新拍快照：本用例控制的环境才是「原始环境」
        for p in (patch.object(net, "_saved_env", None),
                  patch.object(net, "_saved_urllib", None),
                  patch.object(net, "_direct", False)):
            p.start()
            self.addCleanup(p.stop)
        # LIFO：先还原 urllib 补丁，再退模块全局补丁，最后还原环境变量
        self.addCleanup(self._restore_env)
        self.addCleanup(net._restore_urllib)

    def _restore_env(self):
        for k, v in self._env_backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_http_mode_exports_env(self):
        self._set(proxy_mode="http", proxy_host="127.0.0.1", proxy_port=7890)
        net.apply_proxy_policy()
        self.assertEqual(os.environ.get("HTTPS_PROXY"), "http://127.0.0.1:7890")
        self.assertEqual(os.environ.get("http_proxy"), "http://127.0.0.1:7890")
        # 回环必须直连：桥接/陶瓦/本地皮肤服务都挂在 127.0.0.1
        self.assertIn("127.0.0.1", os.environ.get("NO_PROXY", ""))
        self.assertFalse(net.force_direct())

    def test_direct_mode_clears_env(self):
        os.environ["HTTP_PROXY"] = "http://stale:1"
        self._set(proxy_mode="direct")
        net.apply_proxy_policy()
        self.assertIsNone(os.environ.get("HTTP_PROXY"))
        self.assertEqual(os.environ.get("NO_PROXY"), "*")
        self.assertTrue(net.force_direct())
        import urllib.request
        self.assertEqual(urllib.request.getproxies(), {})

    def test_switch_back_to_system_restores_env(self):
        os.environ["HTTPS_PROXY"] = "http://original:8888"
        self._set(proxy_mode="http", proxy_host="1.2.3.4", proxy_port=1)
        net.apply_proxy_policy()
        self.assertEqual(os.environ.get("HTTPS_PROXY"), "http://1.2.3.4:1")
        self._set(proxy_mode="system")
        net.apply_proxy_policy()
        self.assertEqual(os.environ.get("HTTPS_PROXY"), "http://original:8888")
        self.assertFalse(net.force_direct())

    def test_incomplete_custom_behaves_like_system(self):
        os.environ.pop("HTTPS_PROXY", None)
        self._set(proxy_mode="http", proxy_host="", proxy_port=0)
        net.apply_proxy_policy()
        self.assertIsNone(os.environ.get("HTTPS_PROXY"))

    def test_requests_sessions_pick_up_policy(self):
        self._set(proxy_mode="http", proxy_host="127.0.0.1", proxy_port=7897)
        net.apply_proxy_policy()
        import requests
        s = requests.Session()
        self.assertEqual(s.proxies.get("https"), "http://127.0.0.1:7897")
        self.assertFalse(s.trust_env)
        # 切回 system 后新会话不再被改写
        self._set(proxy_mode="system")
        net.apply_proxy_policy()
        s2 = requests.Session()
        self.assertEqual(s2.proxies, {})
        self.assertTrue(s2.trust_env)


class TestProxyFnTests(_Isolated):
    class _Resp:
        def __init__(self, code):
            self.status_code = code

    def test_ok(self):
        resp = self._Resp(200)

        class _S:
            trust_env = True
            proxies = {}

            def head(self, url, timeout=0, allow_redirects=True):
                return resp

        with patch("requests.Session", _S):
            out = net.test_proxy(timeout=1)
        self.assertTrue(out["ok"])
        self.assertGreaterEqual(out["latency_ms"], 0)
        self.assertIn("200", out["message"])

    def test_failure_reports_message(self):
        class _S:
            trust_env = True
            proxies = {}

            def head(self, url, timeout=0, allow_redirects=True):
                raise OSError("connection refused")

        with patch("requests.Session", _S):
            out = net.test_proxy(timeout=1)
        self.assertFalse(out["ok"])
        self.assertEqual(out["latency_ms"], -1)
        self.assertIn("connection refused", out["message"])


class BridgeFacadeTests(_Isolated):
    def setUp(self):
        super().setUp()
        p = patch("mclauncher.net.apply_proxy_policy", lambda *a, **k: None)
        p.start()
        self.addCleanup(p.stop)
        from bridge.api import BackendAPI

        class _Bus:
            def emit(self, *a, **k):
                pass

        self.api = BackendAPI(_Bus())

    def test_settings_roundtrip_with_clamp(self):
        self.api.save_settings({"proxy_mode": "socks5", "proxy_host": " 1.2.3.4 ",
                                "proxy_port": 99999, "proxy_user": "u", "proxy_pass": "p"})
        s = self.api.get_settings()
        self.assertEqual(s["proxy_mode"], "socks5")
        self.assertEqual(s["proxy_host"], "1.2.3.4")
        self.assertEqual(s["proxy_port"], 65535)  # 钳位
        self.assertEqual(s["proxy_user"], "u")
        self.assertEqual(s["proxy_pass"], "p")

    def test_invalid_mode_ignored(self):
        self.api.save_settings({"proxy_mode": "http"})
        self.api.save_settings({"proxy_mode": "gopher"})
        self.assertEqual(CONFIG.get("proxy_mode"), "http")

    def test_test_proxy_shape(self):
        with patch("mclauncher.net.test_proxy",
                   lambda *a, **k: {"ok": True, "latency_ms": 5, "message": "HTTP 200"}):
            out = self.api.test_proxy()
        self.assertTrue(out["ok"])


class QtFacadeTests(_Isolated):
    def setUp(self):
        super().setUp()
        for target in ("mclauncher.source.invalidate_probe",
                       "mclauncher.source.warmup_async",
                       "mclauncher.net.apply_proxy_policy"):
            p = patch(target, lambda *a, **k: None)
            p.start()
            self.addCleanup(p.stop)

    def test_settings_roundtrip(self):
        from app.backend import BackendAPI as QtBackend
        QtBackend.save_settings(None, {"proxy_mode": "http", "proxy_host": "h",
                                       "proxy_port": 7890})
        s = QtBackend.get_settings(None)
        self.assertEqual(s["proxy_mode"], "http")
        self.assertEqual(s["proxy_host"], "h")
        self.assertEqual(s["proxy_port"], 7890)
        # 只传部分键不冲掉已存值
        QtBackend.save_settings(None, {"download_threads": 8})
        self.assertEqual(CONFIG.get("proxy_host"), "h")


if __name__ == "__main__":
    unittest.main()
