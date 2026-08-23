# -*- coding: utf-8 -*-
"""motion.py — 通用微动效工具：淡入淡出、滑入、数值补间、缩放脉冲。

全部尊重系统动画偏好（motion_prefs.ui_motion_ok）：系统关闭窗口动画时
直接走终态。动画对象挂在触发控件名下避免被 GC 提前回收；结束后自动
摘掉 graphics effect（effect 常驻会拖慢绘制）。
"""

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QVariantAnimation
from PySide6.QtGui import QVector3D
from PySide6.QtWidgets import QGraphicsOpacityEffect, QGraphicsScale
from qfluentwidgets import ProgressBar

from .motion_prefs import ui_motion_ok

# 补间动画没有天然宿主控件时的寄存处（防 GC）
_TWEENS: list = []


def _keep(widget, *anims):
    box = getattr(widget, "_mcl_anims", None)
    if box is None:
        box = []
        widget._mcl_anims = box
    box.extend(anims)

    def _drop():
        for a in anims:
            if a in box:
                box.remove(a)
    for a in anims:
        a.finished.connect(_drop)


def fade(widget, start: float = 0.0, end: float = 1.0, ms: int = 180,
         on_done=None):
    """透明度过渡。结束移除 effect。"""
    if not ui_motion_ok() or not widget.isVisible():
        if on_done:
            on_done()
        return
    eff = QGraphicsOpacityEffect(widget)
    widget.setGraphicsEffect(eff)
    a = QPropertyAnimation(eff, b"opacity", widget)
    a.setDuration(ms)
    a.setStartValue(start)
    a.setEndValue(end)
    a.setEasingCurve(QEasingCurve.OutCubic)

    def done():
        widget.setGraphicsEffect(None)
        if on_done:
            on_done()
    a.finished.connect(done)
    _keep(widget, a)
    a.start()


def slide_in(widget, dy: int = -10, ms: int = 200):
    """从上方滑入 + 淡入（浮层工具条用）。结束后 y 恢复原值。"""
    if not ui_motion_ok():
        return
    x0, y0 = widget.x(), widget.y()
    eff = QGraphicsOpacityEffect(widget)
    widget.setGraphicsEffect(eff)
    p = QVariantAnimation(widget)
    p.setDuration(ms)
    p.setStartValue(float(-dy))
    p.setEndValue(0.0)
    p.setEasingCurve(QEasingCurve.OutCubic)
    p.valueChanged.connect(lambda v: widget.move(x0, y0 + int(v)))
    o = QPropertyAnimation(eff, b"opacity", widget)
    o.setDuration(ms)
    o.setStartValue(0.0)
    o.setEndValue(1.0)
    o.setEasingCurve(QEasingCurve.OutCubic)
    o.finished.connect(lambda: widget.setGraphicsEffect(None))
    _keep(widget, p, o)
    p.start()
    o.start()


def tween(setter, start, end, ms: int = 240, on_done=None):
    """数值补间：setter(v) 按帧调用（高度展开、位移等）。"""
    if not ui_motion_ok() or start == end:
        setter(end)
        if on_done:
            on_done()
        return None
    a = QVariantAnimation()
    a.setDuration(ms)
    a.setStartValue(start)
    a.setEndValue(end)
    a.setEasingCurve(QEasingCurve.OutCubic)
    a.valueChanged.connect(lambda v: setter(v))
    if on_done:
        a.finished.connect(on_done)
    _TWEENS.append(a)
    a.finished.connect(lambda: a in _TWEENS and _TWEENS.remove(a))
    a.start()
    return a


def pop(widget, scale: float = 1.35, ms: int = 260):
    """缩放脉冲（角标计数变化）：transform 缩放，不动布局。

    上一个脉冲还没放完就跳过（下载计数高频变化时会连成一片抖动）。
    """
    if not ui_motion_ok() or not widget.isVisible():
        return
    if getattr(widget, "_mcl_anims", None):
        return
    sc = QGraphicsScale(widget)
    sc.setOrigin(QVector3D(widget.width() / 2, widget.height() / 2, 0))
    widget.setGraphicsEffect(sc)
    gx = QPropertyAnimation(sc, b"x", widget)
    gy = QPropertyAnimation(sc, b"y", widget)
    for a in (gx, gy):
        a.setDuration(ms)
        a.setStartValue(scale)
        a.setEndValue(1.0)
        a.setEasingCurve(QEasingCurve.OutBack)
    gx.finished.connect(lambda: widget.setGraphicsEffect(None))
    _keep(widget, gx, gy)
    gx.start()
    gy.start()


class SmoothProgressBar(ProgressBar):
    """进度条补间：setValue 的变化走 240ms 缓动，进度增长不再跳格。

    value() 语义不变（立即反映目标值），只有绘制是渐进的。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._shown = super().value()
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(240)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(
            lambda v: SmoothProgressBar.setValue(self, int(v)))

    def setValue(self, v):
        v = int(v)
        anim = getattr(self, "_anim", None)
        if anim is None:  # 父类构造期间会先调 setValue(0)
            ProgressBar.setValue(self, v)
            self._shown = v
            return
        if not ui_motion_ok():
            anim.stop()
            self._shown = v
            ProgressBar.setValue(self, v)
            return
        if anim.state() == QVariantAnimation.Running:
            anim.stop()          # 密集更新：直接跳终值，不再重启动画
            self._shown = v
            ProgressBar.setValue(self, v)
            return
        anim.setStartValue(self._shown)
        anim.setEndValue(v)
        self._shown = v
        anim.start()
