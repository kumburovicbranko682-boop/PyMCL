# -*- coding: utf-8 -*-
"""版本自定义图标（PCL2「版本图标」/ HMCL 同款）。

覆盖：设置/读取/替换/清除、格式与大小守卫、随复制/重命名迁移、两个门面。
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import PropertyMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mclauncher import version_ops as vops  # noqa: E402
from mclauncher.config import CONFIG  # noqa: E402
from mclauncher.version_ops import VersionOpError  # noqa: E402

# 1x1 透明 PNG
_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d4944415478da63640000000600023081d02f0000000049454e44ae426082")


class _WithVersion(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        self.addCleanup(self._td.cleanup)
        patcher = patch.object(type(CONFIG), "instances_dir",
                               new_callable=PropertyMock, return_value=self.root)
        patcher.start()
        self.addCleanup(patcher.stop)
        from mclauncher.instances import Instance
        (self.root / "inst").mkdir(parents=True)
        self.inst = Instance("inst")
        vdir = self.inst.versions_dir() / "1.20.1"
        vdir.mkdir(parents=True)
        (vdir / "1.20.1.json").write_text('{"id": "1.20.1"}', encoding="utf-8")

    def _png(self, name="icon.png", size=0) -> Path:
        p = self.root / name
        p.write_bytes(_PNG + b"\x00" * size)
        return p


class IconTests(_WithVersion):
    def test_default_empty(self):
        self.assertEqual(vops.icon_path(self.inst, "1.20.1"), "")
        self.assertEqual(vops.icon_path(self.inst, "ghost"), "")

    def test_set_get_clear(self):
        out = vops.set_icon(self.inst, "1.20.1", self._png())
        self.assertTrue(Path(out).is_file())
        self.assertEqual(vops.icon_path(self.inst, "1.20.1"), out)
        self.assertEqual(Path(out).name, ".version_icon.png")
        vops.clear_icon(self.inst, "1.20.1")
        self.assertEqual(vops.icon_path(self.inst, "1.20.1"), "")

    def test_replace_removes_old_suffix(self):
        vops.set_icon(self.inst, "1.20.1", self._png("a.png"))
        jpg = self.root / "b.jpg"
        jpg.write_bytes(b"\xff\xd8\xff\xe0fakejpg")
        out = vops.set_icon(self.inst, "1.20.1", jpg)
        self.assertTrue(out.endswith(".version_icon.jpg"))
        vdir = self.inst.versions_dir() / "1.20.1"
        self.assertFalse((vdir / ".version_icon.png").exists())

    def test_guards(self):
        with self.assertRaises(VersionOpError):
            vops.set_icon(self.inst, "1.20.1", self.root / "ghost.png")
        bad = self.root / "notes.txt"
        bad.write_text("x", encoding="utf-8")
        with self.assertRaises(VersionOpError):
            vops.set_icon(self.inst, "1.20.1", bad)
        big = self._png("big.png", size=4 * 1024 * 1024)
        with self.assertRaises(VersionOpError):
            vops.set_icon(self.inst, "1.20.1", big)
        with self.assertRaises(VersionOpError):
            vops.set_icon(self.inst, "ghost", self._png())

    def test_travels_with_copy_and_rename(self):
        vops.set_icon(self.inst, "1.20.1", self._png())
        vops.copy_version(self.inst, "1.20.1", "1.20.1-copy")
        self.assertTrue(vops.icon_path(self.inst, "1.20.1-copy")
                        .endswith(".version_icon.png"))
        vops.rename_version(self.inst, "1.20.1", "生存主线")
        self.assertTrue(vops.icon_path(self.inst, "生存主线")
                        .endswith(".version_icon.png"))
        self.assertEqual(vops.icon_path(self.inst, "1.20.1"), "")


class FacadeTests(_WithVersion):
    def test_bridge(self):
        from bridge.api import BackendAPI

        class _Bus:
            def emit(self, *a, **k):
                pass

        api = BackendAPI(_Bus())
        with patch.object(api, "_instance", lambda name: self.inst):
            self.assertEqual(api.get_version_icon("inst", "1.20.1"), "")
            out = api.set_version_icon("inst", "1.20.1", str(self._png()))
            self.assertEqual(api.get_version_icon("inst", "1.20.1"), out)
            api.clear_version_icon("inst", "1.20.1")
            self.assertEqual(api.get_version_icon("inst", "1.20.1"), "")

    def test_qt_parity_by_name(self):
        # Qt 门面方法体依赖 self._instance/_emit_ui_changed，这里只验签名存在且一致
        import inspect
        from app.backend import BackendAPI as QtBackend
        from bridge.api import BackendAPI as BridgeBackend
        for name in ("get_version_icon", "set_version_icon", "clear_version_icon"):
            qt_sig = inspect.signature(getattr(QtBackend, name))
            br_sig = inspect.signature(getattr(BridgeBackend, name))
            self.assertEqual(str(qt_sig), str(br_sig), name)


if __name__ == "__main__":
    unittest.main()
