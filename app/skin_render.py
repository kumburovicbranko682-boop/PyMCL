# -*- coding: utf-8 -*-
"""皮肤本地 2D 渲染（正面纸娃娃），不依赖 crafatar / mc-heads 等第三方渲染站。

支持 64x64（1.8+，含左右肢与外层）与 64x32（旧版，左侧镜像右侧）纹理，
经典（4px 臂）与纤细（3px 臂）模型。
"""
from __future__ import annotations

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QImage, QPainter, QPixmap


def _copy(painter: QPainter, tex: QImage, sx, sy, w, h, dx, dy,
          mirror: bool = False):
    part = tex.copy(QRect(sx, sy, w, h))
    if mirror:
        if hasattr(part, "flipped"):  # Qt 6.9+，mirrored 已弃用
            part = part.flipped(Qt.Horizontal)
        else:
            part = part.mirrored(True, False)
    painter.drawImage(dx, dy, part)


def render_front(png_bytes: bytes, variant: str = "classic",
                 height: int = 256) -> QPixmap:
    """把 64x64 / 64x32 皮肤纹理合成为正面全身像。

    返回按 height 等比放大（最近邻，保持像素感）的 QPixmap；
    纹理无效时返回空 QPixmap。
    """
    tex = QImage()
    if not png_bytes or not tex.loadFromData(png_bytes) or tex.width() != 64:
        return QPixmap()
    if tex.format() != QImage.Format_ARGB32:
        tex = tex.convertToFormat(QImage.Format_ARGB32)
    legacy = tex.height() == 32
    slim = str(variant or "").lower() == "slim"
    arm_w = 3 if slim else 4
    body_w = 8 + 2 * arm_w
    canvas = QImage(body_w, 32, QImage.Format_ARGB32)
    canvas.fill(Qt.transparent)
    p = QPainter(canvas)
    try:
        torso_x = arm_w  # 身体左缘
        # 头 + 帽
        head_x = torso_x  # 头与身体同宽对齐
        _copy(p, tex, 8, 8, 8, 8, head_x, 0)
        _copy(p, tex, 40, 8, 8, 8, head_x, 0)
        # 身体
        _copy(p, tex, 20, 20, 8, 12, torso_x, 8)
        if not legacy:
            _copy(p, tex, 20, 36, 8, 12, torso_x, 8)
        # 右臂（观察者视角在左）
        _copy(p, tex, 44, 20, arm_w, 12, 0, 8)
        if not legacy:
            _copy(p, tex, 44, 36, arm_w, 12, 0, 8)
        # 左臂
        if legacy:
            _copy(p, tex, 44, 20, arm_w, 12, torso_x + 8, 8, mirror=True)
        else:
            _copy(p, tex, 36, 52, arm_w, 12, torso_x + 8, 8)
            _copy(p, tex, 52, 52, arm_w, 12, torso_x + 8, 8)
        # 右腿（观察者视角在左）
        leg_left = torso_x  # 双腿各 4px，正好占身体宽度
        _copy(p, tex, 4, 20, 4, 12, leg_left, 20)
        if not legacy:
            _copy(p, tex, 4, 36, 4, 12, leg_left, 20)
        # 左腿
        if legacy:
            _copy(p, tex, 4, 20, 4, 12, leg_left + 4, 20, mirror=True)
        else:
            _copy(p, tex, 20, 52, 4, 12, leg_left + 4, 20)
            _copy(p, tex, 4, 52, 4, 12, leg_left + 4, 20)
    finally:
        p.end()
    scale = max(1, int(height) // 32)
    scaled = canvas.scaled(body_w * scale, 32 * scale,
                           Qt.KeepAspectRatio, Qt.FastTransformation)
    return QPixmap.fromImage(scaled)
