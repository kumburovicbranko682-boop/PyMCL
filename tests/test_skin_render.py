# -*- coding: utf-8 -*-
"""皮肤本地 2D 渲染：像素级验证正面拼装。"""
from __future__ import annotations

import os
import struct
import unittest
import zlib

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _png_rgba(width: int, height: int, painter) -> bytes:
    """生成真实 PNG。painter(x, y) -> (r, g, b, a)。"""
    raws = []
    for y in range(height):
        row = b"\x00"
        for x in range(width):
            row += bytes(painter(x, y))
        raws.append(row)
    def chunk(tag, data):
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(
            ">I", zlib.crc32(body) & 0xFFFFFFFF)
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    idat = zlib.compress(b"".join(raws))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", idat) + chunk(b"IEND", b""))


HEAD = (255, 0, 0, 255)      # 头 (8,8)
HAT = (0, 0, 255, 255)       # 帽 (40,8) —— 应覆盖头
TORSO = (255, 255, 0, 255)   # 身 (20,20)
R_ARM = (0, 255, 255, 255)   # 右臂 (44,20)
L_ARM = (128, 0, 128, 255)   # 左臂 (36,52)
R_LEG = (255, 128, 0, 255)   # 右腿 (4,20)
L_LEG = (96, 96, 96, 255)    # 左腿 (20,52)


def _region(x, y, legacy=False):
    if 8 <= x < 16 and 8 <= y < 16:
        return HEAD
    if 40 <= x < 48 and 8 <= y < 16:
        return HAT
    if 20 <= x < 28 and 20 <= y < 32:
        return TORSO
    if 44 <= x < 48 and 20 <= y < 32:
        return R_ARM
    if 4 <= x < 8 and 20 <= y < 32:
        return R_LEG
    if not legacy:
        if 36 <= x < 40 and 52 <= y < 64:
            return L_ARM
        if 20 <= x < 24 and 52 <= y < 64:
            return L_LEG
    return (0, 0, 0, 0)


class SkinRenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtGui import QGuiApplication
        cls.app = QGuiApplication.instance() or QGuiApplication([])
        cls.tex64 = _png_rgba(64, 64, lambda x, y: _region(x, y))
        cls.tex32 = _png_rgba(64, 32, lambda x, y: _region(x, y, legacy=True))

    def _pixel(self, pix, x, y):
        img = pix.toImage()
        c = img.pixelColor(x, y)
        return (c.red(), c.green(), c.blue(), c.alpha())

    def test_classic_layout(self):
        from app.skin_render import render_front
        pix = render_front(self.tex64, "classic", height=256)
        self.assertFalse(pix.isNull())
        scale = pix.height() // 32
        self.assertEqual(pix.width(), 16 * scale)
        # 帽覆盖头（头区中心）
        self.assertEqual(self._pixel(pix, 8 * scale, 4 * scale), HAT)
        # 身体
        self.assertEqual(self._pixel(pix, 8 * scale, 14 * scale), TORSO)
        # 观察者左侧 = 右臂
        self.assertEqual(self._pixel(pix, 2 * scale, 14 * scale), R_ARM)
        # 观察者右侧 = 左臂
        self.assertEqual(self._pixel(pix, 14 * scale, 14 * scale), L_ARM)
        # 腿
        self.assertEqual(self._pixel(pix, 6 * scale, 26 * scale), R_LEG)
        self.assertEqual(self._pixel(pix, 10 * scale, 26 * scale), L_LEG)

    def test_slim_width(self):
        from app.skin_render import render_front
        pix = render_front(self.tex64, "slim", height=256)
        scale = pix.height() // 32
        self.assertEqual(pix.width(), 14 * scale)

    def test_legacy_mirrors_limbs(self):
        from app.skin_render import render_front
        pix = render_front(self.tex32, "classic", height=256)
        self.assertFalse(pix.isNull())
        scale = pix.height() // 32
        # 旧版没有左肢：右肢镜像
        self.assertEqual(self._pixel(pix, 14 * scale, 14 * scale), R_ARM)
        self.assertEqual(self._pixel(pix, 10 * scale, 26 * scale), R_LEG)

    def test_invalid_data(self):
        from app.skin_render import render_front
        self.assertTrue(render_front(b"not a png").isNull())
        self.assertTrue(render_front(b"").isNull())


if __name__ == "__main__":
    unittest.main()
