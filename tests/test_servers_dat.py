# -*- coding: utf-8 -*-
"""服务器列表与游戏内 servers.dat 互通测试。"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from mclauncher import servers as sv
from mclauncher.nbt import loads as nbt_loads


class _FakeInstance:
    def __init__(self, base: Path):
        self.name = "测试实例"
        self.path = base
        base.mkdir(parents=True, exist_ok=True)


class _Base(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.inst = _FakeInstance(Path(self._td.name))

    def tearDown(self):
        self._td.cleanup()

    def _dat(self):
        return self.inst.path / sv.DAT_FILE


class DatRoundtripTests(_Base):
    def test_roundtrip_preserves_game_fields(self):
        entries = [
            {"name": "Hypixel", "ip": "mc.hypixel.net",
             "icon": "aWNvbi1iYXNlNjQ=", "acceptTextures": 1},
            {"name": "本地服", "ip": "localhost:25566", "hidden": 1},
        ]
        sv.write_dat_entries(self._dat(), entries)
        back = sv.read_dat_entries(self._dat())
        self.assertEqual(len(back), 2)
        self.assertEqual(back[0]["name"], "Hypixel")
        self.assertEqual(back[0]["icon"], "aWNvbi1iYXNlNjQ=")
        self.assertEqual(back[0]["acceptTextures"], 1)
        self.assertEqual(back[1]["ip"], "localhost:25566")
        self.assertEqual(back[1]["hidden"], 1)

    def test_written_dat_is_valid_gzip_nbt(self):
        sv.write_dat_entries(self._dat(), [{"name": "s", "ip": "example.com"}])
        root = nbt_loads(self._dat().read_bytes())
        self.assertIn("servers", root)
        self.assertEqual(root["servers"][0]["ip"], "example.com")

    def test_read_missing_or_corrupt(self):
        self.assertEqual(sv.read_dat_entries(self._dat()), [])
        self._dat().write_bytes(b"not nbt at all")
        self.assertEqual(sv.read_dat_entries(self._dat()), [])


class CrudTests(_Base):
    def test_add_writes_game_visible_entry(self):
        row = sv.add_server(self.inst, "我的服", "play.example.com", 25565, "描述文字")
        self.assertEqual(row["ip"], "play.example.com")
        self.assertEqual(row["port"], 25565)
        self.assertEqual(row["description"], "描述文字")
        raw = sv.read_dat_entries(self._dat())
        # 默认端口时 ip 字段不带端口，与游戏写法一致
        self.assertEqual(raw[0]["ip"], "play.example.com")

    def test_add_with_custom_port_embeds_port(self):
        sv.add_server(self.inst, "s", "example.com", 25570)
        raw = sv.read_dat_entries(self._dat())
        self.assertEqual(raw[0]["ip"], "example.com:25570")
        row = sv.list_servers(self.inst)[0]
        self.assertEqual((row["ip"], row["port"]), ("example.com", 25570))

    def test_game_added_entry_shows_up(self):
        # 模拟游戏自己写的 servers.dat（带 icon / acceptTextures）
        sv.write_dat_entries(self._dat(), [
            {"name": "游戏里加的", "ip": "srv.example.org:25577",
             "icon": "abc=", "acceptTextures": 1},
        ])
        rows = sv.list_servers(self.inst)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "游戏里加的")
        self.assertEqual(rows[0]["port"], 25577)
        self.assertEqual(rows[0]["icon"], "abc=")

    def test_update_preserves_icon_and_moves_description(self):
        sv.write_dat_entries(self._dat(), [
            {"name": "老名字", "ip": "a.example.com", "icon": "abc=", "acceptTextures": 1},
        ])
        sv.update_server(self.inst, 0, name="新名字", ip="b.example.com",
                         port=25580, description="新描述")
        raw = sv.read_dat_entries(self._dat())
        self.assertEqual(raw[0]["name"], "新名字")
        self.assertEqual(raw[0]["ip"], "b.example.com:25580")
        self.assertEqual(raw[0]["icon"], "abc=")
        self.assertEqual(raw[0]["acceptTextures"], 1)
        row = sv.list_servers(self.inst)[0]
        self.assertEqual(row["description"], "新描述")

    def test_delete_removes_entry_and_meta(self):
        sv.add_server(self.inst, "s1", "one.example.com", description="d1")
        sv.add_server(self.inst, "s2", "two.example.com")
        sv.delete_server(self.inst, 0)
        rows = sv.list_servers(self.inst)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["ip"], "two.example.com")
        meta = json.loads((self.inst.path / sv.META_FILE).read_text(encoding="utf-8"))
        self.assertNotIn("one.example.com:25565", meta)

    def test_bad_index_raises(self):
        with self.assertRaises(sv.ServerError):
            sv.delete_server(self.inst, 5)
        with self.assertRaises(sv.ServerError):
            sv.update_server(self.inst, 0, name="x")


class LegacyMigrationTests(_Base):
    def test_legacy_json_merged_into_dat(self):
        legacy = [
            {"name": "旧条目", "ip": "legacy.example.com", "port": 25565,
             "description": "旧描述"},
            {"name": "旧条目2", "ip": "legacy2.example.com", "port": 25599},
        ]
        (self.inst.path / sv.LEGACY_FILE).write_text(
            json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
        rows = sv.list_servers(self.inst)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["description"], "旧描述")
        self.assertEqual(rows[1]["port"], 25599)
        # 并入后 dat 落盘、旧文件改名备份，不会重复合并
        self.assertTrue(self._dat().is_file())
        self.assertFalse((self.inst.path / sv.LEGACY_FILE).exists())
        self.assertTrue((self.inst.path / (sv.LEGACY_FILE + ".imported")).is_file())
        self.assertEqual(len(sv.list_servers(self.inst)), 2)

    def test_legacy_duplicate_not_doubled(self):
        sv.add_server(self.inst, "已有", "dup.example.com")
        (self.inst.path / sv.LEGACY_FILE).write_text(
            json.dumps([{"name": "重复", "ip": "dup.example.com", "port": 25565}]),
            encoding="utf-8")
        rows = sv.list_servers(self.inst)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "已有")


class ImportExportTests(_Base):
    def test_import_txt_dedupes(self):
        sv.add_server(self.inst, "已有", "one.example.com")
        n = sv.import_servers_txt(self.inst, "\n".join([
            "# 注释",
            "新服\ttwo.example.com:25566",
            "one.example.com",
            "three.example.com",
        ]))
        self.assertEqual(n, 2)
        rows = sv.list_servers(self.inst)
        self.assertEqual(len(rows), 3)

    def test_export_txt(self):
        sv.add_server(self.inst, "我的服", "one.example.com", 25570)
        text = sv.export_servers_txt(self.inst)
        self.assertIn("我的服\tone.example.com:25570", text)

    def test_import_json(self):
        n = sv.import_servers_json(self.inst, [
            {"name": "a", "ip": "a.example.com", "description": "描述"},
            {"ip": "b.example.com", "port": 25571},
            {"bad": True},
        ])
        self.assertEqual(n, 2)
        rows = sv.list_servers(self.inst)
        self.assertEqual(rows[0]["description"], "描述")
        self.assertEqual(rows[1]["port"], 25571)


class TerracottaLobbyTests(_Base):
    def test_lobby_injection_preserves_icons(self):
        from mclauncher import terracotta as tc
        sv.write_dat_entries(self._dat(), [
            {"name": "带图标的服", "ip": "iconed.example.com", "icon": "abc=",
             "acceptTextures": 1},
        ])
        tc.write_lobby_server(self.inst.path, "terracotta://127.0.0.1:35781")
        raw = sv.read_dat_entries(self._dat())
        self.assertEqual(raw[0]["name"], tc.LOBBY_NAME)
        others = [r for r in raw if r.get("ip") == "iconed.example.com"]
        self.assertEqual(len(others), 1)
        self.assertEqual(others[0]["icon"], "abc=")
        self.assertEqual(others[0]["acceptTextures"], 1)


if __name__ == "__main__":
    unittest.main()
