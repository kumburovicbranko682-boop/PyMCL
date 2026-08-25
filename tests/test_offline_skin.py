# -*- coding: utf-8 -*-
"""离线账户皮肤：RSA 签名、本地 Yggdrasil 皮肤服务、正版皮肤抓取。"""
import base64
import json
import struct
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mclauncher import offline_skin as osk
from mclauncher.skin import SkinError


def _png_bytes(width: int, height: int, pad: bytes = b"") -> bytes:
    ihdr = struct.pack(">II", width, height) + b"\x08\x06\x00\x00\x00"
    return (b"\x89PNG\r\n\x1a\n"
            + struct.pack(">I", len(ihdr)) + b"IHDR" + ihdr + b"\x00\x00\x00\x00"
            + pad)


# 测试用小密钥：纯 Python 生成 512 位很快，签名逻辑与 2048 位完全一致
KEY = osk.generate_keypair(512)


class RsaTests(unittest.TestCase):
    def test_sign_verify_roundtrip(self):
        data = b"textures-property-value"
        sig = osk.sign_sha1(data, KEY)
        self.assertEqual(len(sig), (KEY["n"].bit_length() + 7) // 8)
        self.assertTrue(osk.verify_sha1(data, sig, KEY))

    def test_verify_rejects_tampered_data(self):
        sig = osk.sign_sha1(b"hello", KEY)
        self.assertFalse(osk.verify_sha1(b"hellx", sig, KEY))

    def test_verify_rejects_tampered_signature(self):
        sig = bytearray(osk.sign_sha1(b"hello", KEY))
        sig[0] ^= 0xFF
        self.assertFalse(osk.verify_sha1(b"hello", bytes(sig), KEY))

    def test_public_key_pem_structure(self):
        pem = osk.public_key_pem(KEY)
        self.assertTrue(pem.startswith("-----BEGIN PUBLIC KEY-----"))
        self.assertTrue(pem.endswith("-----END PUBLIC KEY-----"))
        body = "".join(pem.splitlines()[1:-1])
        der = base64.b64decode(body)
        # SubjectPublicKeyInfo 是 SEQUENCE，内含 rsaEncryption OID
        self.assertEqual(der[0], 0x30)
        self.assertIn(bytes.fromhex("06092a864886f70d010101"), der)

    def test_key_persist_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "key.json"
            k1 = osk.load_or_create_key(path=path, bits=512)
            k2 = osk.load_or_create_key(path=path, bits=512)
            self.assertEqual(k1["n"], k2["n"])
            self.assertEqual(k1["d"], k2["d"])
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(int(data["n"], 16), k1["n"])


class CapeValidationTests(unittest.TestCase):
    def test_valid_cape(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "cape.png"
            p.write_bytes(_png_bytes(64, 32))
            self.assertEqual(osk.validate_cape_png(p), (64, 32))

    def test_not_png(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "cape.png"
            p.write_bytes(b"<html>not a png</html>" + b"\x00" * 40)
            with self.assertRaises(SkinError):
                osk.validate_cape_png(p)

    def test_too_large(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "cape.png"
            p.write_bytes(_png_bytes(64, 32, pad=b"\x00" * (osk.MAX_CAPE_BYTES + 1)))
            with self.assertRaises(SkinError):
                osk.validate_cape_png(p)


class StoreSkinFileTests(unittest.TestCase):
    def test_copies_with_uuid_name(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "in.png"
            src.write_bytes(_png_bytes(64, 64))
            dest = osk.store_skin_file(src, "AA-BB", "skin", dest_dir=Path(td) / "out")
            self.assertTrue(Path(dest).is_file())
            self.assertEqual(Path(dest).name, "aabb-skin.png")
            self.assertEqual(Path(dest).read_bytes(), src.read_bytes())

    def test_rejects_bad_skin(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "in.png"
            src.write_bytes(_png_bytes(48, 48))
            with self.assertRaises(SkinError):
                osk.store_skin_file(src, "u", "skin", dest_dir=td)

    def test_cape_kind_uses_cape_rules(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "in.png"
            src.write_bytes(_png_bytes(22, 17))  # 旧版披风尺寸，皮肤规则会拒绝
            dest = osk.store_skin_file(src, "u", "cape", dest_dir=td)
            self.assertTrue(Path(dest).name.endswith("-cape.png"))


class SkinServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls.skin_png = root / "skin.png"
        cls.skin_png.write_bytes(_png_bytes(64, 64, pad=b"skin-data"))
        cls.cape_png = root / "cape.png"
        cls.cape_png.write_bytes(_png_bytes(64, 32, pad=b"cape-data"))
        cls.server = osk.SkinServer(key=KEY)
        cls.server.register(
            name="Steve", uuid="00112233-4455-6677-8899-aabbccddeeff",
            skin_path=str(cls.skin_png), model="slim", cape_path=str(cls.cape_png))

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()
        cls.tmp.cleanup()

    def _get(self, path: str):
        return urllib.request.urlopen(f"{self.server.url}{path}", timeout=5)

    def test_meta_root(self):
        with self._get("/") as resp:
            meta = json.loads(resp.read())
        self.assertIn("127.0.0.1", meta["skinDomains"])
        self.assertEqual(meta["signaturePublickey"], osk.public_key_pem(KEY))
        self.assertEqual(meta["meta"]["implementationName"], "pymcl-offline-skin")

    def test_profile_signed(self):
        uid = "00112233445566778899aabbccddeeff"
        with self._get(f"/sessionserver/session/minecraft/profile/{uid}?unsigned=false") as resp:
            prof = json.loads(resp.read())
        self.assertEqual(prof["id"], uid)
        self.assertEqual(prof["name"], "Steve")
        prop = prof["properties"][0]
        self.assertEqual(prop["name"], "textures")
        payload = json.loads(base64.b64decode(prop["value"]))
        self.assertEqual(payload["profileId"], uid)
        skin = payload["textures"]["SKIN"]
        self.assertIn("/textures/", skin["url"])
        self.assertEqual(skin["metadata"]["model"], "slim")
        self.assertIn("CAPE", payload["textures"])
        # 签名可用元数据里的公钥验证
        self.assertTrue(osk.verify_sha1(
            prop["value"].encode("ascii"),
            base64.b64decode(prop["signature"]), KEY))

    def test_profile_accepts_dashed_uuid(self):
        with self._get("/sessionserver/session/minecraft/profile/"
                       "00112233-4455-6677-8899-aabbccddeeff") as resp:
            prof = json.loads(resp.read())
        self.assertEqual(prof["name"], "Steve")

    def test_profile_unsigned_by_default(self):
        uid = "00112233445566778899aabbccddeeff"
        with self._get(f"/sessionserver/session/minecraft/profile/{uid}") as resp:
            prof = json.loads(resp.read())
        self.assertNotIn("signature", prof["properties"][0])

    def test_profile_unknown_is_204(self):
        with self._get("/sessionserver/session/minecraft/profile/" + "0" * 32) as resp:
            self.assertEqual(resp.status, 204)

    def test_texture_download(self):
        uid = "00112233445566778899aabbccddeeff"
        with self._get(f"/sessionserver/session/minecraft/profile/{uid}?unsigned=false") as resp:
            prof = json.loads(resp.read())
        payload = json.loads(base64.b64decode(prof["properties"][0]["value"]))
        skin_url = payload["textures"]["SKIN"]["url"]
        with urllib.request.urlopen(skin_url, timeout=5) as resp:
            self.assertEqual(resp.headers["Content-Type"], "image/png")
            self.assertEqual(resp.read(), self.skin_png.read_bytes())
        cape_url = payload["textures"]["CAPE"]["url"]
        with urllib.request.urlopen(cape_url, timeout=5) as resp:
            self.assertEqual(resp.read(), self.cape_png.read_bytes())

    def test_texture_unknown_is_404(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._get("/textures/" + "f" * 64)
        self.assertEqual(ctx.exception.code, 404)

    def test_profiles_by_name(self):
        req = urllib.request.Request(
            f"{self.server.url}/api/profiles/minecraft",
            data=json.dumps(["Steve", "Nobody"]).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=5) as resp:
            rows = json.loads(resp.read())
        self.assertEqual(rows, [{"id": "00112233445566778899aabbccddeeff", "name": "Steve"}])

    def test_join_and_has_joined(self):
        req = urllib.request.Request(
            f"{self.server.url}/sessionserver/session/minecraft/join",
            data=b"{}", headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=5) as resp:
            self.assertEqual(resp.status, 204)
        with self._get("/sessionserver/session/minecraft/hasJoined"
                       "?username=Steve&serverId=abc") as resp:
            prof = json.loads(resp.read())
        self.assertEqual(prof["name"], "Steve")
        self.assertIn("signature", prof["properties"][0])
        with self._get("/sessionserver/session/minecraft/hasJoined"
                       "?username=Nobody&serverId=abc") as resp:
            self.assertEqual(resp.status, 204)

    def test_unknown_path_is_404(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._get("/authserver/authenticate")
        self.assertEqual(ctx.exception.code, 404)


class PrepareInjectionTests(unittest.TestCase):
    def test_no_skin_returns_empty(self):
        self.assertEqual(osk.prepare_injection({"type": "offline", "name": "A", "uuid": "1" * 32}), "")

    def test_non_offline_returns_empty(self):
        acc = {"type": "microsoft", "name": "A", "uuid": "1" * 32, "skin_file": "/tmp/x.png"}
        self.assertEqual(osk.prepare_injection(acc), "")

    def test_missing_file_returns_empty(self):
        acc = {"type": "offline", "name": "A", "uuid": "1" * 32,
               "skin_file": "/nonexistent/skin.png"}
        self.assertEqual(osk.prepare_injection(acc), "")

    def test_registers_and_returns_url(self):
        with tempfile.TemporaryDirectory() as td:
            skin = Path(td) / "skin.png"
            skin.write_bytes(_png_bytes(64, 64))
            server = osk.SkinServer(key=KEY)
            try:
                acc = {"type": "offline", "name": "Herobrine", "uuid": "a" * 32,
                       "skin_file": str(skin), "skin_model": "slim"}
                url = osk.prepare_injection(acc, server=server)
                self.assertEqual(url, server.url)
                prof = server.lookup_name("Herobrine")
                self.assertIsNotNone(prof)
                self.assertEqual(prof["model"], "slim")
                self.assertTrue(prof["skin_hash"])
            finally:
                server.stop()

    def test_untyped_account_counts_as_offline(self):
        # backend.offline_account 的快速启动路径产出的 dict 带 type=offline，
        # 但历史数据可能没有 type 字段——按离线处理
        with tempfile.TemporaryDirectory() as td:
            skin = Path(td) / "skin.png"
            skin.write_bytes(_png_bytes(64, 64))
            server = osk.SkinServer(key=KEY)
            try:
                acc = {"name": "Old", "uuid": "b" * 32, "skin_file": str(skin)}
                self.assertEqual(osk.prepare_injection(acc, server=server), server.url)
            finally:
                server.stop()


class _FakeResponse:
    def __init__(self, status_code=200, payload=None, content=b""):
        self.status_code = status_code
        self._payload = payload
        self.content = content

    def json(self):
        if self._payload is None:
            raise ValueError("no body")
        return self._payload


class _FakeSession:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def get(self, url, timeout=None):
        self.calls.append(url)
        for prefix, resp in self.routes.items():
            if url.startswith(prefix):
                return resp
        return _FakeResponse(404)


def _textures_value(skin_url="", cape_url="", slim=False):
    textures = {}
    if skin_url:
        entry = {"url": skin_url}
        if slim:
            entry["metadata"] = {"model": "slim"}
        textures["SKIN"] = entry
    if cape_url:
        textures["CAPE"] = {"url": cape_url}
    payload = {"timestamp": 0, "profileId": "x", "profileName": "x", "textures": textures}
    return base64.b64encode(json.dumps(payload).encode()).decode()


class FetchPremiumTests(unittest.TestCase):
    def test_fetch_skin_and_cape(self):
        skin_bytes = _png_bytes(64, 64, pad=b"sk")
        cape_bytes = _png_bytes(64, 32, pad=b"cp")
        session = _FakeSession({
            osk.MOJANG_NAME_URL.format(name="Notch"): _FakeResponse(
                200, {"id": "069a79f444e94726a5befca90e38aaf5", "name": "Notch"}),
            osk.MOJANG_PROFILE_URL.format(uuid="069a79f444e94726a5befca90e38aaf5"): _FakeResponse(
                200, {"properties": [{
                    "name": "textures",
                    "value": _textures_value("http://tex/skin", "http://tex/cape", slim=True),
                }]}),
            "http://tex/skin": _FakeResponse(200, content=skin_bytes),
            "http://tex/cape": _FakeResponse(200, content=cape_bytes),
        })
        with tempfile.TemporaryDirectory() as td:
            out = osk.fetch_premium_skin("Notch", "c" * 32, session=session, dest_dir=td)
            self.assertEqual(out["skin_model"], "slim")
            self.assertEqual(Path(out["skin_file"]).read_bytes(), skin_bytes)
            self.assertEqual(Path(out["cape_file"]).read_bytes(), cape_bytes)
            self.assertTrue(Path(out["skin_file"]).name.startswith("c" * 32))

    def test_player_not_found(self):
        session = _FakeSession({
            osk.MOJANG_NAME_URL.format(name="Ghost"): _FakeResponse(404),
        })
        with self.assertRaises(SkinError):
            osk.fetch_premium_skin("Ghost", "d" * 32, session=session)

    def test_player_without_custom_skin(self):
        session = _FakeSession({
            osk.MOJANG_NAME_URL.format(name="Plain"): _FakeResponse(
                200, {"id": "e" * 32, "name": "Plain"}),
            osk.MOJANG_PROFILE_URL.format(uuid="e" * 32): _FakeResponse(
                200, {"properties": [{"name": "textures", "value": _textures_value()}]}),
        })
        with self.assertRaises(SkinError):
            osk.fetch_premium_skin("Plain", "f" * 32, session=session)

    def test_classic_model_and_no_cape(self):
        skin_bytes = _png_bytes(64, 64)
        session = _FakeSession({
            osk.MOJANG_NAME_URL.format(name="Classic"): _FakeResponse(
                200, {"id": "a1" * 16, "name": "Classic"}),
            osk.MOJANG_PROFILE_URL.format(uuid="a1" * 16): _FakeResponse(
                200, {"properties": [{"name": "textures",
                                      "value": _textures_value("http://tex/skin")}]}),
            "http://tex/skin": _FakeResponse(200, content=skin_bytes),
        })
        with tempfile.TemporaryDirectory() as td:
            out = osk.fetch_premium_skin("Classic", "1" * 32, session=session, dest_dir=td)
            self.assertEqual(out["skin_model"], "default")
            self.assertEqual(out["cape_file"], "")

    def test_empty_name_rejected(self):
        with self.assertRaises(SkinError):
            osk.fetch_premium_skin("  ", "x", session=_FakeSession({}))


if __name__ == "__main__":
    unittest.main()
