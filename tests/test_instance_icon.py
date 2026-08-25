# -*- coding: utf-8 -*-
"""实例自定义图标（HMCL/PCL2 版本图标 parity）：魔数识别、替换、清除、门面接线。"""

import base64
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import PropertyMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mclauncher import instances  # noqa: E402
from mclauncher.config import CONFIG  # noqa: E402

# 1x1 真实 PNG
PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGBgAAAABQAB"
    "h6FO1AAAAABJRU5ErkJggg=="
)
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 32
GIF_BYTES = b"GIF89a" + b"\x00" * 16
WEBP_BYTES = b"RIFF\x24\x00\x00\x00WEBP" + b"\x00" * 16
BMP_BYTES = b"BM" + b"\x00" * 32


class _Base(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.base = Path(self._td.name)
        self.addCleanup(self._td.cleanup)
        root = self.base / "instances"
        root.mkdir()
        patcher = patch.object(type(CONFIG), "instances_dir",
                               new_callable=PropertyMock, return_value=root)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.inst = instances.Instance("测试")
        self.inst.create()

    def _src(self, name: str, data: bytes) -> Path:
        p = self.base / name
        p.write_bytes(data)
        return p


class IconCoreTests(_Base):
    def test_set_png_icon(self):
        self.inst.set_icon(self._src("a.png", PNG_BYTES))
        icon = self.inst.icon_path()
        self.assertIsNotNone(icon)
        self.assertEqual(icon.name, ".instance_icon.png")
        self.assertEqual(icon.read_bytes(), PNG_BYTES)

    def test_format_sniffed_not_extension(self):
        # 扩展名撒谎（.png 里装 JPEG），按内容存成 .jpg
        self.inst.set_icon(self._src("lie.png", JPEG_BYTES))
        self.assertEqual(self.inst.icon_path().name, ".instance_icon.jpg")

    def test_gif_webp_bmp_sniff(self):
        for data, suffix in ((GIF_BYTES, ".gif"), (WEBP_BYTES, ".webp"), (BMP_BYTES, ".bmp")):
            self.inst.set_icon(self._src(f"x{suffix}.bin", data))
            self.assertEqual(self.inst.icon_path().suffix, suffix)

    def test_replace_removes_old_suffix(self):
        self.inst.set_icon(self._src("a.jpg", JPEG_BYTES))
        self.inst.set_icon(self._src("b.png", PNG_BYTES))
        icons = list(self.inst.path.glob(".instance_icon.*"))
        self.assertEqual(len(icons), 1)
        self.assertEqual(icons[0].suffix, ".png")

    def test_clear_icon(self):
        self.inst.set_icon(self._src("a.png", PNG_BYTES))
        self.inst.clear_icon()
        self.assertIsNone(self.inst.icon_path())
        # 没图标时 clear 也不报错
        self.inst.clear_icon()

    def test_reject_non_image(self):
        with self.assertRaises(instances.InstanceError):
            self.inst.set_icon(self._src("t.txt", b"hello world, not an image"))
        self.assertIsNone(self.inst.icon_path())

    def test_reject_missing_file(self):
        with self.assertRaises(instances.InstanceError):
            self.inst.set_icon(self.base / "nope.png")

    def test_reject_oversize(self):
        big = self._src("big.png", PNG_BYTES + b"\x00" * (4 * 1024 * 1024))
        with self.assertRaises(instances.InstanceError):
            self.inst.set_icon(big)
        self.assertIsNone(self.inst.icon_path())

    def test_rename_keeps_icon(self):
        self.inst.set_icon(self._src("a.png", PNG_BYTES))
        self.inst.rename("改名")
        self.assertIsNotNone(self.inst.icon_path())
        self.assertEqual(self.inst.icon_path().read_bytes(), PNG_BYTES)


class BridgeFacadeTests(_Base):
    def _api(self):
        from bridge.api import BackendAPI

        class _Bus:
            def emit(self, *a, **k):
                pass

        return BackendAPI(_Bus())

    def test_set_and_clear_via_bridge(self):
        api = self._api()
        src = self._src("a.png", PNG_BYTES)
        api.set_instance_icon("测试", str(src))
        rows = api.get_instances()
        row = next(r for r in rows if r["name"] == "测试")
        self.assertTrue(row["icon"].endswith(".instance_icon.png"))
        self.assertTrue(os.path.isfile(row["icon"]))
        api.clear_instance_icon("测试")
        rows = api.get_instances()
        row = next(r for r in rows if r["name"] == "测试")
        self.assertEqual(row["icon"], "")

    def test_qt_backend_has_same_methods(self):
        import ast
        src = (Path(__file__).resolve().parents[1] / "app" / "backend.py").read_text("utf-8")
        tree = ast.parse(src)
        names = {n.name for n in ast.walk(tree)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        self.assertIn("set_instance_icon", names)
        self.assertIn("clear_instance_icon", names)


if __name__ == "__main__":
    unittest.main()
