# -*- coding: utf-8 -*-
"""本地皮肤 2D 立绘渲染（PCL2 / HMCL 皮肤预览同款）。

用合成皮肤逐像素验证：现代 64x64 / 旧版 64x32 / HD、slim 细臂、
外层叠加与旧版全不透明帽子跳过、镜像补齐左臂左腿、预览缓存。
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from mclauncher import skin_render, utils
from mclauncher.skin_render import SkinRenderError

RED = (255, 0, 0, 255)
GREEN = (0, 255, 0, 255)
BLUE = (0, 0, 255, 255)
YELLOW = (255, 255, 0, 255)
CYAN = (0, 255, 255, 255)
MAGENTA = (255, 0, 255, 255)
HAT = (10, 20, 30, 255)
CLEAR = (0, 0, 0, 0)


def _fill(img, rect, color):
    x, y, w, h = rect
    for dx in range(w):
        for dy in range(h):
            img.putpixel((x + dx, y + dy), color)


def _modern_skin(path: Path, arm_w: int = 4, hat=None, jacket=None):
    """现代 64x64：头红 / 身绿 / 右臂蓝 / 左臂黄 / 右腿青 / 左腿品红。"""
    img = Image.new("RGBA", (64, 64), CLEAR)
    _fill(img, (8, 8, 8, 8), RED)
    _fill(img, (20, 20, 8, 12), GREEN)
    _fill(img, (44, 20, arm_w, 12), BLUE)
    _fill(img, (36, 52, arm_w, 12), YELLOW)
    _fill(img, (4, 20, 4, 12), CYAN)
    _fill(img, (20, 52, 4, 12), MAGENTA)
    if hat is not None:
        _fill(img, (40, 8, 8, 8), hat)
    if jacket is not None:
        _fill(img, (20, 36, 8, 12), jacket)
    img.save(path)
    return path


def _legacy_skin(path: Path, hat=None):
    """旧版 64x32：右臂左半列蓝、右半列黄（用来验证镜像）。"""
    img = Image.new("RGBA", (64, 32), CLEAR)
    _fill(img, (8, 8, 8, 8), RED)
    _fill(img, (20, 20, 8, 12), GREEN)
    _fill(img, (44, 20, 2, 12), BLUE)    # 右臂左半
    _fill(img, (46, 20, 2, 12), YELLOW)  # 右臂右半
    _fill(img, (4, 20, 2, 12), CYAN)     # 右腿左半
    _fill(img, (6, 20, 2, 12), MAGENTA)  # 右腿右半
    if hat is not None:
        _fill(img, (40, 8, 8, 8), hat)
    img.save(path)
    return path


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)


class TestModern(Base):
    def test_classic_layout(self):
        skin = _modern_skin(self.root / "s.png")
        out = skin_render.render_front(skin, model="default", scale=1)
        self.assertEqual(out.size, (16, 32))
        self.assertEqual(out.getpixel((8, 4)), RED)       # 头
        self.assertEqual(out.getpixel((8, 14)), GREEN)    # 身体
        self.assertEqual(out.getpixel((2, 14)), BLUE)     # 右臂（观察者左侧）
        self.assertEqual(out.getpixel((14, 14)), YELLOW)  # 左臂
        self.assertEqual(out.getpixel((6, 26)), CYAN)     # 右腿
        self.assertEqual(out.getpixel((10, 26)), MAGENTA)  # 左腿

    def test_slim_canvas(self):
        skin = _modern_skin(self.root / "s.png", arm_w=3)
        out = skin_render.render_front(skin, model="slim", scale=1)
        self.assertEqual(out.size, (14, 32))
        self.assertEqual(out.getpixel((1, 14)), BLUE)     # 3px 右臂
        self.assertEqual(out.getpixel((12, 14)), YELLOW)  # 3px 左臂
        self.assertEqual(out.getpixel((7, 4)), RED)       # 头居中

    def test_hat_overlay_applied(self):
        skin = _modern_skin(self.root / "s.png", hat=HAT)
        out = skin_render.render_front(skin, scale=1)
        self.assertEqual(out.getpixel((8, 4)), HAT)

    def test_modern_opaque_hat_not_skipped(self):
        """整块不透明跳过是旧版专属启发；现代皮肤外层照常叠加。"""
        skin = _modern_skin(self.root / "s.png", hat=HAT, jacket=HAT)
        out = skin_render.render_front(skin, scale=1)
        self.assertEqual(out.getpixel((8, 4)), HAT)
        self.assertEqual(out.getpixel((8, 14)), HAT)

    def test_scale_upsamples(self):
        skin = _modern_skin(self.root / "s.png")
        out = skin_render.render_front(skin, scale=4)
        self.assertEqual(out.size, (64, 128))
        self.assertEqual(out.getpixel((33, 17)), RED)


class TestLegacy(Base):
    def test_mirrored_limbs(self):
        skin = _legacy_skin(self.root / "s.png")
        out = skin_render.render_front(skin, scale=1)
        self.assertEqual(out.size, (16, 32))
        # 右臂原样：左半蓝右半黄
        self.assertEqual(out.getpixel((0, 14)), BLUE)
        self.assertEqual(out.getpixel((3, 14)), YELLOW)
        # 左臂镜像：左半黄右半蓝
        self.assertEqual(out.getpixel((12, 14)), YELLOW)
        self.assertEqual(out.getpixel((15, 14)), BLUE)
        # 腿同理
        self.assertEqual(out.getpixel((4, 26)), CYAN)
        self.assertEqual(out.getpixel((7, 26)), MAGENTA)
        self.assertEqual(out.getpixel((8, 26)), MAGENTA)
        self.assertEqual(out.getpixel((11, 26)), CYAN)

    def test_opaque_hat_skipped(self):
        skin = _legacy_skin(self.root / "s.png", hat=(255, 255, 255, 255))
        out = skin_render.render_front(skin, scale=1)
        self.assertEqual(out.getpixel((8, 4)), RED)

    def test_partial_hat_applied(self):
        img = Image.new("RGBA", (64, 32), CLEAR)
        _fill(img, (8, 8, 8, 8), RED)
        _fill(img, (40, 8, 4, 8), HAT)  # 帽子只画一半，另一半透明
        p = self.root / "s.png"
        img.save(p)
        out = skin_render.render_front(p, scale=1)
        self.assertEqual(out.getpixel((4, 2)), HAT)
        self.assertEqual(out.getpixel((10, 2)), RED)


class TestHDAndErrors(Base):
    def test_hd_skin(self):
        skin = _modern_skin(self.root / "base.png")
        hd = Image.open(skin).resize((128, 128), Image.NEAREST)
        p = self.root / "hd.png"
        hd.save(p)
        out = skin_render.render_front(p, scale=2)
        self.assertEqual(out.size, (32, 64))
        self.assertEqual(out.getpixel((16, 8)), RED)

    def test_bad_size_raises(self):
        p = self.root / "bad.png"
        Image.new("RGBA", (50, 50)).save(p)
        with self.assertRaises(SkinRenderError):
            skin_render.render_front(p)

    def test_missing_file_raises(self):
        with self.assertRaises(SkinRenderError):
            skin_render.render_front(self.root / "nope.png")

    def test_render_head(self):
        skin = _modern_skin(self.root / "s.png", hat=None)
        out = skin_render.render_head(skin, scale=1)
        self.assertEqual(out.size, (8, 8))
        self.assertEqual(out.getpixel((4, 4)), RED)


class TestPreviewCache(Base):
    def setUp(self):
        super().setUp()
        p = patch.object(utils, "ROOT", self.root)
        p.start()
        self.addCleanup(p.stop)

    def test_cache_roundtrip(self):
        skin = _modern_skin(self.root / "s.png")
        first = skin_render.ensure_preview(skin, scale=2)
        self.assertTrue(Path(first).is_file())
        self.assertTrue(first.endswith(".png"))
        second = skin_render.ensure_preview(skin, scale=2)
        self.assertEqual(first, second)
        # 内容变了 → 新缓存键
        _modern_skin(skin, hat=HAT)
        third = skin_render.ensure_preview(skin, scale=2)
        self.assertNotEqual(first, third)

    def test_kind_head_and_bad_kind(self):
        skin = _modern_skin(self.root / "s.png")
        head = skin_render.ensure_preview(skin, kind="head", scale=2)
        img = Image.open(head)
        self.assertEqual(img.size, (16, 16))
        with self.assertRaises(SkinRenderError):
            skin_render.ensure_preview(skin, kind="sideways")

    def test_model_distinct_cache(self):
        skin = _modern_skin(self.root / "s.png")
        a = skin_render.ensure_preview(skin, model="default", scale=2)
        b = skin_render.ensure_preview(skin, model="slim", scale=2)
        self.assertNotEqual(a, b)


class TestFacade(Base):
    def test_bridge_facade(self):
        from bridge.api import BackendAPI
        self.assertTrue(callable(getattr(BackendAPI, "render_skin_preview", None)))

    def test_bridge_impl_renders(self):
        skin = _modern_skin(self.root / "s.png")
        with patch.object(utils, "ROOT", self.root):
            from bridge.api import BackendAPI
            out = BackendAPI.render_skin_preview.__wrapped__(
                None, str(skin)) if hasattr(
                BackendAPI.render_skin_preview, "__wrapped__") else \
                BackendAPI.render_skin_preview(None, str(skin))
        self.assertTrue(Path(out).is_file())


if __name__ == "__main__":
    unittest.main()
