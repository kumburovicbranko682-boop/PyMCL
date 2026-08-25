# -*- coding: utf-8 -*-
"""离线自定义皮肤：RSA 签名、账号字段、本地 Yggdrasil 纹理服务。"""
from __future__ import annotations

import base64
import json
import struct
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from mclauncher import offline_skin, rsa_lite, utils
from mclauncher.skin_ops import SkinError


def make_png(width: int = 64, height: int = 64, marker: bytes = b"") -> bytes:
    """能过 IHDR 尺寸校验的最小 PNG 字节。"""
    ihdr = struct.pack(">II", width, height) + b"\x08\x06\x00\x00\x00"
    return (b"\x89PNG\r\n\x1a\n"
            + struct.pack(">I", len(ihdr)) + b"IHDR" + ihdr + b"\x00" * 4
            + marker)


# 全套测试共用一把小密钥，避免反复生成 2048 位拖慢用例
KEY_1024 = rsa_lite.generate(1024)


def http_get(url: str):
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def http_post(url: str, payload):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


class TestRsaLite(unittest.TestCase):
    def test_sign_verify_roundtrip(self):
        data = b"pymcl offline skin"
        sig = rsa_lite.sign_sha1(data, KEY_1024)
        self.assertEqual(len(sig), 128)
        self.assertTrue(rsa_lite.verify_sha1(data, sig, KEY_1024))

    def test_tampered_data_fails(self):
        sig = rsa_lite.sign_sha1(b"hello", KEY_1024)
        self.assertFalse(rsa_lite.verify_sha1(b"hellx", sig, KEY_1024))
        self.assertFalse(rsa_lite.verify_sha1(b"hello", sig[:-1] + b"\x00", KEY_1024))

    def test_public_pem_shape(self):
        pem = rsa_lite.public_pem(KEY_1024)
        self.assertTrue(pem.startswith("-----BEGIN PUBLIC KEY-----\n"))
        self.assertTrue(pem.rstrip().endswith("-----END PUBLIC KEY-----"))
        der = rsa_lite.public_der(KEY_1024)
        self.assertEqual(der[0], 0x30)

    def test_interop_with_cryptography(self):
        """有 cryptography 时做一次真实验签（对应 Java 端 SHA1withRSA）。"""
        try:
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import padding
        except ImportError:
            self.skipTest("cryptography 不可用")
        pub = serialization.load_pem_public_key(
            rsa_lite.public_pem(KEY_1024).encode("ascii"))
        data = b"texture-property-value"
        sig = rsa_lite.sign_sha1(data, KEY_1024)
        pub.verify(sig, data, padding.PKCS1v15(), hashes.SHA1())  # 失败会抛异常

    def test_load_or_create_persists(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "key.json"
            with mock.patch.object(rsa_lite, "generate", return_value=dict(KEY_1024)):
                k1 = rsa_lite.load_or_create(path)
            self.assertTrue(path.is_file())
            k2 = rsa_lite.load_or_create(path)  # 第二次直接读文件
            self.assertEqual(k1["n"], k2["n"])
            path.write_text("{broken", encoding="utf-8")
            with mock.patch.object(rsa_lite, "generate", return_value=dict(KEY_1024)):
                k3 = rsa_lite.load_or_create(path)
            self.assertEqual(k3["n"], KEY_1024["n"])


class RootSandbox(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        patcher = mock.patch.object(utils, "ROOT", Path(self.tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)


class TestAccountFields(RootSandbox):
    def test_import_skin_content_addressed(self):
        png = make_png(marker=b"one")
        src = Path(self.tmp.name) / "src.png"
        src.write_bytes(png)
        fields = offline_skin.import_skin(str(src), "slim")
        self.assertEqual(fields["skin_model"], "slim")
        dest = Path(fields["skin_file"])
        self.assertTrue(dest.is_file())
        self.assertEqual(dest.read_bytes(), png)
        # 同一内容再导入指向同一文件
        again = offline_skin.import_skin(str(src), "classic")
        self.assertEqual(again["skin_file"], fields["skin_file"])

    def test_import_rejects_bad_file(self):
        bad = Path(self.tmp.name) / "bad.png"
        bad.write_bytes(b"not a png")
        with self.assertRaises(SkinError):
            offline_skin.import_skin(str(bad))
        with self.assertRaises(SkinError):
            offline_skin.import_skin(str(Path(self.tmp.name) / "missing.png"))

    def test_apply_and_clear(self):
        src = Path(self.tmp.name) / "s.png"
        src.write_bytes(make_png())
        acc = {"type": "offline", "name": "Steve", "uuid": ""}
        msg = offline_skin.apply_to_account(acc, str(src), "alex")
        self.assertIn("纤细", msg)
        self.assertTrue(offline_skin.has_custom_skin(acc))
        self.assertEqual(acc["skin_model"], "slim")
        offline_skin.clear_account(acc)
        self.assertFalse(offline_skin.has_custom_skin(acc))
        self.assertNotIn("skin_file", acc)

    def test_apply_rejects_online_account(self):
        src = Path(self.tmp.name) / "s.png"
        src.write_bytes(make_png())
        with self.assertRaises(SkinError):
            offline_skin.apply_to_account({"type": "microsoft"}, str(src))

    def test_launch_profile(self):
        src = Path(self.tmp.name) / "s.png"
        png = make_png(marker=b"xyz")
        src.write_bytes(png)
        acc = {"type": "offline", "name": "Tester", "uuid": "",
               "skin_file": str(src), "skin_model": "slim"}
        prof = offline_skin.launch_profile(acc)
        self.assertEqual(prof["name"], "Tester")
        self.assertEqual(prof["skin"], png)
        self.assertEqual(prof["model"], "slim")
        self.assertEqual(len(prof["uuid"]), 32)
        # 微软账号 / 无皮肤 / 文件缺失 → None
        self.assertIsNone(offline_skin.launch_profile({"type": "microsoft"}))
        self.assertIsNone(offline_skin.launch_profile(
            {"type": "offline", "name": "A"}))
        self.assertIsNone(offline_skin.launch_profile(
            {"type": "offline", "name": "A",
             "skin_file": str(Path(self.tmp.name) / "gone.png")}))


class TestServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.png = make_png(marker=b"srv")
        cls.uuid = "0123456789abcdef0123456789abcdef"
        cls.server = offline_skin.OfflineSkinServer(
            [{"name": "Tester", "uuid": cls.uuid,
              "skin": cls.png, "model": "slim"}],
            key=KEY_1024)
        cls.server.start()
        cls.root = cls.server.api_root

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    def test_metadata(self):
        code, headers, body = http_get(self.root + "/")
        self.assertEqual(code, 200)
        self.assertIn("application/json", headers.get("Content-Type", ""))
        meta = json.loads(body)
        self.assertIn("127.0.0.1", meta["skinDomains"])
        self.assertIn("BEGIN PUBLIC KEY", meta["signaturePublickey"])
        self.assertEqual(meta["meta"]["implementationName"], "pymcl-offline-skin")

    def test_profile_signed(self):
        code, _h, body = http_get(
            f"{self.root}/sessionserver/session/minecraft/profile/{self.uuid}?unsigned=false")
        self.assertEqual(code, 200)
        prof = json.loads(body)
        self.assertEqual(prof["id"], self.uuid)
        self.assertEqual(prof["name"], "Tester")
        prop = prof["properties"][0]
        self.assertEqual(prop["name"], "textures")
        value = json.loads(base64.b64decode(prop["value"]))
        skin = value["textures"]["SKIN"]
        self.assertTrue(skin["url"].startswith(self.root + "/textures/"))
        self.assertEqual(skin["metadata"]["model"], "slim")
        self.assertEqual(value["profileName"], "Tester")
        sig = base64.b64decode(prop["signature"])
        self.assertTrue(rsa_lite.verify_sha1(
            prop["value"].encode("ascii"), sig, KEY_1024))

    def test_profile_dashed_uuid_and_unsigned(self):
        dashed = f"{self.uuid[:8]}-{self.uuid[8:12]}-{self.uuid[12:16]}-{self.uuid[16:20]}-{self.uuid[20:]}"
        code, _h, body = http_get(
            f"{self.root}/sessionserver/session/minecraft/profile/{dashed}")
        self.assertEqual(code, 200)
        prop = json.loads(body)["properties"][0]
        self.assertNotIn("signature", prop)

    def test_profile_unknown_204(self):
        code, _h, _b = http_get(
            f"{self.root}/sessionserver/session/minecraft/profile/{'f' * 32}")
        self.assertEqual(code, 204)

    def test_textures(self):
        code, _h, body = http_get(
            f"{self.root}/sessionserver/session/minecraft/profile/{self.uuid}")
        prop = json.loads(body)["properties"][0]
        url = json.loads(base64.b64decode(prop["value"]))["textures"]["SKIN"]["url"]
        code, headers, data = http_get(url)
        self.assertEqual(code, 200)
        self.assertEqual(headers.get("Content-Type"), "image/png")
        self.assertEqual(data, self.png)
        code, _h, _b = http_get(f"{self.root}/textures/{'0' * 64}")
        self.assertEqual(code, 404)

    def test_join_and_has_joined(self):
        code, _b = http_post(
            f"{self.root}/sessionserver/session/minecraft/join",
            {"accessToken": "0", "selectedProfile": self.uuid, "serverId": "abc"})
        self.assertEqual(code, 204)
        code, _h, body = http_get(
            f"{self.root}/sessionserver/session/minecraft/hasJoined?username=Tester&serverId=abc")
        self.assertEqual(code, 200)
        prof = json.loads(body)
        self.assertEqual(prof["id"], self.uuid)
        self.assertIn("signature", prof["properties"][0])
        code, _h, _b = http_get(
            f"{self.root}/sessionserver/session/minecraft/hasJoined?username=Nobody&serverId=abc")
        self.assertEqual(code, 204)

    def test_profiles_lookup(self):
        code, body = http_post(
            f"{self.root}/api/profiles/minecraft", ["Tester", "Ghost"])
        self.assertEqual(code, 200)
        rows = json.loads(body)
        self.assertEqual(rows, [{"id": self.uuid, "name": "Tester"}])

    def test_unknown_route_404(self):
        code, _h, _b = http_get(f"{self.root}/nope")
        self.assertEqual(code, 404)


class TestServeForAccount(RootSandbox):
    def test_offline_with_skin(self):
        src = Path(self.tmp.name) / "s.png"
        src.write_bytes(make_png())
        acc = {"type": "offline", "name": "Steve", "uuid": "",
               "skin_file": str(src), "skin_model": "classic"}
        with mock.patch.object(offline_skin, "_signing_key",
                               return_value=dict(KEY_1024)):
            server = offline_skin.serve_for_account(acc)
        self.assertIsNotNone(server)
        try:
            code, _h, body = http_get(server.api_root + "/")
            self.assertEqual(code, 200)
            self.assertIn("skinDomains", json.loads(body))
        finally:
            server.stop()

    def test_no_server_when_not_applicable(self):
        self.assertIsNone(offline_skin.serve_for_account(
            {"type": "microsoft", "name": "x"}))
        self.assertIsNone(offline_skin.serve_for_account(
            {"type": "offline", "name": "x"}))


if __name__ == "__main__":
    unittest.main()
