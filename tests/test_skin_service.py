# -*- coding: utf-8 -*-
"""皮肤上传 / 重置 / 披风管理：对着本地 mock 服务器验证请求形态与错误映射。"""
from __future__ import annotations

import json
import struct
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from mclauncher import skin


def fake_png(width: int, height: int, pad: int = 64) -> bytes:
    ihdr = struct.pack(">II", width, height) + b"\x08\x06\x00\x00\x00"
    chunk = struct.pack(">I", 13) + b"IHDR" + ihdr + b"\x00\x00\x00\x00"
    return b"\x89PNG\r\n\x1a\n" + chunk + b"\x00" * pad


PROFILE_BODY = {
    "id": "11111111222233334444555555555555",
    "name": "Tester",
    "skins": [
        {"id": "s1", "state": "ACTIVE",
         "url": "http://textures/skin1.png", "variant": "SLIM"},
        {"id": "s0", "state": "INACTIVE",
         "url": "http://textures/skin0.png", "variant": "CLASSIC"},
    ],
    "capes": [
        {"id": "c1", "state": "ACTIVE", "url": "http://textures/cape1.png",
         "alias": "Migrator"},
        {"id": "c2", "state": "INACTIVE", "url": "http://textures/cape2.png",
         "alias": "Vanilla"},
    ],
}


class _Handler(BaseHTTPRequestHandler):
    requests: list[dict] = []

    def log_message(self, *args):
        pass

    def _record(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        entry = {
            "method": self.command,
            "path": self.path,
            "auth": self.headers.get("Authorization") or "",
            "content_type": self.headers.get("Content-Type") or "",
            "body": body,
        }
        _Handler.requests.append(entry)
        return entry

    def _reply(self, code: int, payload=None):
        self.send_response(code)
        data = json.dumps(payload).encode() if payload is not None else b""
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if data:
            self.wfile.write(data)

    def _dispatch(self):
        entry = self._record()
        if entry["auth"] == "Bearer expired":
            self._reply(401, {"error": "Unauthorized"})
            return
        if entry["auth"] == "Bearer throttled":
            self._reply(429, {"error": "TooManyRequests"})
            return
        method, path = entry["method"], entry["path"]
        if method == "GET" and path == "/minecraft/profile":
            self._reply(200, PROFILE_BODY)
        elif method == "POST" and path == "/minecraft/profile/skins":
            self._reply(200, PROFILE_BODY)
        elif method == "DELETE" and path == "/minecraft/profile/skins/active":
            self._reply(204)
        elif method == "PUT" and path == "/minecraft/profile/capes/active":
            self._reply(200, PROFILE_BODY)
        elif method == "DELETE" and path == "/minecraft/profile/capes/active":
            self._reply(204)
        elif method == "GET" and path.startswith(
                "/ygg/sessionserver/session/minecraft/profile/"):
            import base64 as b64
            textures = {"textures": {"SKIN": {
                "url": f"http://{self.headers.get('Host')}/textures/skin.png",
                "metadata": {"model": "slim"},
            }}}
            payload = b64.b64encode(json.dumps(textures).encode()).decode()
            self._reply(200, {"id": "u", "name": "Tester",
                              "properties": [{"name": "textures",
                                              "value": payload}]})
        elif method == "GET" and path == "/textures/skin.png":
            data = fake_png(64, 64)
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        elif method == "PUT" and path.startswith("/ygg/api/user/profile/"):
            self._reply(204)
        elif method == "DELETE" and path.startswith("/ygg/api/user/profile/"):
            if path.endswith("/nopermission/skin"):
                self._reply(405)
            else:
                self._reply(204)
        elif method == "GET" and path.startswith("/mojang/users/"):
            who = path.rsplit("/", 1)[-1]
            if who == "Tester":
                self._reply(200, {"id": "1111aaaa1111aaaa1111aaaa1111aaaa",
                                  "name": "Tester"})
            elif who == "Plain":
                self._reply(200, {"id": "2222bbbb2222bbbb2222bbbb2222bbbb",
                                  "name": "Plain"})
            elif who == "Throttle":
                self._reply(429, {"error": "TooManyRequests"})
            else:
                self._reply(404, {"errorMessage": "Couldn't find any profile"})
        elif method == "GET" and path.startswith("/mojang/session/"):
            import base64 as b64
            uuid = path.rsplit("/", 1)[-1]
            if uuid.startswith("1111"):
                textures = {"textures": {
                    "SKIN": {
                        "url": f"http://{self.headers.get('Host')}/textures/skin.png",
                        "metadata": {"model": "slim"},
                    },
                    "CAPE": {
                        "url": f"http://{self.headers.get('Host')}/textures/cape.png",
                    },
                }}
                payload = b64.b64encode(json.dumps(textures).encode()).decode()
                self._reply(200, {"id": uuid, "name": "Tester",
                                  "properties": [{"name": "textures",
                                                  "value": payload}]})
            else:
                # 默认皮肤玩家：没有 textures 属性
                self._reply(200, {"id": uuid, "name": "Plain", "properties": []})
        else:
            self._reply(404, {"error": "NotFound"})

    do_GET = do_POST = do_PUT = do_DELETE = _dispatch


class SkinServiceTests(unittest.TestCase):
    server: ThreadingHTTPServer

    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        host, port = cls.server.server_address
        cls.base = f"http://{host}:{port}"
        cls.ms_api = cls.base + "/minecraft/profile"
        cls.ygg_api = cls.base + "/ygg"
        cls.tmp = tempfile.TemporaryDirectory()
        cls.skin_path = Path(cls.tmp.name) / "skin.png"
        cls.skin_path.write_bytes(fake_png(64, 64))

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def setUp(self):
        _Handler.requests = []

    # ---- 文件校验

    def test_rejects_non_png(self):
        path = Path(self.tmp.name) / "bad.txt"
        path.write_bytes(b"not a png at all, definitely not")
        with self.assertRaises(skin.SkinError) as ctx:
            skin.read_skin_file(path)
        self.assertIn("PNG", str(ctx.exception))

    def test_rejects_wrong_dimensions(self):
        path = Path(self.tmp.name) / "wrong.png"
        path.write_bytes(fake_png(128, 128))
        with self.assertRaises(skin.SkinError) as ctx:
            skin.read_skin_file(path)
        self.assertIn("128", str(ctx.exception))

    def test_accepts_legacy_64x32(self):
        path = Path(self.tmp.name) / "legacy.png"
        path.write_bytes(fake_png(64, 32))
        self.assertTrue(skin.read_skin_file(path))

    def test_missing_file(self):
        with self.assertRaises(skin.SkinError):
            skin.read_skin_file(Path(self.tmp.name) / "nope.png")

    def test_variant_normalization(self):
        self.assertEqual(skin.normalize_variant("SLIM"), "slim")
        self.assertEqual(skin.normalize_variant("alex"), "slim")
        self.assertEqual(skin.normalize_variant(""), "classic")
        self.assertEqual(skin.normalize_variant("classic"), "classic")

    # ---- 微软 profile

    def test_fetch_profile_parses_variant_and_cape(self):
        prof = skin.fetch_ms_profile("tok", api_base=self.ms_api)
        self.assertEqual(prof["name"], "Tester")
        self.assertEqual(prof["variant"], "slim")
        self.assertEqual(prof["active_cape"], "c1")
        self.assertEqual(len(prof["capes"]), 2)
        self.assertEqual(prof["capes"][0]["alias"], "Migrator")
        req = _Handler.requests[0]
        self.assertEqual(req["auth"], "Bearer tok")

    def test_upload_skin_sends_multipart(self):
        prof = skin.upload_ms_skin("tok", self.skin_path, "slim",
                                   api_base=self.ms_api)
        self.assertEqual(prof["name"], "Tester")
        req = _Handler.requests[0]
        self.assertEqual(req["method"], "POST")
        self.assertIn("multipart/form-data", req["content_type"])
        self.assertIn(b'name="variant"', req["body"])
        self.assertIn(b"slim", req["body"])
        self.assertIn(b'name="file"', req["body"])
        self.assertIn(b"\x89PNG", req["body"])

    def test_reset_skin(self):
        skin.reset_ms_skin("tok", api_base=self.ms_api)
        req = _Handler.requests[0]
        self.assertEqual(req["method"], "DELETE")
        self.assertTrue(req["path"].endswith("/skins/active"))

    def test_set_cape(self):
        skin.set_ms_cape("tok", "c2", api_base=self.ms_api)
        req = _Handler.requests[0]
        self.assertEqual(req["method"], "PUT")
        self.assertEqual(json.loads(req["body"]), {"capeId": "c2"})

    def test_hide_cape(self):
        skin.set_ms_cape("tok", "", api_base=self.ms_api)
        req = _Handler.requests[0]
        self.assertEqual(req["method"], "DELETE")
        self.assertTrue(req["path"].endswith("/capes/active"))

    def test_expired_token_message(self):
        with self.assertRaises(skin.SkinError) as ctx:
            skin.fetch_ms_profile("expired", api_base=self.ms_api)
        self.assertIn("重新登录", str(ctx.exception))

    def test_throttled_message(self):
        with self.assertRaises(skin.SkinError) as ctx:
            skin.upload_ms_skin("throttled", self.skin_path,
                                api_base=self.ms_api)
        self.assertIn("频繁", str(ctx.exception))

    # ---- 皮肤站（authlib-injector）

    def test_ygg_upload(self):
        skin.upload_ygg_skin(self.ygg_api, "tok",
                             "11111111-2222-3333-4444-555555555555",
                             self.skin_path, "slim")
        req = _Handler.requests[0]
        self.assertEqual(req["method"], "PUT")
        self.assertEqual(
            req["path"],
            "/ygg/api/user/profile/11111111222233334444555555555555/skin")
        self.assertIn(b'name="model"', req["body"])
        self.assertIn(b"slim", req["body"])
        self.assertIn(b"\x89PNG", req["body"])

    def test_ygg_classic_model_is_empty_string(self):
        skin.upload_ygg_skin(self.ygg_api, "tok",
                             "11111111222233334444555555555555",
                             self.skin_path, "classic")
        body = _Handler.requests[0]["body"]
        self.assertIn(b'name="model"', body)
        self.assertNotIn(b"slim", body)

    def test_ygg_reset(self):
        skin.reset_ygg_skin(self.ygg_api, "tok",
                            "11111111222233334444555555555555")
        req = _Handler.requests[0]
        self.assertEqual(req["method"], "DELETE")

    def test_ygg_unsupported_site_message(self):
        with self.assertRaises(skin.SkinError) as ctx:
            skin.reset_ygg_skin(self.ygg_api, "tok", "nopermission")
        self.assertIn("不支持", str(ctx.exception))

    def test_ygg_missing_api(self):
        with self.assertRaises(skin.SkinError):
            skin.upload_ygg_skin("", "tok", "u", self.skin_path)

    # ---- 皮肤纹理获取（本地渲染用）

    def test_ygg_texture_info(self):
        info = skin.fetch_ygg_texture_info(
            self.ygg_api, "11111111222233334444555555555555")
        self.assertTrue(info["url"].endswith("/textures/skin.png"))
        self.assertEqual(info["variant"], "slim")

    def test_fetch_skin_texture_authlib(self):
        acc = {"type": "authlib", "api": self.ygg_api,
               "uuid": "11111111222233334444555555555555"}
        data = skin.fetch_skin_texture(acc)
        self.assertEqual(data["variant"], "slim")
        self.assertTrue(data["png"].startswith(b"\x89PNG"))

    def test_fetch_skin_texture_microsoft(self):
        from unittest import mock
        profile = {"skins": [
            {"active": True, "url": self.base + "/textures/skin.png",
             "variant": "classic"}]}
        with mock.patch.object(skin, "fetch_ms_profile", return_value=profile):
            data = skin.fetch_skin_texture(
                {"type": "microsoft", "access_token": "tok"})
        self.assertEqual(data["variant"], "classic")
        self.assertTrue(data["png"].startswith(b"\x89PNG"))

    def test_fetch_skin_texture_offline_rejected(self):
        with self.assertRaises(skin.SkinError):
            skin.fetch_skin_texture({"type": "offline", "name": "Player"})


class PlayerLookupTests(unittest.TestCase):
    """正版玩家查询（对标 PCL 百宝箱皮肤下载）。"""

    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        host, port = cls.server.server_address
        cls.base = f"http://{host}:{port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        _Handler.requests = []
        self._p1 = mock.patch.object(
            skin, "MOJANG_UUID_API", self.base + "/mojang/users")
        self._p2 = mock.patch.object(
            skin, "MOJANG_SESSION_API", self.base + "/mojang/session")
        self._p1.start()
        self._p2.start()
        self.addCleanup(self._p1.stop)
        self.addCleanup(self._p2.stop)

    def test_lookup_slim_with_cape(self):
        info = skin.lookup_player("Tester")
        self.assertEqual(info["name"], "Tester")
        self.assertEqual(info["uuid"], "1111aaaa-1111-aaaa-1111-aaaa1111aaaa")
        self.assertEqual(info["variant"], "slim")
        self.assertTrue(info["skin_url"].endswith("/textures/skin.png"))
        self.assertTrue(info["cape_url"].endswith("/textures/cape.png"))

    def test_lookup_missing_player(self):
        with self.assertRaises(skin.SkinError) as ctx:
            skin.lookup_player("Nobody")
        self.assertIn("不存在", str(ctx.exception))

    def test_lookup_throttled(self):
        with self.assertRaises(skin.SkinError) as ctx:
            skin.lookup_player("Throttle")
        self.assertIn("频繁", str(ctx.exception))

    def test_invalid_name_rejected_locally(self):
        with self.assertRaises(skin.SkinError):
            skin.lookup_player("bad name!")
        with self.assertRaises(skin.SkinError):
            skin.lookup_player("x" * 17)
        self.assertEqual(_Handler.requests, [])

    def test_fetch_player_skin_downloads_png(self):
        info = skin.fetch_player_skin("Tester")
        self.assertTrue(info["png"].startswith(b"\x89PNG"))
        self.assertEqual(info["variant"], "slim")

    def test_default_skin_has_no_png(self):
        info = skin.fetch_player_skin("Plain")
        self.assertNotIn("png", info)
        self.assertEqual(info["uuid"], "2222bbbb-2222-bbbb-2222-bbbb2222bbbb")
        self.assertEqual(info["skin_url"], "")


if __name__ == "__main__":
    unittest.main()
