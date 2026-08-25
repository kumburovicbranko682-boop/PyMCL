# -*- coding: utf-8 -*-
"""拖拽/本地文件导入识别与分发。"""
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mclauncher import import_files  # noqa: E402


def _make_zip(path: Path, entries: dict):
    """entries: {内部路径: 内容(str|bytes) 或 None 表示目录}。"""
    with zipfile.ZipFile(path, "w") as z:
        for name, content in entries.items():
            if content is None:
                z.writestr(name if name.endswith("/") else name + "/", b"")
            else:
                data = content.encode("utf-8") if isinstance(content, str) else content
                z.writestr(name, data)


class ClassifyTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_mrpack(self):
        p = self.dir / "pack.mrpack"
        p.write_bytes(b"PK\x03\x04junk")
        self.assertEqual(import_files.classify_file(p)["kind"], "modpack")

    def test_jar_and_litemod_are_mods(self):
        for name in ("sodium.jar", "old.litemod"):
            p = self.dir / name
            p.write_bytes(b"PK\x03\x04junk")
            self.assertEqual(import_files.classify_file(p)["kind"], "mod")

    def test_cf_manifest_zip(self):
        p = self.dir / "cfpack.zip"
        _make_zip(p, {"manifest.json": json.dumps(
            {"manifestType": "minecraftModpack", "minecraft": {"version": "1.20.1"}}),
            "overrides/mods/a.jar": "x"})
        self.assertEqual(import_files.classify_file(p)["kind"], "modpack")

    def test_cf_manifest_nested_one_dir(self):
        p = self.dir / "cfpack2.zip"
        _make_zip(p, {"MyPack/manifest.json": json.dumps(
            {"minecraft": {"version": "1.19.2"}})})
        self.assertEqual(import_files.classify_file(p)["kind"], "modpack")

    def test_non_modpack_manifest_ignored(self):
        # manifest.json 内容不是整合包清单：不能误判
        p = self.dir / "webapp.zip"
        _make_zip(p, {"manifest.json": json.dumps({"name": "some web app"}),
                      "index.html": "<html></html>"})
        self.assertEqual(import_files.classify_file(p)["kind"], "unknown")

    def test_modrinth_index_zip(self):
        p = self.dir / "mr.zip"
        _make_zip(p, {"modrinth.index.json": "{}"})
        self.assertEqual(import_files.classify_file(p)["kind"], "modpack")

    def test_mmc_pack_zip(self):
        p = self.dir / "mmc.zip"
        _make_zip(p, {"OnePack/mmc-pack.json": "{}", "OnePack/instance.cfg": "name=x"})
        self.assertEqual(import_files.classify_file(p)["kind"], "modpack")

    def test_plain_minecraft_dir_zip(self):
        p = self.dir / "mcdir.zip"
        _make_zip(p, {
            ".minecraft/versions/1.20.1/1.20.1.json": "{}",
            ".minecraft/versions/1.20.1/1.20.1.jar": "x",
            ".minecraft/mods/a.jar": "x",
        })
        self.assertEqual(import_files.classify_file(p)["kind"], "modpack")

    def test_world_zip(self):
        p = self.dir / "world.zip"
        _make_zip(p, {"MyWorld/level.dat": b"\x0a\x00", "MyWorld/region/r.0.0.mca": "x"})
        self.assertEqual(import_files.classify_file(p)["kind"], "world")

    def test_world_zip_root_level_dat(self):
        p = self.dir / "world2.zip"
        _make_zip(p, {"level.dat": b"\x0a\x00"})
        self.assertEqual(import_files.classify_file(p)["kind"], "world")

    def test_datapack_zip(self):
        p = self.dir / "dp.zip"
        _make_zip(p, {"pack.mcmeta": "{}", "data/ns/functions/f.mcfunction": "say hi"})
        self.assertEqual(import_files.classify_file(p)["kind"], "datapack")

    def test_resourcepack_zip(self):
        p = self.dir / "rp.zip"
        _make_zip(p, {"pack.mcmeta": "{}", "assets/minecraft/textures/block/stone.png": "x"})
        self.assertEqual(import_files.classify_file(p)["kind"], "resourcepack")

    def test_resourcepack_nested(self):
        p = self.dir / "rp2.zip"
        _make_zip(p, {"Faithful/pack.mcmeta": "{}",
                      "Faithful/assets/minecraft/x.png": "x"})
        self.assertEqual(import_files.classify_file(p)["kind"], "resourcepack")

    def test_shaderpack_zip(self):
        p = self.dir / "seus.zip"
        _make_zip(p, {"shaders/composite.fsh": "void main(){}",
                      "shaders/composite.vsh": "void main(){}"})
        self.assertEqual(import_files.classify_file(p)["kind"], "shaderpack")

    def test_shaderpack_nested(self):
        p = self.dir / "bsl.zip"
        _make_zip(p, {"BSL/shaders/final.fsh": "void main(){}"})
        self.assertEqual(import_files.classify_file(p)["kind"], "shaderpack")

    def test_world_wins_over_inner_datapack(self):
        # 世界里自带数据包：level.dat 优先
        p = self.dir / "w3.zip"
        _make_zip(p, {
            "W/level.dat": b"\x0a\x00",
            "W/datapacks/dp/pack.mcmeta": "{}",
            "W/datapacks/dp/data/ns/f.json": "{}",
        })
        self.assertEqual(import_files.classify_file(p)["kind"], "world")

    def test_unknown_zip(self):
        p = self.dir / "junk.zip"
        _make_zip(p, {"readme.txt": "hello"})
        info = import_files.classify_file(p)
        self.assertEqual(info["kind"], "unknown")
        self.assertEqual(info["label"], "无法识别")

    def test_corrupt_zip(self):
        p = self.dir / "bad.zip"
        p.write_bytes(b"not a zip at all")
        self.assertEqual(import_files.classify_file(p)["kind"], "unknown")

    def test_missing_file(self):
        info = import_files.classify_file(self.dir / "nope.zip")
        self.assertEqual(info["kind"], "unknown")
        self.assertIn("error", info)

    def test_unsupported_ext(self):
        p = self.dir / "a.txt"
        p.write_text("x")
        self.assertEqual(import_files.classify_file(p)["kind"], "unknown")

    def test_classify_files_batch(self):
        p1 = self.dir / "a.mrpack"
        p1.write_bytes(b"PK")
        p2 = self.dir / "b.jar"
        p2.write_bytes(b"PK")
        kinds = [i["kind"] for i in import_files.classify_files([p1, p2])]
        self.assertEqual(kinds, ["modpack", "mod"])


class DispatchTests(unittest.TestCase):
    """bridge.api.import_local_file 按识别结果调对应安装方法。"""

    def setUp(self):
        from bridge.api import BackendAPI

        class _Bus:
            def emit(self, *a, **k):
                pass

        self.api = BackendAPI(_Bus())
        self.calls = []
        for meth in ("install_modpack", "install_mod", "install_world",
                     "install_resourcepack", "install_shader", "install_datapack"):
            setattr(self.api, meth,
                    (lambda m: lambda *a, **k: self.calls.append((m, a, k)) or "tid")(meth))
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_modpack_dispatch(self):
        p = self.dir / "x.mrpack"
        p.write_bytes(b"PK")
        tid = self.api.import_local_file(str(p))
        self.assertEqual(tid, "tid")
        meth, args, kwargs = self.calls[0]
        self.assertEqual(meth, "install_modpack")
        self.assertEqual(kwargs["extra"]["path"], str(p))

    def test_mod_dispatch(self):
        p = self.dir / "sodium.jar"
        p.write_bytes(b"PK")
        self.api.import_local_file(str(p), instance="game2")
        meth, args, kwargs = self.calls[0]
        self.assertEqual(meth, "install_mod")
        self.assertEqual(args[1], "game2")
        self.assertEqual(kwargs["extra"]["path"], str(p))

    def test_world_dispatch(self):
        p = self.dir / "w.zip"
        _make_zip(p, {"W/level.dat": b"\x0a"})
        self.api.import_local_file(str(p))
        self.assertEqual(self.calls[0][0], "install_world")

    def test_kind_override(self):
        p = self.dir / "data.zip"
        _make_zip(p, {"readme.txt": "x"})
        self.api.import_local_file(str(p), kind="datapack")
        self.assertEqual(self.calls[0][0], "install_datapack")

    def test_unknown_raises(self):
        p = self.dir / "junk.zip"
        _make_zip(p, {"readme.txt": "x"})
        with self.assertRaises(ValueError):
            self.api.import_local_file(str(p))
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
