# -*- coding: utf-8 -*-
"""3D 皮肤预览：纯 QPainter 正交投影渲染，可拖动旋转（对齐 PCL2 / HMCL）。

原理：正交投影下长方体的每个面都投影成平行四边形，QPainter 的仿射
变换可以精确完成「纹理矩形 → 平行四边形」贴图，无需 OpenGL。
画家算法按深度排序面片，先画远面再画近面，第二层（外套/帽子等）
天然盖在内层之上。支持 64x64 现代格式、64x32 旧格式（左肢镜像右肢）、
HD 皮肤（64 的整数倍）与 slim（细手臂）模型。
"""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap, QTransform
from PySide6.QtWidgets import QWidget

from mclauncher.i18n import tr


def _boxes(model: str, legacy: bool):
    """返回体块列表 [(center, size, uv, inflate, mirror), ...]。

    坐标：X 向玩家左手，Y 向上，Z 朝观察者（玩家面向 +Z）；
    单位 = 皮肤纹理像素。mirror=True 表示整个体块水平镜像
    （旧格式左臂/左腿复用右侧纹理）。
    """
    aw = 3 if model == "slim" else 4  # 手臂宽
    boxes = [
        ((0.0, 28.0, 0.0), (8, 8, 8), (0, 0), 0.0, False),        # 头
        ((0.0, 18.0, 0.0), (8, 12, 4), (16, 16), 0.0, False),     # 身体
        ((-(4 + aw / 2), 18.0, 0.0), (aw, 12, 4), (40, 16), 0.0, False),  # 右臂
        ((-2.0, 6.0, 0.0), (4, 12, 4), (0, 16), 0.0, False),      # 右腿
    ]
    if legacy:
        boxes += [
            ((4 + aw / 2, 18.0, 0.0), (aw, 12, 4), (40, 16), 0.0, True),  # 左臂=镜像
            ((2.0, 6.0, 0.0), (4, 12, 4), (0, 16), 0.0, True),            # 左腿=镜像
        ]
    else:
        boxes += [
            ((4 + aw / 2, 18.0, 0.0), (aw, 12, 4), (32, 48), 0.0, False),  # 左臂
            ((2.0, 6.0, 0.0), (4, 12, 4), (16, 48), 0.0, False),           # 左腿
            # 第二层
            ((0.0, 18.0, 0.0), (8, 12, 4), (16, 32), 0.25, False),         # 外套
            ((-(4 + aw / 2), 18.0, 0.0), (aw, 12, 4), (40, 32), 0.25, False),
            ((4 + aw / 2, 18.0, 0.0), (aw, 12, 4), (48, 48), 0.25, False),
            ((-2.0, 6.0, 0.0), (4, 12, 4), (0, 32), 0.25, False),
            ((2.0, 6.0, 0.0), (4, 12, 4), (0, 48), 0.25, False),
        ]
    boxes.append(((0.0, 28.0, 0.0), (8, 8, 8), (32, 0), 0.5, False))  # 帽子层
    return boxes


def _faces_of(center, size, uv, inflate):
    """一个体块的 6 个面。每面 (P0, U, V, 纹理矩形 (ru, rv, rw, rh))。

    P0 是纹理区左上角纹素对应的 3D 点，U/V 是纹理 u/v 方向的 3D 边向量。
    区块布局（u,v 为体块纹理原点，w,h,d 为体块尺寸）：
        上(u+d,v)  下(u+d+w,v)
        右侧(u,v+d)  正面(u+d,v+d)  左侧(u+d+w,v+d)  背面(u+d+w+d,v+d)
    """
    cx, cy, cz = center
    w, h, d = size
    u, v = uv
    x0, x1 = cx - w / 2 - inflate, cx + w / 2 + inflate
    y0, y1 = cy - h / 2 - inflate, cy + h / 2 + inflate
    z0, z1 = cz - d / 2 - inflate, cz + d / 2 + inflate
    W, H, D = x1 - x0, y1 - y0, z1 - z0
    return [
        # 正面 (+Z)：u=0 在玩家右手侧（-X，观察者屏幕左）
        ((x0, y1, z1), (W, 0, 0), (0, -H, 0), (u + d, v + d, w, h)),
        # 背面 (-Z)：从背后看，u=0 在 +X
        ((x1, y1, z0), (-W, 0, 0), (0, -H, 0), (u + d + w + d, v + d, w, h)),
        # 玩家右侧 (-X)：u=0 在背面 (z0)，与正面区左缘共边
        ((x0, y1, z0), (0, 0, D), (0, -H, 0), (u, v + d, d, h)),
        # 玩家左侧 (+X)：u=0 在正面 (z1)
        ((x1, y1, z1), (0, 0, -D), (0, -H, 0), (u + d + w, v + d, d, h)),
        # 顶 (+Y)：v 向正面增大（v=0 在后脑勺）
        ((x0, y1, z0), (W, 0, 0), (0, 0, D), (u + d, v, w, d)),
        # 底 (-Y)：Minecraft 惯例上下翻转
        ((x0, y0, z1), (W, 0, 0), (0, 0, -D), (u + d + w, v, w, d)),
    ]


def _extract_face(img: QImage, rect, s: int, mirror: bool) -> QImage:
    ru, rv, rw, rh = rect
    part = img.copy(int(ru * s), int(rv * s), int(rw * s), int(rh * s))
    if mirror:
        if hasattr(part, "flipped"):  # Qt 6.9+，mirrored() 已弃用
            part = part.flipped(Qt.Horizontal)
        else:
            part = part.mirrored(True, False)
    return part


def _mirror_swap(faces):
    """体块整体水平镜像：正/背/顶/底纹理左右翻转，左右两侧面互换纹理。"""
    front, back, right, left, top, bottom = faces
    return [
        (front[0], front[1], front[2], front[3], True),
        (back[0], back[1], back[2], back[3], True),
        (right[0], right[1], right[2], left[3], True),   # 右面用左区
        (left[0], left[1], left[2], right[3], True),     # 左面用右区
        (top[0], top[1], top[2], top[3], True),
        (bottom[0], bottom[1], bottom[2], bottom[3], True),
    ]


_CAPE_TILT = 10.0  # 披风自然下垂的倾角（度）


def _cape_faces():
    """披风的 6 个面（已就位：背在身后、绕 Y 转 180°、带下垂倾角）。

    披风纹理 64x32，盒体 10x16x1，UV 原点 (0,0)。游戏里从玩家背后
    看到的花纹是盒体的「正面」区 (1,1,10,16)，因此盒体要转 180°。
    """
    import math as _m
    center = (0.0, 16.0, -3.0)
    pivot_y = (center[0], center[1], center[2])
    tilt = _m.radians(_CAPE_TILT)
    sa, ca = _m.sin(tilt), _m.cos(tilt)
    py, pz = 24.0, -3.0  # 悬挂点（肩部）

    def place_point(p):
        # 绕 Y 转 180°（关于盒体中心）
        x = 2 * pivot_y[0] - p[0]
        z = 2 * pivot_y[2] - p[2]
        y = p[1]
        # 绕 X 倾斜（关于肩部悬挂点）
        y2 = py + (y - py) * ca - (z - pz) * sa
        z2 = pz + (y - py) * sa + (z - pz) * ca
        return (x, y2, z2)

    def place_dir(v):
        x, y, z = -v[0], v[1], -v[2]
        return (x, y * ca - z * sa, y * sa + z * ca)

    out = []
    for p0, uvec, vvec, rect in _faces_of(center, (10, 16, 1), (0, 0), 0.0):
        out.append((place_point(p0), place_dir(uvec), place_dir(vvec), rect))
    return out


def normalize_cape(img: QImage):
    """校验披风纹理（64x32 或其整数倍）。返回 (ARGB32 图, 缩放系数)；非法 None。"""
    if img is None or img.isNull() or img.width() < 64 or img.width() % 64:
        return None
    s = img.width() // 64
    if img.height() != 32 * s:
        return None
    return img.convertToFormat(QImage.Format_ARGB32), s


def normalize_texture(img: QImage):
    """校验并归一化皮肤纹理。返回 (ARGB32 图, 缩放系数 s, 是否旧格式)；非法返回 None。"""
    if img is None or img.isNull() or img.width() < 64 or img.width() % 64:
        return None
    s = img.width() // 64
    legacy = img.height() == 32 * s
    if not legacy and img.height() != 64 * s:
        return None
    return img.convertToFormat(QImage.Format_ARGB32), s, legacy


# 预放大倍数：先把每个面的纹理最近邻放大，再做仿射平滑采样，像素边界干净
_PRESCALE = 4
# 超采样倍数：整图放大渲染后平滑缩小，消除面片接缝锯齿
_SUPERSAMPLE = 2


def render_skin_3d(img: QImage, model: str = "classic",
                   yaw: float = 30.0, pitch: float = 15.0,
                   height: int = 256, width: int = 0,
                   cape: QImage | None = None) -> QImage | None:
    """渲染皮肤 3D 视图。yaw 绕 Y 轴（度），pitch 抬头/低头（度）。

    cape 传披风纹理（64x32 或整数倍）时一并渲染在背后。
    返回 ARGB32 QImage（width 默认 height*0.75），纹理非法返回 None。
    """
    got = normalize_texture(img)
    if not got:
        return None
    tex, s, legacy = got
    cape_got = normalize_cape(cape) if cape is not None else None
    model = "slim" if model == "slim" else "classic"
    if legacy:
        model = "classic"  # 旧格式没有 slim

    ya, pa = math.radians(yaw), math.radians(pitch)
    sy, cy_ = math.sin(ya), math.cos(ya)
    sp, cp = math.sin(pa), math.cos(pa)

    def rotate(p):
        x, y, z = p
        # 绕 Y（偏航）再绕 X（俯仰）
        x1 = x * cy_ + z * sy
        z1 = -x * sy + z * cy_
        y2 = y * cp - z1 * sp
        z2 = y * sp + z1 * cp
        return x1, y2, z2

    out_h = max(64, int(height)) * _SUPERSAMPLE
    out_w = (int(width) if width else int(height * 0.75)) * _SUPERSAMPLE
    # 模型（含帽子层膨胀、披风）任意转角下的最大半径 ~19；留边距
    scale = min(out_h / 39.0, out_w / 22.0)
    cx_s, cy_s = out_w / 2.0, out_h / 2.0
    y_mid = 16.0  # 模型垂直中心（脚 0 → 头顶 32）

    sources = []
    for center, size, uv, inflate, mirror in _boxes(model, legacy):
        box_faces = _faces_of(center, size, uv, inflate)
        if mirror:
            entries = _mirror_swap(box_faces)
        else:
            entries = [(p0, uvec, vvec, rect, False)
                       for p0, uvec, vvec, rect in box_faces]
        sources += [(p0, uvec, vvec, rect, flip, 0)
                    for p0, uvec, vvec, rect, flip in entries]
    if cape_got:
        sources += [(p0, uvec, vvec, rect, False, 1)
                    for p0, uvec, vvec, rect in _cape_faces()]

    faces = []
    for p0, uvec, vvec, rect, flip, ti in sources:
        p1 = (p0[0] + uvec[0], p0[1] + uvec[1], p0[2] + uvec[2])
        p2 = (p0[0] + vvec[0], p0[1] + vvec[1], p0[2] + vvec[2])
        r0, r1, r2 = rotate(p0), rotate(p1), rotate(p2)

        def to_screen(r):
            return (cx_s + r[0] * scale, cy_s - (r[1] - y_mid) * scale)

        s0, s1, s2 = to_screen(r0), to_screen(r1), to_screen(r2)
        e1 = (s1[0] - s0[0], s1[1] - s0[1])
        e2 = (s2[0] - s0[0], s2[1] - s0[1])
        # 背面剔除：正对观察者时 U 向右、V 向下，叉积为正
        if e1[0] * e2[1] - e1[1] * e2[0] <= 0:
            continue
        depth = (r0[2] + r1[2] + r2[2]) / 3.0
        faces.append((depth, s0, e1, e2, rect, flip, ti))

    canvas = QImage(out_w, out_h, QImage.Format_ARGB32)
    canvas.fill(Qt.transparent)
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
    for _depth, s0, e1, e2, rect, flip, ti in sorted(faces, key=lambda f: f[0]):
        src_img, src_s = (tex, s) if ti == 0 else cape_got
        part = _extract_face(src_img, rect, src_s, flip)
        part = part.scaled(part.width() * _PRESCALE, part.height() * _PRESCALE,
                           Qt.IgnoreAspectRatio, Qt.FastTransformation)
        tw, th = part.width(), part.height()
        if not tw or not th:
            continue
        painter.setTransform(QTransform(
            e1[0] / tw, e1[1] / tw, e2[0] / th, e2[1] / th, s0[0], s0[1]))
        painter.drawImage(QPointF(0, 0), part)
    painter.end()
    return canvas.scaled(out_w // _SUPERSAMPLE, out_h // _SUPERSAMPLE,
                         Qt.IgnoreAspectRatio, Qt.SmoothTransformation)


class SkinView3D(QWidget):
    """可拖动旋转的 3D 皮肤预览控件。滚轮缩放，双击回到初始角度。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._tex: QImage | None = None
        self._cape: QImage | None = None
        self._model = "classic"
        self._yaw = 30.0
        self._pitch = 15.0
        self._zoom = 1.0
        self._drag_at = None
        self._bg = QColor(0, 0, 0, 0)
        self.setCursor(Qt.OpenHandCursor)
        self.setToolTip(tr("拖动旋转，滚轮缩放，双击复位"))

    def set_background(self, color):
        self._bg = QColor(color)
        self.update()

    def set_texture_file(self, path: str, model: str = "classic",
                         cape_file: str = "") -> bool:
        img = QImage(str(path or ""))
        cape = QImage(str(cape_file)) if cape_file else None
        return self.set_texture(img, model, cape)

    def set_texture(self, img: QImage, model: str = "classic",
                    cape: QImage | None = None) -> bool:
        if normalize_texture(img) is None:
            return False
        self._tex = img
        self._cape = cape if (cape is not None
                              and normalize_cape(cape) is not None) else None
        self._model = "slim" if model == "slim" else "classic"
        self.update()
        return True

    def clear(self):
        self._tex = None
        self._cape = None
        self.update()

    # ---- 交互
    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            self._drag_at = ev.position()
            self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, ev):
        if self._drag_at is None:
            return
        delta = ev.position() - self._drag_at
        self._drag_at = ev.position()
        self._yaw = (self._yaw + delta.x() * 0.8) % 360.0
        self._pitch = max(-89.0, min(89.0, self._pitch + delta.y() * 0.5))
        self.update()

    def mouseReleaseEvent(self, ev):
        self._drag_at = None
        self.setCursor(Qt.OpenHandCursor)

    def mouseDoubleClickEvent(self, ev):
        self._yaw, self._pitch, self._zoom = 30.0, 15.0, 1.0
        self.update()

    def wheelEvent(self, ev):
        step = 1.1 if ev.angleDelta().y() > 0 else 1 / 1.1
        self._zoom = max(0.5, min(2.5, self._zoom * step))
        self.update()

    def paintEvent(self, ev):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        if self._bg.alpha():
            painter.setPen(Qt.NoPen)
            painter.setBrush(self._bg)
            painter.drawRoundedRect(self.rect(), 8, 8)
        if self._tex is None:
            painter.end()
            return
        h = max(64, int(self.height() * self._zoom))
        img = render_skin_3d(self._tex, self._model,
                             yaw=self._yaw, pitch=self._pitch,
                             height=h, width=int(self.width() * self._zoom),
                             cape=self._cape)
        if img is not None:
            pix = QPixmap.fromImage(img)
            painter.drawPixmap((self.width() - pix.width()) // 2,
                               (self.height() - pix.height()) // 2, pix)
        painter.end()
