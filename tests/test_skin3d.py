# -*- coding: utf-8 -*-
"""3D 皮肤渲染：用带独特颜色的合成皮肤逐面验证 UV 朝向、镜像与图层。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtGui import QColor, QImage
    from PySide6.QtCore import Qt
    # 必须用完整的 QApplication 而不是 QGuiApplication：pytest 收集期就会
    # 执行这里，若先占坑一个 QGuiApplication，后面控件类测试建 QWidget
    # 会直接 SIGABRT（QWidget 要求 QApplication）。
    from PySide6.QtWidgets import QApplication
    HAVE_QT = True
except ImportError:
    HAVE_QT = False

if HAVE_QT:
    _app = QApplication.instance() or QApplication(sys.argv[:1])
    from app.skin3d import normalize_texture, render_skin_3d

RED = QColor(255, 0, 0) if HAVE_QT else None
BLUE = QColor(0, 0, 255) if HAVE_QT else None
GREEN = QColor(0, 160, 0) if HAVE_QT else None
YELLOW = QColor(230, 230, 0) if HAVE_QT else None
CYAN = QColor(0, 200, 200) if HAVE_QT else None
MAGENTA = QColor(200, 0, 200) if HAVE_QT else None
ORANGE = QColor(255, 128, 0) if HAVE_QT else None


def blank_skin(h=64):
    img = QImage(64, h, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    # 全身底色（灰），保证每个体块可见
    grey = QColor(128, 128, 128).rgba()
    for region in ((0, 8, 32, 8), (8, 0, 16, 8),        # 头
                   (16, 16, 24, 16), (28, 16, 8, 4),    # 身体行
                   (0, 16, 16, 16), (4, 16, 8, 4),      # 右腿
                   (40, 16, 16, 16), (44, 16, 8, 4)):   # 右臂
        x, y, w, hh = region
        for dx in range(w):
            for dy in range(hh):
                img.setPixel(x + dx, y + dy, grey)
    if h == 64:
        for region in ((16, 48, 16, 16), (20, 48, 8, 4),   # 左腿
                       (32, 48, 16, 16), (36, 48, 8, 4)):  # 左臂
            x, y, w, hh = region
            for dx in range(w):
                for dy in range(hh):
                    img.setPixel(x + dx, y + dy, grey)
    return img


def fill(img, x, y, w, h, color):
    for dx in range(w):
        for dy in range(h):
            img.setPixel(x + dx, y + dy, color.rgba())


def color_pixels(img, color, tol=24):
    """返回颜色接近 color 的像素坐标列表。"""
    pts = []
    for x in range(img.width()):
        for y in range(img.height()):
            c = img.pixelColor(x, y)
            if c.alpha() > 200 and (abs(c.red() - color.red()) < tol
                                    and abs(c.green() - color.green()) < tol
                                    and abs(c.blue() - color.blue()) < tol):
                pts.append((x, y))
    return pts


@unittest.skipUnless(HAVE_QT, "需要 PySide6")
class TestFaceOrientation(unittest.TestCase):
    def head_skin(self):
        img = blank_skin()
        # 头正面：左半（u 0..4，玩家右手侧）红、右半蓝
        fill(img, 8, 8, 4, 8, RED)
        fill(img, 12, 8, 4, 8, BLUE)
        fill(img, 24, 8, 8, 8, GREEN)     # 背面
        fill(img, 0, 8, 8, 8, YELLOW)     # 玩家右侧
        fill(img, 16, 8, 8, 8, CYAN)      # 玩家左侧
        fill(img, 8, 0, 8, 8, MAGENTA)    # 顶
        return img

    def test_front_view(self):
        out = render_skin_3d(self.head_skin(), yaw=0, pitch=0, height=200)
        reds = color_pixels(out, RED)
        blues = color_pixels(out, BLUE)
        self.assertTrue(reds and blues)
        # 玩家右手侧（红）应出现在观察者屏幕左侧
        mean_red = sum(x for x, _ in reds) / len(reds)
        mean_blue = sum(x for x, _ in blues) / len(blues)
        self.assertLess(mean_red, mean_blue)
        # 正面看不到背面/顶面
        self.assertFalse(color_pixels(out, GREEN))
        self.assertFalse(color_pixels(out, MAGENTA))

    def test_back_view(self):
        out = render_skin_3d(self.head_skin(), yaw=180, pitch=0, height=200)
        self.assertTrue(color_pixels(out, GREEN))
        self.assertFalse(color_pixels(out, RED))
        self.assertFalse(color_pixels(out, BLUE))

    def test_side_views(self):
        # yaw=90：玩家右侧（-X，黄）转向观察者
        out = render_skin_3d(self.head_skin(), yaw=90, pitch=0, height=200)
        self.assertTrue(color_pixels(out, YELLOW))
        self.assertFalse(color_pixels(out, CYAN))
        out = render_skin_3d(self.head_skin(), yaw=-90, pitch=0, height=200)
        self.assertTrue(color_pixels(out, CYAN))
        self.assertFalse(color_pixels(out, YELLOW))

    def test_top_view(self):
        out = render_skin_3d(self.head_skin(), yaw=0, pitch=89, height=200)
        self.assertTrue(color_pixels(out, MAGENTA))

    def test_hat_layer_covers_face(self):
        img = self.head_skin()
        fill(img, 40, 8, 8, 8, ORANGE)  # 帽子正面
        out = render_skin_3d(img, yaw=0, pitch=0, height=200)
        self.assertTrue(color_pixels(out, ORANGE))
        self.assertFalse(color_pixels(out, RED))  # 被帽子完全盖住


@unittest.skipUnless(HAVE_QT, "需要 PySide6")
class TestModels(unittest.TestCase):
    def test_slim_arms_narrower(self):
        img = blank_skin()
        fill(img, 44, 20, 4, 12, ORANGE)   # 右臂正面（classic 宽 4）
        fill(img, 36, 52, 4, 12, ORANGE)   # 左臂正面
        classic = render_skin_3d(img, "classic", yaw=0, pitch=0, height=200)
        slim = render_skin_3d(img, "slim", yaw=0, pitch=0, height=200)
        self.assertGreater(len(color_pixels(classic, ORANGE)),
                           len(color_pixels(slim, ORANGE)))

    def test_legacy_mirrors_limbs(self):
        img = blank_skin(h=32)
        fill(img, 44, 20, 4, 12, ORANGE)  # 右臂正面
        out = render_skin_3d(img, yaw=0, pitch=0, height=200)
        pts = color_pixels(out, ORANGE)
        self.assertTrue(pts)
        # 镜像后左右两条手臂都是橙色：屏幕两侧都有命中
        xs = sorted(x for x, _ in pts)
        mid = out.width() / 2
        self.assertTrue(any(x < mid - 5 for x in xs))
        self.assertTrue(any(x > mid + 5 for x in xs))

    def test_hd_skin(self):
        img = blank_skin().scaled(128, 128)
        out = render_skin_3d(img, yaw=30, pitch=15, height=200)
        self.assertIsNotNone(out)

    def test_invalid_texture(self):
        bad = QImage(50, 50, QImage.Format_ARGB32)
        self.assertIsNone(render_skin_3d(bad))
        self.assertIsNone(normalize_texture(QImage()))


PURPLE = QColor(150, 0, 220) if HAVE_QT else None
TEAL = QColor(0, 120, 120) if HAVE_QT else None


@unittest.skipUnless(HAVE_QT, "需要 PySide6")
class TestCape(unittest.TestCase):
    def cape_tex(self):
        img = QImage(64, 32, QImage.Format_ARGB32)
        img.fill(Qt.transparent)
        fill(img, 1, 1, 10, 16, PURPLE)   # 外侧花纹（从背后看到）
        fill(img, 12, 1, 10, 16, TEAL)    # 内侧（贴着身体）
        return img

    def test_back_view_shows_cape_art(self):
        skin = blank_skin()
        out = render_skin_3d(skin, yaw=180, pitch=0, height=200,
                             cape=self.cape_tex())
        self.assertTrue(color_pixels(out, PURPLE))
        self.assertFalse(color_pixels(out, TEAL))

    def test_front_view_cape_hidden_behind_body(self):
        skin = blank_skin()
        out = render_skin_3d(skin, yaw=0, pitch=0, height=200,
                             cape=self.cape_tex())
        self.assertFalse(color_pixels(out, PURPLE))

    def test_invalid_cape_ignored(self):
        skin = blank_skin()
        bad = QImage(50, 20, QImage.Format_ARGB32)
        out = render_skin_3d(skin, yaw=180, pitch=0, height=200, cape=bad)
        self.assertIsNotNone(out)
        from app.skin3d import normalize_cape
        self.assertIsNone(normalize_cape(bad))
        self.assertIsNone(normalize_cape(QImage()))


def _png_64x64() -> bytes:
    """真实的 64x64 透明 PNG（fetch_skin_texture 会校验尺寸）。"""
    import struct
    import zlib

    def chunk(tag, data):
        c = tag + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c))
    ihdr = struct.pack(">IIBBBBB", 64, 64, 8, 6, 0, 0, 0)
    row = b"\x00" + b"\x00\x00\x00\x00" * 64
    idat = zlib.compress(row * 64)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", idat) + chunk(b"IEND", b""))


class TestFetchTextureCape(unittest.TestCase):
    """fetch_skin_texture 的披风附带逻辑（cape_png，拿不到不挡皮肤）。"""

    def test_offline_cape_file_included(self):
        from mclauncher.skin import fetch_skin_texture
        with tempfile.TemporaryDirectory() as td:
            skin_p = Path(td) / "s.png"
            skin_p.write_bytes(_png_64x64())
            cape_p = Path(td) / "c.png"
            cape_p.write_bytes(b"\x89PNG\r\n\x1a\ncape")
            out = fetch_skin_texture({
                "type": "offline", "skin_file": str(skin_p),
                "cape_file": str(cape_p), "skin_model": "slim"})
        self.assertEqual(out["variant"], "slim")
        self.assertEqual(out["cape_png"], b"\x89PNG\r\n\x1a\ncape")

    def test_offline_missing_cape_skipped(self):
        from mclauncher.skin import fetch_skin_texture
        with tempfile.TemporaryDirectory() as td:
            skin_p = Path(td) / "s.png"
            skin_p.write_bytes(_png_64x64())
            out = fetch_skin_texture({
                "type": "offline", "skin_file": str(skin_p),
                "cape_file": str(Path(td) / "nope.png")})
        self.assertNotIn("cape_png", out)

    def test_microsoft_active_cape_downloaded(self):
        from mclauncher import skin as skin_mod
        profile = {
            "skins": [{"active": True, "url": "http://t.example/skin",
                       "variant": "classic"}],
            "capes": [{"active": False, "url": "http://t.example/old"},
                      {"active": True, "url": "http://t.example/cape"}],
        }
        skin_png = _png_64x64()

        def fake_get(url, timeout=0):
            resp = mock.Mock()
            resp.status_code = 200
            resp.content = skin_png if url.endswith("/skin") else b"cape-bytes"
            return resp

        with mock.patch.object(skin_mod, "fetch_ms_profile", return_value=profile), \
             mock.patch("requests.get", fake_get):
            out = skin_mod.fetch_skin_texture(
                {"type": "microsoft", "access_token": "tok"})
        self.assertEqual(out["png"], skin_png)
        self.assertEqual(out["cape_png"], b"cape-bytes")

    def test_cape_download_failure_does_not_break_skin(self):
        import requests as requests_mod
        from mclauncher import skin as skin_mod
        profile = {
            "skins": [{"active": True, "url": "http://t.example/skin",
                       "variant": "slim"}],
            "capes": [{"active": True, "url": "http://t.example/cape"}],
        }
        skin_png = _png_64x64()

        def fake_get(url, timeout=0):
            if url.endswith("/cape"):
                raise requests_mod.ConnectionError("boom")
            resp = mock.Mock()
            resp.status_code = 200
            resp.content = skin_png
            return resp

        with mock.patch.object(skin_mod, "fetch_ms_profile", return_value=profile), \
             mock.patch("requests.get", fake_get):
            out = skin_mod.fetch_skin_texture(
                {"type": "microsoft", "access_token": "tok"})
        self.assertEqual(out["variant"], "slim")
        self.assertNotIn("cape_png", out)


if __name__ == "__main__":
    unittest.main()
