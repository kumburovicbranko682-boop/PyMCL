# -*- coding: utf-8 -*-
"""玩家档案查询（PCL2 百宝箱「IGN / UUID 查询」同款）。

全部走假会话，不碰真网络。覆盖：按名/按 UUID 查询、纹理解码（模型/披风）、
非法输入、404/429 错误路径、两个门面。
"""
import base64
import json
import unittest
from unittest.mock import patch

from mclauncher import skin
from mclauncher.skin import SkinError, lookup_player

RAW_UUID = "069a79f444e94726a5befca90e38aaf5"
DASHED = "069a79f4-44e9-4726-a5be-fca90e38aaf5"


def _textures_b64(skin_url="http://textures.minecraft.net/texture/abc",
                  slim=False, cape_url=""):
    tex = {"textures": {}}
    if skin_url:
        entry = {"url": skin_url}
        if slim:
            entry["metadata"] = {"model": "slim"}
        tex["textures"]["SKIN"] = entry
    if cape_url:
        tex["textures"]["CAPE"] = {"url": cape_url}
    return base64.b64encode(json.dumps(tex).encode()).decode()


class _Resp:
    def __init__(self, code, data=None):
        self.status_code = code
        self._data = data

    def json(self):
        return self._data


class _Session:
    """按 URL 分发的假会话。"""

    def __init__(self, name_resp=None, profile_resp=None):
        self.name_resp = name_resp
        self.profile_resp = profile_resp
        self.calls = []

    def get(self, url, timeout=0):
        self.calls.append(url)
        if "api.mojang.com" in url:
            return self.name_resp
        return self.profile_resp


def _profile(name="Notch", slim=False, cape=""):
    return _Resp(200, {
        "id": RAW_UUID, "name": name,
        "properties": [{"name": "textures",
                        "value": _textures_b64(slim=slim, cape_url=cape)}],
    })


class LookupTests(unittest.TestCase):
    def test_by_name(self):
        s = _Session(_Resp(200, {"id": RAW_UUID, "name": "Notch"}), _profile())
        out = lookup_player("Notch", session=s)
        self.assertEqual(out["name"], "Notch")
        self.assertEqual(out["uuid"], DASHED)
        self.assertEqual(out["variant"], "classic")
        self.assertEqual(out["cape_url"], "")
        self.assertIn("textures.minecraft.net", out["skin_url"])
        self.assertIn(RAW_UUID, out["avatar"])
        self.assertIn(RAW_UUID, out["body"])
        self.assertEqual(len(s.calls), 2)

    def test_by_uuid_skips_name_endpoint(self):
        for q in (RAW_UUID, DASHED, RAW_UUID.upper()):
            s = _Session(None, _profile())
            out = lookup_player(q, session=s)
            self.assertEqual(out["uuid"], DASHED)
            self.assertEqual(len(s.calls), 1)
            self.assertIn("sessionserver", s.calls[0])

    def test_slim_and_cape(self):
        s = _Session(None, _profile(slim=True, cape="http://tex/cape"))
        out = lookup_player(RAW_UUID, session=s)
        self.assertEqual(out["variant"], "slim")
        self.assertEqual(out["cape_url"], "http://tex/cape")

    def test_invalid_input_no_network(self):
        s = _Session()
        for bad in ("", "  ", "has space", "太长" * 10, "a" * 17, "emoji😀"):
            with self.assertRaises(SkinError):
                lookup_player(bad, session=s)
        self.assertEqual(s.calls, [])

    def test_name_not_found(self):
        for code in (204, 404):
            s = _Session(_Resp(code), None)
            with self.assertRaises(SkinError) as ctx:
                lookup_player("Ghost", session=s)
            self.assertIn("找不到", str(ctx.exception))

    def test_rate_limited(self):
        s = _Session(_Resp(429), None)
        with self.assertRaises(SkinError) as ctx:
            lookup_player("Notch", session=s)
        self.assertIn("频繁", str(ctx.exception))

    def test_uuid_profile_not_found(self):
        s = _Session(None, _Resp(404))
        with self.assertRaises(SkinError):
            lookup_player(RAW_UUID, session=s)

    def test_broken_textures_property_tolerated(self):
        resp = _Resp(200, {"id": RAW_UUID, "name": "N",
                           "properties": [{"name": "textures", "value": "!!!"}]})
        out = lookup_player(RAW_UUID, session=_Session(None, resp))
        self.assertEqual(out["skin_url"], "")
        self.assertEqual(out["variant"], "classic")


class FacadeTests(unittest.TestCase):
    def test_both_facades_delegate(self):
        fake = {"name": "N", "uuid": DASHED}
        with patch.object(skin, "lookup_player", lambda q: fake):
            from bridge.api import BackendAPI

            class _Bus:
                def emit(self, *a, **k):
                    pass

            self.assertEqual(BackendAPI(_Bus()).lookup_player("N"), fake)
            from app.backend import BackendAPI as QtBackend
            self.assertEqual(QtBackend.lookup_player(None, "N"), fake)


if __name__ == "__main__":
    unittest.main()
