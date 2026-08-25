# -*- coding: utf-8 -*-
"""存档详情（level.dat 解析）与单人世界直启（quickPlay）。"""
from __future__ import annotations

import gzip
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mclauncher import nbt_lite as nbt
from mclauncher import saves
from mclauncher.instances import Instance


def write_level_dat(save_dir: Path, name="我的世界", version="1.20.4",
                    game_type=0, hardcore=0, cheats=1, last_played_ms=1724500000000,
                    with_version=True):
    data = {
        "LevelName": (nbt.TAG_STRING, name),
        "GameType": (nbt.TAG_INT, game_type),
        "hardcore": (nbt.TAG_BYTE, hardcore),
        "allowCommands": (nbt.TAG_BYTE, cheats),
        "LastPlayed": (nbt.TAG_LONG, last_played_ms),
    }
    if with_version:
        data["Version"] = (nbt.TAG_COMPOUND, {
            "Name": (nbt.TAG_STRING, version),
            "Id": (nbt.TAG_INT, 3700),
        })
    root = {"Data": (nbt.TAG_COMPOUND, data)}
    save_dir.mkdir(parents=True, exist_ok=True)
    (save_dir / "level.dat").write_bytes(gzip.compress(nbt.dumps(root)))


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

    def make_instance(self, name="w") -> Instance:
        inst = Instance(name)
        inst.create()
        return inst


class TestLevelMeta(Sandbox):
    def test_reads_modern_level(self):
        inst = self.make_instance()
        save = inst.path / "saves" / "New World"
        write_level_dat(save, name="服务器备份", version="1.20.4",
                        game_type=1, cheats=1)
        meta = saves.read_level_meta(save)
        self.assertEqual(meta["level_name"], "服务器备份")
        self.assertEqual(meta["version_name"], "1.20.4")
        self.assertEqual(meta["mode"], "创造")
        self.assertTrue(meta["cheats"])
        self.assertEqual(meta["last_played"], 1724500000)

    def test_hardcore_overrides_mode(self):
        inst = self.make_instance("h")
        save = inst.path / "saves" / "HC"
        write_level_dat(save, game_type=0, hardcore=1)
        self.assertEqual(saves.read_level_meta(save)["mode"], "硬核")

    def test_legacy_without_version(self):
        inst = self.make_instance("old")
        save = inst.path / "saves" / "Legacy"
        write_level_dat(save, with_version=False)
        meta = saves.read_level_meta(save)
        self.assertEqual(meta["version_name"], "")
        self.assertEqual(meta["mode"], "生存")

    def test_corrupt_level_dat(self):
        inst = self.make_instance("bad")
        save = inst.path / "saves" / "Broken"
        save.mkdir(parents=True)
        (save / "level.dat").write_bytes(b"garbage")
        self.assertEqual(saves.read_level_meta(save), {})

    def test_list_saves_includes_meta(self):
        inst = self.make_instance("lst")
        write_level_dat(inst.path / "saves" / "Alpha", name="Alpha世界", game_type=2)
        rows = saves.list_saves(inst)
        self.assertEqual(rows[0]["name"], "Alpha")
        self.assertEqual(rows[0]["level_name"], "Alpha世界")
        self.assertEqual(rows[0]["mode"], "冒险")


class TestQuickPlay(Sandbox):
    QP_JSON = {
        "id": "1.20.1",
        "arguments": {"game": [
            "--username", "${auth_player_name}",
            {"rules": [{"action": "allow",
                        "features": {"is_quick_play_singleplayer": True}}],
             "value": ["--quickPlaySingleplayer", "${quickPlaySingleplayer}"]},
        ]},
    }
    OLD_JSON = {
        "id": "1.19.4",
        "arguments": {"game": ["--username", "${auth_player_name}"]},
    }

    def _write_version(self, inst: Instance, vid: str, data: dict):
        vdir = inst.path / "versions" / vid
        vdir.mkdir(parents=True, exist_ok=True)
        (vdir / f"{vid}.json").write_text(json.dumps(data), "utf-8")

    def _api(self):
        from bridge.api import BackendAPI, EventBus
        return BackendAPI(EventBus())

    def test_supported_detection(self):
        inst = self.make_instance("qp")
        self._write_version(inst, "1.20.1", self.QP_JSON)
        self._write_version(inst, "1.19.4", self.OLD_JSON)
        api = self._api()
        self.assertTrue(api.world_quickplay_supported("qp", "1.20.1"))
        self.assertFalse(api.world_quickplay_supported("qp", "1.19.4"))

    def test_launch_world_rejects_old_version(self):
        from mclauncher.launcher import LaunchError
        inst = self.make_instance("qp2")
        self._write_version(inst, "1.19.4", self.OLD_JSON)
        api = self._api()
        with self.assertRaises(LaunchError) as ctx:
            api.launch_world("qp2", "MyWorld", "1.19.4")
        self.assertIn("1.20", str(ctx.exception))

    def test_launch_world_requires_installed_version(self):
        from mclauncher.launcher import LaunchError
        self.make_instance("empty")
        api = self._api()
        with self.assertRaises(LaunchError):
            api.launch_world("empty", "MyWorld")

    def test_launch_world_passes_quickplay_args(self):
        inst = self.make_instance("qp3")
        self._write_version(inst, "1.20.1", self.QP_JSON)
        api = self._api()
        with mock.patch.object(api, "launch_game", return_value="task-9") as lg:
            out = api.launch_world("qp3", "New World", "1.20.1")
        self.assertEqual(out, "task-9")
        kwargs = lg.call_args.kwargs
        self.assertEqual(kwargs["extra_game_args"],
                         ["--quickPlaySingleplayer", "New World"])
        self.assertEqual(kwargs["version"], "1.20.1")


if __name__ == "__main__":
    unittest.main()
