# -*- coding: utf-8 -*-
"""skin_ops 单测：PNG 校验、能力判定、上传/重置请求构成。"""
from __future__ import annotations

import http.server
import struct
import tempfile
import threading
import unittest
import zlib
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from mclauncher import skin_ops
from mclauncher.skin_ops import SkinError


def make_png(width: int, height: int, pad_to: int = 0) -> bytes:
    def chunk(typ: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + typ + data
                + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    raw = b"".join(b"\x00" + b"\x00\x00\x00\x00" * width for _ in range(height))
    idat = zlib.compress(raw)
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
           + chunk(b"IDAT", idat) + chunk(b"IEND", b""))
    if pad_to and len(png) < pad_to:
        # 塞一个无压缩的私有 chunk 撑大文件
        png = (png[: -12] + chunk(b"prIv", b"\x00" * (pad_to - len(png)))
               + png[-12:])
    return png


class RecordingHandler(http.server.BaseHTTPRequestHandler):
    records: list = []
    status = 204

    def _record(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        type(self).records.append({
            "method": self.command,
            "path": self.path,
            "auth": self.headers.get("Authorization") or "",
            "body": body,
        })
        self.send_response(type(self).status)
        self.end_headers()

    do_PUT = _record
    do_DELETE = _record

    def log_message(self, *_args):
        pass


@contextmanager
def ygg_server(status: int = 204):
    RecordingHandler.records = []
    RecordingHandler.status = status
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), RecordingHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", RecordingHandler.records
    finally:
        server.shutdown()
        server.server_close()


class TestPngValidation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, name: str, data: bytes) -> str:
        p = self.dir / name
        p.write_bytes(data)
        return str(p)

    def test_read_png_size(self):
        self.assertEqual(skin_ops.read_png_size(make_png(64, 64)), (64, 64))
        self.assertEqual(skin_ops.read_png_size(make_png(64, 32)), (64, 32))

    def test_not_png_rejected(self):
        path = self._write("bad.png", b"JFIF not a png")
        with self.assertRaises(SkinError):
            skin_ops.load_skin_file(path)

    def test_missing_file(self):
        with self.assertRaises(SkinError):
            skin_ops.load_skin_file(str(self.dir / "nope.png"))

    def test_mojang_dims(self):
        ok64 = self._write("a.png", make_png(64, 64))
        ok_legacy = self._write("b.png", make_png(64, 32))
        bad = self._write("c.png", make_png(128, 128))
        skin_ops.load_skin_file(ok64, strict_mojang=True)
        skin_ops.load_skin_file(ok_legacy, strict_mojang=True)
        with self.assertRaises(SkinError):
            skin_ops.load_skin_file(bad, strict_mojang=True)

    def test_mojang_size_limit(self):
        big = self._write("big.png", make_png(64, 64, pad_to=skin_ops.MS_MAX_BYTES + 100))
        with self.assertRaises(SkinError):
            skin_ops.load_skin_file(big, strict_mojang=True)
        # 皮肤站不卡 24KB
        skin_ops.load_skin_file(big, strict_mojang=False)

    def test_hd_skin_for_authlib(self):
        hd = self._write("hd.png", make_png(128, 128))
        skin_ops.load_skin_file(hd, strict_mojang=False)
        weird = self._write("w.png", make_png(100, 64))
        with self.assertRaises(SkinError):
            skin_ops.load_skin_file(weird, strict_mojang=False)

    def test_normalize_variant(self):
        self.assertEqual(skin_ops.normalize_variant("slim"), "slim")
        self.assertEqual(skin_ops.normalize_variant("Alex"), "slim")
        self.assertEqual(skin_ops.normalize_variant("classic"), "classic")
        self.assertEqual(skin_ops.normalize_variant(""), "classic")
        self.assertEqual(skin_ops.normalize_variant(None), "classic")


class TestChangeSupport(unittest.TestCase):
    def test_kinds(self):
        self.assertTrue(skin_ops.change_support({"type": "microsoft"})["ok"])
        self.assertTrue(skin_ops.change_support({"type": "authlib"})["ok"])
        # 离线账号支持本地皮肤，并带一条说明文案
        offline = skin_ops.change_support({"type": "offline"})
        self.assertTrue(offline["ok"])
        self.assertTrue(offline["note"])
        for kind in ("nide8", ""):
            res = skin_ops.change_support({"type": kind})
            self.assertFalse(res["ok"])
            self.assertTrue(res["reason"])
        self.assertFalse(skin_ops.change_support(None)["ok"])


class TestYggdrasilRequests(unittest.TestCase):
    UUID = "12345678-1234-1234-1234-123456789abc"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.png = Path(self.tmp.name) / "skin.png"
        self.png.write_bytes(make_png(64, 64))

    def tearDown(self):
        self.tmp.cleanup()

    def _account(self, api: str) -> dict:
        return {"type": "authlib", "api": api, "uuid": self.UUID,
                "access_token": "tok123", "name": "Tester"}

    def test_upload_slim(self):
        with ygg_server() as (api, records):
            msg = skin_ops.upload_skin(self._account(api), str(self.png), "slim")
        self.assertIn("皮肤已更换", msg)
        self.assertEqual(len(records), 1)
        rec = records[0]
        self.assertEqual(rec["method"], "PUT")
        self.assertEqual(
            rec["path"],
            f"/api/user/profile/{self.UUID.replace('-', '')}/skin")
        self.assertEqual(rec["auth"], "Bearer tok123")
        self.assertIn(b'name="model"', rec["body"])
        self.assertIn(b"slim", rec["body"])
        self.assertIn(b"\x89PNG", rec["body"])

    def test_upload_classic_model_empty(self):
        with ygg_server() as (api, records):
            skin_ops.upload_skin(self._account(api), str(self.png), "classic")
        body = records[0]["body"]
        self.assertIn(b'name="model"', body)
        self.assertNotIn(b"slim", body)

    def test_reset(self):
        with ygg_server() as (api, records):
            msg = skin_ops.reset_skin(self._account(api))
        self.assertIn("重置", msg)
        self.assertEqual(records[0]["method"], "DELETE")
        self.assertEqual(records[0]["auth"], "Bearer tok123")

    def test_expired_token_401(self):
        with ygg_server(status=401) as (api, _):
            with self.assertRaises(SkinError) as ctx:
                skin_ops.upload_skin(self._account(api), str(self.png))
        self.assertIn("重新登录", str(ctx.exception))

    def test_unsupported_site_405(self):
        with ygg_server(status=405) as (api, _):
            with self.assertRaises(SkinError) as ctx:
                skin_ops.upload_skin(self._account(api), str(self.png))
        self.assertIn("不支持", str(ctx.exception))

    def test_missing_token(self):
        acc = self._account("http://127.0.0.1:1")
        acc["access_token"] = ""
        with self.assertRaises(SkinError):
            skin_ops.upload_skin(acc, str(self.png))

    def test_unsupported_account(self):
        with self.assertRaises(SkinError):
            skin_ops.upload_skin({"type": "nide8", "name": "x"}, str(self.png))
        with self.assertRaises(SkinError):
            skin_ops.reset_skin({"type": "nide8", "name": "x"})

    def test_offline_routes_to_local(self):
        """离线账号走本地皮肤：写账号字段，不发任何网络请求。"""
        from mclauncher import offline_skin, utils
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(utils, "ROOT", Path(tmp)):
            acc = {"type": "offline", "name": "x", "uuid": ""}
            msg = skin_ops.upload_skin(acc, str(self.png), "slim")
            self.assertIn("离线皮肤", msg)
            self.assertTrue(acc.get("skin_file"))
            self.assertEqual(acc.get("skin_model"), "slim")
            self.assertTrue(offline_skin.has_custom_skin(acc))
            skin_ops.reset_skin(acc)
            self.assertFalse(acc.get("skin_file"))


class TestMicrosoftRequests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.png = Path(self.tmp.name) / "skin.png"
        self.png.write_bytes(make_png(64, 64))
        self.account = {"type": "microsoft", "name": "MS", "uuid": "u",
                        "access_token": "mstok"}

    def tearDown(self):
        self.tmp.cleanup()

    def test_upload(self):
        resp = mock.Mock(status_code=200)
        with mock.patch.object(skin_ops.requests, "post", return_value=resp) as post:
            msg = skin_ops.upload_skin(self.account, str(self.png), "slim")
        self.assertIn("皮肤已更换", msg)
        args, kwargs = post.call_args
        self.assertEqual(args[0], skin_ops.MS_SKIN_URL)
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer mstok")
        self.assertEqual(kwargs["data"]["variant"], "slim")
        self.assertEqual(kwargs["files"]["file"][2], "image/png")

    def test_upload_rejects_hd(self):
        hd = Path(self.tmp.name) / "hd.png"
        hd.write_bytes(make_png(128, 128))
        with mock.patch.object(skin_ops.requests, "post") as post:
            with self.assertRaises(SkinError):
                skin_ops.upload_skin(self.account, str(hd))
        post.assert_not_called()

    def test_reset(self):
        resp = mock.Mock(status_code=200)
        with mock.patch.object(skin_ops.requests, "delete", return_value=resp) as delete:
            msg = skin_ops.reset_skin(self.account)
        self.assertIn("重置", msg)
        self.assertEqual(delete.call_args[0][0], skin_ops.MS_SKIN_RESET_URL)

    def test_401_maps_to_relogin(self):
        resp = mock.Mock(status_code=401)
        with mock.patch.object(skin_ops.requests, "post", return_value=resp):
            with self.assertRaises(SkinError) as ctx:
                skin_ops.upload_skin(self.account, str(self.png))
        self.assertIn("重新登录", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
