# -*- coding: utf-8 -*-
"""离线账号本地皮肤：RSA 密钥、迷你 Yggdrasil 服务、textures 签名。"""
from __future__ import annotations

import base64
import json
import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest import mock

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mclauncher import offline_skin

UUID = "069a79f4-44e9-4726-a5be-fca90e38aaf5"
NODASH = UUID.replace("-", "")


def _make_png(width=64, height=64) -> bytes:
    def chunk(typ, data):
        body = typ + data
        return (struct.pack(">I", len(data)) + body
                + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    raw = b"".join(b"\x00" + b"\x12\x34\x56\xff" * width for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


class OfflineSkinTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.skin_png = root / "skin.png"
        self.skin_png.write_bytes(_make_png())
        self._patches = [
            mock.patch.object(offline_skin, "KEY_FILE", root / "key.pem"),
            mock.patch.object(offline_skin, "SKIN_DIR", root / "skins"),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self.tmp.cleanup()


class KeyTest(OfflineSkinTestBase):
    def test_key_roundtrip(self):
        k1 = offline_skin.load_or_create_key()
        self.assertTrue(offline_skin.KEY_FILE.is_file())
        k2 = offline_skin.load_or_create_key()
        self.assertEqual(offline_skin.public_key_pem(k1),
                         offline_skin.public_key_pem(k2))
        self.assertIn("BEGIN PUBLIC KEY", offline_skin.public_key_pem(k1))


class StoreSkinTest(OfflineSkinTestBase):
    def test_store_dedupes_by_content(self):
        p1 = offline_skin.store_skin(self.skin_png)
        p2 = offline_skin.store_skin(self.skin_png)
        self.assertEqual(p1, p2)
        self.assertTrue(p1.is_file())

    def test_rejects_bad_size(self):
        bad = Path(self.tmp.name) / "bad.png"
        bad.write_bytes(_make_png(32, 32))
        from mclauncher.skin import SkinError
        with self.assertRaises(SkinError):
            offline_skin.store_skin(bad)


class ServerTest(OfflineSkinTestBase):
    def setUp(self):
        super().setUp()
        self.srv = offline_skin.serve_for_account(
            "Alice", UUID, self.skin_png, model="classic")
        self.base = self.srv.api_root()

    def tearDown(self):
        self.srv.stop()
        super().tearDown()

    def test_metadata(self):
        meta = requests.get(self.base + "/", timeout=5).json()
        self.assertIn("127.0.0.1", meta["skinDomains"])
        self.assertIn("BEGIN PUBLIC KEY", meta["signaturePublickey"])
        self.assertEqual(meta["meta"]["implementationName"], "pymcl-offline-skin")

    def test_profile_signed_and_texture_served(self):
        resp = requests.get(
            f"{self.base}/sessionserver/session/minecraft/profile/{NODASH}",
            params={"unsigned": "false"}, timeout=5)
        self.assertEqual(resp.status_code, 200)
        prof = resp.json()
        self.assertEqual(prof["id"], NODASH)
        self.assertEqual(prof["name"], "Alice")
        prop = prof["properties"][0]
        self.assertEqual(prop["name"], "textures")
        payload = json.loads(base64.b64decode(prop["value"]))
        self.assertEqual(payload["profileName"], "Alice")
        skin_url = payload["textures"]["SKIN"]["url"]
        self.assertNotIn("metadata", payload["textures"]["SKIN"])  # classic 不带 slim 标记
        # 皮肤文件能按 URL 取回且内容一致
        tex = requests.get(skin_url, timeout=5)
        self.assertEqual(tex.status_code, 200)
        self.assertEqual(tex.content, self.skin_png.read_bytes())
        # 签名可用元数据公钥验证（游戏侧 authlib 同款校验）
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
        meta = requests.get(self.base + "/", timeout=5).json()
        pub = serialization.load_pem_public_key(meta["signaturePublickey"].encode())
        pub.verify(base64.b64decode(prop["signature"]),
                   prop["value"].encode("ascii"),
                   padding.PKCS1v15(), hashes.SHA1())

    def test_profile_accepts_dashed_uuid(self):
        resp = requests.get(
            f"{self.base}/sessionserver/session/minecraft/profile/{UUID}", timeout=5)
        self.assertEqual(resp.status_code, 200)

    def test_unknown_profile_204(self):
        resp = requests.get(
            f"{self.base}/sessionserver/session/minecraft/profile/{'0' * 32}", timeout=5)
        self.assertEqual(resp.status_code, 204)

    def test_name_lookup(self):
        resp = requests.post(
            f"{self.base}/api/profiles/minecraft",
            json=["Alice", "Nobody"], timeout=5)
        self.assertEqual(resp.json(), [{"id": NODASH, "name": "Alice"}])

    def test_join_and_has_joined(self):
        resp = requests.post(
            f"{self.base}/sessionserver/session/minecraft/join",
            json={"accessToken": "0", "selectedProfile": NODASH,
                  "serverId": "x"}, timeout=5)
        self.assertEqual(resp.status_code, 204)
        resp = requests.get(
            f"{self.base}/sessionserver/session/minecraft/hasJoined",
            params={"username": "Alice", "serverId": "x"}, timeout=5)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["name"], "Alice")
        resp = requests.get(
            f"{self.base}/sessionserver/session/minecraft/hasJoined",
            params={"username": "Nobody", "serverId": "x"}, timeout=5)
        self.assertEqual(resp.status_code, 204)


class SlimModelTest(OfflineSkinTestBase):
    def test_slim_metadata(self):
        srv = offline_skin.serve_for_account("Bob", UUID, self.skin_png, model="slim")
        try:
            resp = requests.get(
                f"{srv.api_root()}/sessionserver/session/minecraft/profile/{NODASH}",
                timeout=5)
            payload = json.loads(base64.b64decode(
                resp.json()["properties"][0]["value"]))
            self.assertEqual(payload["textures"]["SKIN"]["metadata"]["model"], "slim")
        finally:
            srv.stop()


class FetchTextureTest(OfflineSkinTestBase):
    def test_offline_account_local_texture(self):
        from mclauncher import skin as skin_mod
        acc = {"type": "offline", "name": "Alice", "uuid": UUID,
               "skin_file": str(self.skin_png), "skin_model": "slim"}
        data = skin_mod.fetch_skin_texture(acc)
        self.assertEqual(data["variant"], "slim")
        self.assertEqual(data["png"], self.skin_png.read_bytes())

    def test_offline_account_without_skin_raises(self):
        from mclauncher import skin as skin_mod
        with self.assertRaises(skin_mod.SkinError):
            skin_mod.fetch_skin_texture({"type": "offline", "name": "Alice"})


if __name__ == "__main__":
    unittest.main()
