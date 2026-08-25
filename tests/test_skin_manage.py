from __future__ import annotations

import struct
import tempfile
import unittest
from pathlib import Path

from mclauncher import skin


def _png_bytes(width: int, height: int, pad: bytes = b"") -> bytes:
    """构造带合法签名和 IHDR 的 PNG 头（校验只解析头部）。"""
    ihdr = struct.pack(">II", width, height) + b"\x08\x06\x00\x00\x00"
    return (b"\x89PNG\r\n\x1a\n"
            + struct.pack(">I", len(ihdr)) + b"IHDR" + ihdr + b"\x00\x00\x00\x00"
            + pad)


class _FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("no body")
        return self._payload


PROFILE = {
    "id": "069a79f444e94726a5befca90e38aaf5",
    "name": "Notch",
    "skins": [{
        "id": "s1", "state": "ACTIVE",
        "url": "http://textures.minecraft.net/texture/abc",
        "variant": "SLIM",
    }],
    "capes": [
        {"id": "c1", "state": "ACTIVE", "url": "http://x/c1", "alias": "Migrator"},
        {"id": "c2", "state": "INACTIVE", "url": "http://x/c2", "alias": "Vanilla"},
    ],
}


class _FakeSession:
    """记录请求并按队列回放响应。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def _pop(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        return self.responses.pop(0)

    def get(self, url, **kw):
        return self._pop("GET", url, **kw)

    def post(self, url, **kw):
        return self._pop("POST", url, **kw)

    def put(self, url, **kw):
        return self._pop("PUT", url, **kw)

    def delete(self, url, **kw):
        return self._pop("DELETE", url, **kw)


class ValidatePngTests(unittest.TestCase):
    def _write(self, data: bytes) -> Path:
        f = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        f.write(data)
        f.close()
        self.addCleanup(Path(f.name).unlink)
        return Path(f.name)

    def test_accepts_64x64(self):
        path = self._write(_png_bytes(64, 64))
        self.assertEqual(skin.validate_skin_png(path), (64, 64))

    def test_accepts_legacy_64x32(self):
        path = self._write(_png_bytes(64, 32))
        self.assertEqual(skin.validate_skin_png(path), (64, 32))

    def test_rejects_wrong_dimensions(self):
        path = self._write(_png_bytes(128, 128))
        with self.assertRaises(skin.SkinError) as ctx:
            skin.validate_skin_png(path)
        self.assertIn("128x128", str(ctx.exception))

    def test_rejects_non_png(self):
        path = self._write(b"\xff\xd8\xff\xe0 not a png" + b"\x00" * 40)
        with self.assertRaises(skin.SkinError):
            skin.validate_skin_png(path)

    def test_rejects_missing_file(self):
        with self.assertRaises(skin.SkinError):
            skin.validate_skin_png(Path(tempfile.gettempdir()) / "no-such-skin.png")

    def test_rejects_oversized_file(self):
        path = self._write(_png_bytes(64, 64, pad=b"\x00" * (skin.MAX_SKIN_BYTES + 1)))
        with self.assertRaises(skin.SkinError) as ctx:
            skin.validate_skin_png(path)
        self.assertIn("过大", str(ctx.exception))


class ProfileApiTests(unittest.TestCase):
    def test_fetch_profile_ok(self):
        sess = _FakeSession([_FakeResponse(200, PROFILE)])
        data = skin.fetch_profile("tok", session=sess)
        self.assertEqual(data["name"], "Notch")
        call = sess.calls[0]
        self.assertEqual(call["method"], "GET")
        self.assertEqual(call["url"], skin.PROFILE_URL)
        self.assertEqual(call["headers"]["Authorization"], "Bearer tok")

    def test_fetch_profile_no_profile(self):
        sess = _FakeSession([_FakeResponse(404)])
        with self.assertRaises(skin.SkinError):
            skin.fetch_profile("tok", session=sess)

    def test_upload_skin_builds_multipart(self):
        f = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        f.write(_png_bytes(64, 64))
        f.close()
        self.addCleanup(Path(f.name).unlink)
        sess = _FakeSession([_FakeResponse(200, PROFILE)])
        out = skin.upload_skin("tok", f.name, "slim", session=sess)
        self.assertEqual(out["name"], "Notch")
        call = sess.calls[0]
        self.assertEqual(call["method"], "POST")
        self.assertEqual(call["url"], skin.SKIN_URL)
        self.assertEqual(call["data"], {"variant": "slim"})
        name, blob, mime = call["files"]["file"]
        self.assertTrue(name.endswith(".png"))
        self.assertTrue(blob.startswith(b"\x89PNG"))
        self.assertEqual(mime, "image/png")

    def test_upload_skin_rejects_bad_variant(self):
        with self.assertRaises(skin.SkinError):
            skin.upload_skin("tok", "whatever.png", "wide", session=_FakeSession([]))

    def test_upload_skin_maps_401(self):
        f = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        f.write(_png_bytes(64, 64))
        f.close()
        self.addCleanup(Path(f.name).unlink)
        sess = _FakeSession([_FakeResponse(401)])
        with self.assertRaises(skin.SkinError) as ctx:
            skin.upload_skin("tok", f.name, session=sess)
        self.assertIn("重新登录", str(ctx.exception))

    def test_reset_skin_refetches_on_empty_body(self):
        sess = _FakeSession([_FakeResponse(204), _FakeResponse(200, PROFILE)])
        out = skin.reset_skin("tok", session=sess)
        self.assertEqual(out["name"], "Notch")
        self.assertEqual(sess.calls[0]["method"], "DELETE")
        self.assertEqual(sess.calls[0]["url"], skin.SKIN_ACTIVE_URL)
        self.assertEqual(sess.calls[1]["method"], "GET")

    def test_set_cape_put(self):
        sess = _FakeSession([_FakeResponse(200, PROFILE)])
        skin.set_cape("tok", "c2", session=sess)
        call = sess.calls[0]
        self.assertEqual(call["method"], "PUT")
        self.assertEqual(call["url"], skin.CAPE_ACTIVE_URL)
        self.assertEqual(call["json"], {"capeId": "c2"})

    def test_set_cape_hide(self):
        sess = _FakeSession([_FakeResponse(200, PROFILE)])
        skin.set_cape("tok", "", session=sess)
        call = sess.calls[0]
        self.assertEqual(call["method"], "DELETE")
        self.assertEqual(call["url"], skin.CAPE_ACTIVE_URL)


class SummarizeTests(unittest.TestCase):
    def test_summarize(self):
        out = skin.summarize_profile(PROFILE)
        self.assertEqual(out["name"], "Notch")
        self.assertEqual(out["uuid"], "069a79f4-44e9-4726-a5be-fca90e38aaf5")
        self.assertEqual(out["variant"], "slim")
        self.assertEqual(out["skin_url"], "http://textures.minecraft.net/texture/abc")
        self.assertEqual(out["active_cape"], "c1")
        self.assertEqual(len(out["capes"]), 2)
        self.assertTrue(out["capes"][0]["active"])
        self.assertFalse(out["capes"][1]["active"])

    def test_summarize_empty(self):
        out = skin.summarize_profile({})
        self.assertEqual(out["skin_url"], "")
        self.assertEqual(out["variant"], "classic")
        self.assertEqual(out["capes"], [])
        self.assertEqual(out["active_cape"], "")

    def test_skin_site_url(self):
        acc = {"type": "authlib", "api": "https://littleskin.cn/api/yggdrasil"}
        self.assertEqual(skin.skin_site_url(acc), "https://littleskin.cn")
        self.assertEqual(skin.skin_site_url({"type": "microsoft"}), "")
        self.assertEqual(skin.skin_site_url(None), "")


if __name__ == "__main__":
    unittest.main()
