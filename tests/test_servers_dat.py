# -*- coding: utf-8 -*-
"""服务器列表写入游戏真实读取的 servers.dat（NBT）。"""
from __future__ import annotations

import gzip
import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mclauncher import nbt_lite as nbt
from mclauncher import servers, utils
from mclauncher.instances import Instance


def vanilla_servers_dat(entries) -> bytes:
    """按原版游戏的写法手工构造 servers.dat 字节，独立于我们的 writer。"""

    def s(text: str) -> bytes:
        raw = text.encode("utf-8")
        return struct.pack(">H", len(raw)) + raw

    body = b""
    for e in entries:
        comp = b""
        for key, val in e.items():
            if isinstance(val, str):
                comp += b"\x08" + s(key) + s(val)
            else:  # byte
                comp += b"\x01" + s(key) + struct.pack(">b", val)
        body += comp + b"\x00"
    out = b"\x0a" + s("")                                  # root compound ""
    out += b"\x09" + s("servers")                          # TAG_List "servers"
    out += b"\x0a" + struct.pack(">i", len(entries))       # elem=compound, count
    out += body
    out += b"\x00"                                          # root TAG_End
    return out


class TestNbtLite(unittest.TestCase):
    def test_roundtrip_all_types(self):
        root = {
            "b": (nbt.TAG_BYTE, -3),
            "h": (nbt.TAG_SHORT, 1234),
            "i": (nbt.TAG_INT, -56789),
            "l": (nbt.TAG_LONG, 2**40),
            "f": (nbt.TAG_FLOAT, 1.5),
            "d": (nbt.TAG_DOUBLE, 3.25),
            "ba": (nbt.TAG_BYTE_ARRAY, b"\x01\x02\x03"),
            "s": (nbt.TAG_STRING, "你好 world"),
            "lst": (nbt.TAG_LIST, (nbt.TAG_INT, [1, 2, 3])),
            "comp": (nbt.TAG_COMPOUND, {"x": (nbt.TAG_STRING, "y")}),
            "ia": (nbt.TAG_INT_ARRAY, [7, 8]),
            "la": (nbt.TAG_LONG_ARRAY, [2**33]),
        }
        name, parsed = nbt.loads(nbt.dumps(root, "root"))
        self.assertEqual(name, "root")
        self.assertEqual(parsed, root)

    def test_reads_vanilla_bytes(self):
        raw = vanilla_servers_dat([
            {"name": "Hypixel", "ip": "mc.hypixel.net", "acceptTextures": 1},
        ])
        name, root = nbt.loads(raw)
        self.assertEqual(name, "")
        elem_type, items = root["servers"][1]
        self.assertEqual(elem_type, nbt.TAG_COMPOUND)
        self.assertEqual(items[0]["name"], (nbt.TAG_STRING, "Hypixel"))
        self.assertEqual(items[0]["acceptTextures"], (nbt.TAG_BYTE, 1))

    def test_reads_gzip(self):
        raw = gzip.compress(vanilla_servers_dat([{"name": "n", "ip": "a"}]))
        _name, root = nbt.loads(raw)
        self.assertEqual(len(root["servers"][1][1]), 1)


class TestAddress(unittest.TestCase):
    def test_split(self):
        self.assertEqual(servers._split_address("mc.example.com"), ("mc.example.com", 25565))
        self.assertEqual(servers._split_address("mc.example.com:25566"), ("mc.example.com", 25566))
        self.assertEqual(servers._split_address("[2001:db8::1]:25570"), ("2001:db8::1", 25570))
        self.assertEqual(servers._split_address("2001:db8::1"), ("2001:db8::1", 25565))

    def test_join(self):
        self.assertEqual(servers._join_address("mc.example.com", 25565), "mc.example.com")
        self.assertEqual(servers._join_address("mc.example.com", 25566), "mc.example.com:25566")
        self.assertEqual(servers._join_address("2001:db8::1", 25570), "[2001:db8::1]:25570")


class Sandbox(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        patcher = mock.patch(
            "mclauncher.instances.get_instance_path",
            side_effect=lambda name: self.root / "instances" / name)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def make_instance(self, name="srv") -> Instance:
        inst = Instance(name)
        inst.create()
        return inst


class TestServersDat(Sandbox):
    def test_add_writes_game_readable_dat(self):
        inst = self.make_instance()
        servers.add_server(inst, "Hypixel", "mc.hypixel.net")
        servers.add_server(inst, "本地", "127.0.0.1", port=25566)
        dat = inst.path / "servers.dat"
        self.assertTrue(dat.is_file())
        # 直接读字节验证游戏格式（不经过我们的高层 API）
        _name, root = nbt.loads(dat.read_bytes())
        elem_type, items = root["servers"][1]
        self.assertEqual(elem_type, nbt.TAG_COMPOUND)
        self.assertEqual(items[0]["ip"], (nbt.TAG_STRING, "mc.hypixel.net"))
        self.assertEqual(items[1]["ip"], (nbt.TAG_STRING, "127.0.0.1:25566"))

        rows = servers.list_servers(inst)
        self.assertEqual([r["name"] for r in rows], ["Hypixel", "本地"])
        self.assertEqual(rows[1]["port"], 25566)

    def test_update_and_delete(self):
        inst = self.make_instance("upd")
        servers.add_server(inst, "A", "a.example.com")
        servers.add_server(inst, "B", "b.example.com")
        servers.update_server(inst, 0, name="A2", port=25577, hidden=True)
        rows = servers.list_servers(inst)
        self.assertEqual(rows[0]["name"], "A2")
        self.assertEqual(rows[0]["port"], 25577)
        self.assertTrue(rows[0]["hidden"])
        servers.delete_server(inst, 0)
        rows = servers.list_servers(inst)
        self.assertEqual([r["name"] for r in rows], ["B"])

    def test_preserves_vanilla_extra_fields(self):
        inst = self.make_instance("extra")
        raw = vanilla_servers_dat([
            {"name": "Game", "ip": "srv.example.com", "acceptTextures": 1},
        ])
        (inst.path / "servers.dat").write_bytes(raw)
        # 启动器改名后，游戏写的 acceptTextures 字段不能丢
        servers.update_server(inst, 0, name="Game2")
        _n, root = nbt.loads((inst.path / "servers.dat").read_bytes())
        comp = root["servers"][1][1][0]
        self.assertEqual(comp["name"], (nbt.TAG_STRING, "Game2"))
        self.assertEqual(comp["acceptTextures"], (nbt.TAG_BYTE, 1))

    def test_migrates_legacy_json(self):
        inst = self.make_instance("legacy")
        utils.write_json(inst.path / "servers.json", [
            {"name": "Old", "ip": "old.example.com", "port": 25566},
        ])
        rows = servers.list_servers(inst)
        self.assertEqual(rows[0]["name"], "Old")
        self.assertEqual(rows[0]["port"], 25566)
        self.assertTrue((inst.path / "servers.dat").is_file())
        # 迁移后以 dat 为准：json 里再加东西不生效
        utils.write_json(inst.path / "servers.json", [])
        self.assertEqual(len(servers.list_servers(inst)), 1)

    def test_import_txt_roundtrip(self):
        inst = self.make_instance("imp")
        n = servers.import_servers_txt(
            inst, "# 注释\nHypixel\tmc.hypixel.net\nplay.example.com:25566\n")
        self.assertEqual(n, 2)
        text = servers.export_servers_txt(inst)
        self.assertIn("mc.hypixel.net", text)
        self.assertIn("play.example.com:25566", text)
        _n2, root = nbt.loads((inst.path / "servers.dat").read_bytes())
        self.assertEqual(len(root["servers"][1][1]), 2)

    def test_corrupt_dat_returns_empty(self):
        inst = self.make_instance("bad")
        (inst.path / "servers.dat").write_bytes(b"\x0a\x00\x01garbage")
        self.assertEqual(servers.list_servers(inst), [])


class TestTerracottaLobby(Sandbox):
    def test_lobby_preserves_existing_and_writes_uncompressed(self):
        from mclauncher import terracotta
        inst = self.make_instance("lobby")
        dat = inst.path / "servers.dat"
        # 游戏写的未压缩 servers.dat（老实现读不了会清空它）
        dat.write_bytes(vanilla_servers_dat([
            {"name": "我的服", "ip": "mc.example.com", "acceptTextures": 1},
        ]))
        terracotta.write_lobby_server(inst.path, "127.0.0.1:35781")
        raw = dat.read_bytes()
        # 必须是未压缩 NBT（游戏只认未压缩），且首位是大厅、原条目保留
        self.assertNotEqual(raw[:2], b"\x1f\x8b")
        rows = servers.read_servers_dat(dat)
        self.assertEqual(rows[0]["name"], terracotta.LOBBY_NAME)
        self.assertEqual(rows[0]["port"], 35781)
        self.assertEqual(rows[1]["name"], "我的服")
        # 游戏写的字段不能丢
        self.assertIn("acceptTextures", rows[1]["_extra"])

    def test_lobby_replaces_stale_lobby(self):
        from mclauncher import terracotta
        inst = self.make_instance("lobby2")
        terracotta.write_lobby_server(inst.path, "127.0.0.1:1000")
        terracotta.write_lobby_server(inst.path, "127.0.0.1:2000")
        rows = servers.read_servers_dat(inst.path / "servers.dat")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["port"], 2000)

    def test_reads_legacy_gzip_dat(self):
        from mclauncher import terracotta
        inst = self.make_instance("lobby3")
        dat = inst.path / "servers.dat"
        dat.write_bytes(gzip.compress(vanilla_servers_dat(
            [{"name": "旧条目", "ip": "old.example.com"}])))
        terracotta.write_lobby_server(inst.path, "127.0.0.1:3000")
        rows = servers.read_servers_dat(dat)
        self.assertEqual([r["name"] for r in rows][1], "旧条目")


if __name__ == "__main__":
    unittest.main()
