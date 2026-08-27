# -*- coding: utf-8 -*-
"""实例复制：整实例拷贝、跳过运行垃圾、自动命名、失败回滚。"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import PropertyMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mclauncher import instances
from mclauncher.config import CONFIG


class DuplicateInstanceTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        patcher = patch.object(type(CONFIG), "instances_dir",
                               new_callable=PropertyMock,
                               return_value=self.root)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._td.cleanup)

    def _make_src(self, name="生存服"):
        inst = instances.Instance(name)
        inst.create()
        (inst.path / "mods" / "jei.jar").write_bytes(b"jarbytes")
        (inst.path / "config" / "jei.toml").write_text("cfg", "utf-8")
        (inst.path / "saves" / "world" / "region").mkdir(parents=True)
        (inst.path / "saves" / "world" / "level.dat").write_bytes(b"lvl")
        (inst.path / "saves" / "world" / "region" / "r.0.0.mca").write_bytes(b"chunk")
        vdir = inst.path / "versions" / "1.20.1"
        vdir.mkdir(parents=True)
        (vdir / "1.20.1.json").write_text('{"id": "1.20.1"}', "utf-8")
        (inst.path / "options.txt").write_text("lang:zh_cn", "utf-8")
        (inst.path / "logs").mkdir(exist_ok=True)
        (inst.path / "logs" / "latest.log").write_text("noise", "utf-8")
        (inst.path / "crash-reports").mkdir(exist_ok=True)
        (inst.path / "crash-reports" / "crash.txt").write_text("boom", "utf-8")
        inst.set_meta("modpack", {"name": "P", "version": "1.0"})
        return inst

    def test_copies_everything_except_runtime_junk(self):
        self._make_src()
        out = instances.duplicate_instance("生存服", "生存服-实验")
        self.assertEqual(out, "生存服-实验")
        dest = self.root / out
        self.assertEqual((dest / "mods" / "jei.jar").read_bytes(), b"jarbytes")
        self.assertEqual((dest / "config" / "jei.toml").read_text("utf-8"), "cfg")
        self.assertEqual(
            (dest / "saves" / "world" / "region" / "r.0.0.mca").read_bytes(), b"chunk")
        self.assertTrue((dest / "versions" / "1.20.1" / "1.20.1.json").is_file())
        self.assertEqual((dest / "options.txt").read_text("utf-8"), "lang:zh_cn")
        # 运行垃圾不复制内容（标准目录会重建为空目录）
        self.assertFalse((dest / "logs" / "latest.log").exists())
        self.assertFalse((dest / "crash-reports" / "crash.txt").exists())
        # meta 的 name 指向新实例，整合包信息保留（含更新能力）
        meta = instances.Instance(out).meta()
        self.assertEqual(meta.get("name"), out)
        self.assertEqual((meta.get("modpack") or {}).get("name"), "P")
        # 源实例原样
        self.assertTrue((self.root / "生存服" / "mods" / "jei.jar").is_file())

    def test_default_name_and_dedup(self):
        self._make_src()
        first = instances.duplicate_instance("生存服")
        second = instances.duplicate_instance("生存服")
        self.assertEqual(first, "生存服-副本")
        self.assertEqual(second, "生存服-副本-2")
        self.assertTrue((self.root / second / "mods" / "jei.jar").is_file())

    def test_explicit_name_collision_gets_suffix(self):
        self._make_src()
        instances.duplicate_instance("生存服", "试验")
        out = instances.duplicate_instance("生存服", "试验")
        self.assertEqual(out, "试验-2")

    def test_missing_source_raises(self):
        with self.assertRaises(instances.InstanceError):
            instances.duplicate_instance("不存在的实例")

    def test_progress_reported(self):
        self._make_src()
        calls = []
        instances.duplicate_instance("生存服", "带进度",
                                     on_progress=lambda d, t: calls.append((d, t)))
        self.assertTrue(calls)
        done, total = calls[-1]
        self.assertEqual(done, total)
        self.assertGreater(total, 0)

    def test_failure_rolls_back(self):
        self._make_src()
        import shutil as _sh
        real = _sh.copy2
        state = {"n": 0}

        def flaky(src, dst, **kw):
            state["n"] += 1
            if state["n"] >= 3:
                raise OSError("disk full")
            return real(src, dst, **kw)

        with patch.object(instances.shutil, "copy2", side_effect=flaky):
            with self.assertRaises(OSError):
                instances.duplicate_instance("生存服", "坏副本")
        self.assertFalse((self.root / "坏副本").exists())
        self.assertNotIn("坏副本", instances.list_instances())


if __name__ == "__main__":
    unittest.main()
