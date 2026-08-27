# -*- coding: utf-8 -*-
"""皮肤站 / 统一通行证多角色登录：pending 返回与 refresh 绑定。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mclauncher import authlib, nide8
from mclauncher.auth import AuthError

API = "https://skin.example.com/api/yggdrasil"
SID = "a" * 32

P1 = {"id": "11111111111111111111111111111111", "name": "Alice"}
P2 = {"id": "22222222222222222222222222222222", "name": "Bob"}


class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.text = ""
        self.content = b"{}"

    def json(self):
        return self._payload


class AuthlibMultiProfileTest(unittest.TestCase):
    def test_selected_profile_direct(self):
        with mock.patch.object(authlib.requests, "post", return_value=_Resp({
            "accessToken": "T", "clientToken": "C",
            "selectedProfile": P1, "availableProfiles": [P1],
        })) as post:
            acc = authlib.login(API, "u@example.com", "pw")
        self.assertEqual(acc["name"], "Alice")
        self.assertEqual(post.call_count, 1)  # 无需 refresh

    def test_multiple_profiles_pending(self):
        with mock.patch.object(authlib.requests, "post", return_value=_Resp({
            "accessToken": "T", "clientToken": "C",
            "availableProfiles": [P1, P2],
        })):
            out = authlib.login(API, "u@example.com", "pw")
        self.assertTrue(out.get("pending"))
        self.assertEqual([p["name"] for p in out["profiles"]], ["Alice", "Bob"])
        self.assertEqual(out["access_token"], "T")
        self.assertEqual(out["kind"], "authlib")

    def test_single_profile_bound_via_refresh(self):
        calls = []

        def post(url, json=None, timeout=None):
            calls.append((url, json))
            if url.endswith("/authenticate"):
                return _Resp({"accessToken": "T", "clientToken": "C",
                              "availableProfiles": [P1]})
            return _Resp({"accessToken": "T2", "clientToken": "C",
                          "selectedProfile": P1})

        with mock.patch.object(authlib.requests, "post", side_effect=post):
            acc = authlib.login(API, "u@example.com", "pw")
        self.assertEqual(acc["name"], "Alice")
        self.assertEqual(acc["access_token"], "T2")  # 绑定后的新令牌
        self.assertTrue(calls[1][0].endswith("/authserver/refresh"))
        self.assertEqual(calls[1][1]["selectedProfile"]["id"], P1["id"])

    def test_profile_id_param_selects(self):
        def post(url, json=None, timeout=None):
            if url.endswith("/authenticate"):
                return _Resp({"accessToken": "T", "clientToken": "C",
                              "availableProfiles": [P1, P2]})
            return _Resp({"accessToken": "T3", "clientToken": "C",
                          "selectedProfile": P2})

        with mock.patch.object(authlib.requests, "post", side_effect=post):
            acc = authlib.login(API, "u@example.com", "pw", profile_id=P2["id"])
        self.assertEqual(acc["name"], "Bob")

    def test_select_profile_builds_account(self):
        with mock.patch.object(authlib.requests, "post", return_value=_Resp({
            "accessToken": "T2", "clientToken": "C", "selectedProfile": P2,
        })):
            acc = authlib.select_profile(API, "T", "C", P2, "u@example.com")
        self.assertEqual(acc["type"], "authlib")
        self.assertEqual(acc["name"], "Bob")
        self.assertIn("-", acc["uuid"])  # dashed

    def test_no_profiles_raises(self):
        with mock.patch.object(authlib.requests, "post", return_value=_Resp({
            "accessToken": "T", "availableProfiles": [],
        })):
            with self.assertRaises(AuthError):
                authlib.login(API, "u@example.com", "pw")


class Nide8MultiProfileTest(unittest.TestCase):
    def test_multiple_profiles_pending(self):
        with mock.patch.object(nide8.requests, "post", return_value=_Resp({
            "accessToken": "T", "clientToken": "C",
            "availableProfiles": [P1, P2],
        })):
            out = nide8.login(SID, "user", "pw")
        self.assertTrue(out.get("pending"))
        self.assertEqual(out["kind"], "nide8")
        self.assertEqual(out["server_id"], SID)

    def test_select_profile(self):
        with mock.patch.object(nide8.requests, "post", return_value=_Resp({
            "accessToken": "T2", "clientToken": "C", "selectedProfile": P1,
        })) as post:
            acc = nide8.select_profile(SID, "T", "C", P1, "user")
        self.assertEqual(acc["type"], "nide8")
        self.assertEqual(acc["name"], "Alice")
        url = post.call_args[0][0]
        self.assertIn(SID, url)
        self.assertTrue(url.endswith("/authserver/refresh"))

    def test_single_profile_bound(self):
        def post(url, json=None, timeout=None):
            if url.endswith("/authenticate"):
                return _Resp({"accessToken": "T", "clientToken": "C",
                              "availableProfiles": [P2]})
            return _Resp({"accessToken": "T2", "selectedProfile": P2})

        with mock.patch.object(nide8.requests, "post", side_effect=post):
            acc = nide8.login(SID, "user", "pw")
        self.assertEqual(acc["name"], "Bob")
        self.assertEqual(acc["access_token"], "T2")


if __name__ == "__main__":
    unittest.main()
