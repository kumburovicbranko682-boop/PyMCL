# -*- coding: utf-8 -*-
"""widgets.py — 公共视觉组件：图标磁贴、胶囊徽章、渐变 Banner。"""

import math
import os

from PySide6.QtCore import QObject, QRectF, QRunnable, Qt, QThreadPool, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QLinearGradient, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel, CaptionLabel, ComboBox, LineEdit, MessageBoxBase, StrongBodyLabel, SubtitleLabel,
    isDarkTheme,
)
from mclauncher.i18n import tr


class InputDialog(MessageBoxBase):
    """Fluent 风格的单行输入对话框。"""

    def __init__(self, title: str, label: str = "", text: str = "",
                 placeholder: str = "", parent=None):
        super().__init__(parent)
        self.viewLayout.addWidget(SubtitleLabel(title, self))
        if label:
            self.viewLayout.addWidget(BodyLabel(label, self))
        self.edit = LineEdit(self)
        self.edit.setText(text)
        if placeholder:
            self.edit.setPlaceholderText(placeholder)
        self.viewLayout.addWidget(self.edit)
        self.yesButton.setText(tr("确定"))
        self.cancelButton.setText(tr("取消"))
        self.widget.setMinimumWidth(380)
        self.edit.setFocus()

    def value(self) -> str:
        return self.edit.text().strip()


def prompt_feedback_consent(parent) -> bool:
    """首次（或未同意时）弹窗，必须手动点同意才会上传。"""
    from qfluentwidgets import MessageBox
    from mclauncher import feedback as fb
    box = MessageBox(
        tr("是否上传诊断数据"),
        tr("第一次打开需要你亲自选择。\n\n"
        "同意后才会向开发者上传：\n"
        "· 你提交的反馈内容\n"
        "· 本机配置（CPU / 内存 / 显卡 / Java / 实例）\n\n"
        "暂不同意则不会上传，以后可在设置里更改。"),
        parent,
    )
    box.yesButton.setText(tr("同意"))
    box.cancelButton.setText(tr("暂不同意"))
    ok = bool(box.exec())
    fb.set_consent(ok)
    if ok:
        fb.start_heartbeat()
    else:
        fb.stop_heartbeat(send_offline=False)
    return ok


class ComboDialog(MessageBoxBase):
    """Fluent 风格的下拉选择对话框。"""

    def __init__(self, title: str, label: str = "", items=None, current: str = "",
                 parent=None):
        super().__init__(parent)
        self.viewLayout.addWidget(SubtitleLabel(title, self))
        if label:
            hint = BodyLabel(label, self)
            hint.setWordWrap(True)
            self.viewLayout.addWidget(hint)
        self.combo = ComboBox(self)
        self.combo.addItems(list(items or []))
        if current and current in (items or []):
            self.combo.setCurrentText(current)
        self.viewLayout.addWidget(self.combo)
        self.yesButton.setText(tr("确定"))
        self.cancelButton.setText(tr("取消"))
        self.widget.setMinimumWidth(420)

    def value(self) -> str:
        return self.combo.currentText()


class DeviceCodeDialog(MessageBoxBase):
    """微软设备代码登录提示。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.viewLayout.addWidget(SubtitleLabel(tr("微软账号登录"), self))
        self.hint = BodyLabel(tr("正在获取登录代码…"), self)
        self.hint.setWordWrap(True)
        self.code = StrongBodyLabel("------", self)
        self.uri = BodyLabel("", self)
        self.uri.setWordWrap(True)
        self.viewLayout.addWidget(self.hint)
        self.viewLayout.addWidget(self.code)
        self.viewLayout.addWidget(self.uri)
        self.yesButton.setText(tr("打开浏览器"))
        self.cancelButton.setText(tr("关闭"))
        self.widget.setMinimumWidth(420)
        self._uri = ""
        try:
            self.yesButton.clicked.disconnect()
        except TypeError:
            pass
        self.yesButton.clicked.connect(self._open)

    def show_code(self, code: str, uri: str):
        self._uri = uri
        self.code.setText(code)
        self.uri.setText(uri)
        self.hint.setText(tr("请在浏览器打开下面的地址并输入代码："))

    def show_status(self, text: str):
        self.hint.setText(text)

    def _open(self):
        if self._uri:
            QDesktopServices.openUrl(QUrl(self._uri))


PALETTE = [
    "#2E9B6B", "#7C5CD6", "#3E7C4F", "#E8862E",
    "#D95568", "#2E9FB8", "#8A6FBD", "#5B8C5A",
]


def pick_color(name: str) -> str:
    return PALETTE[hash(name) % len(PALETTE)]


def grid_columns(scroll, page, card_w: int, spacing: int = 12, gutter: int = 8) -> int:
    """按滚动区**可视宽度**算卡片网格该放几列。

    不能拿页面自身的 width()：页面左右各有 28px 边距、网格右侧留 8px、
    还有一条竖直滚动条，这些地方都摆不下卡片。按页面宽算会多算出一列，
    网格被顶宽，反而冒出一条横向滚动条。

    viewport 在页面首次构造、还没布局时宽度不可信，这时退回按页面宽度扣掉固定边距估算；
    之后 resizeEvent 会再算一次，拿到的就是真实值。
    """
    avail = scroll.viewport().width() if scroll is not None else 0
    if avail <= card_w:
        avail = page.width() - 56
    avail -= gutter
    if avail <= 0:
        return 1
    return max(1, (avail + spacing) // (card_w + spacing))


class IconTile(QWidget):
    """圆角彩色磁贴，中间显示一个字符。"""

    def __init__(self, text: str, color: str | None = None, size: int = 44, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._color = QColor(color or pick_color(text))
        label = QLabel(text[:1].upper() if text else "?", self)
        label.setAlignment(Qt.AlignCenter)
        label.setStyleSheet("color: white; background: transparent;"
                            f"font-size: {int(size * 0.42)}px; font-weight: 700;")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(label)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 10, 10)
        painter.fillPath(path, self._color)
        super().paintEvent(event)


class Pill(QLabel):
    """WinUI 风格胶囊徽章。

    改颜色必须走 `set_color()`，不要在外面直接 `setStyleSheet()`：
    `paintEvent` 发现主题变了会重跑 `_apply_style()`，外部样式会被无声刷掉，
    表现就是「切一次深浅色，状态色全丢」。
    """

    def __init__(self, text: str, color: str = "#2E9B6B", parent=None, solid: bool = False):
        super().__init__(text, parent)
        self.setProperty("pymclKeepBg", True)
        self._color_hex = color
        self._solid = solid
        self._style_ver = -1
        self._apply_style()

    def set_color(self, color: str, solid: bool | None = None):
        if solid is not None:
            self._solid = solid
        self._color_hex = color
        self._apply_style()

    def _apply_style(self):
        color = self._color_hex
        if self._solid:
            self.setStyleSheet(
                f"color: white; background-color: {color};"
                "border-radius: 8px; padding: 1px 8px; font-size: 11px; font-weight: 600;"
            )
            return
        bg = QColor(color)
        bg.setAlpha(38 if not isDarkTheme() else 60)
        self.setStyleSheet(
            f"color: {color}; background-color: rgba({bg.red()},{bg.green()},{bg.blue()},{bg.alpha()});"
            "border-radius: 9px; padding: 2px 10px; font-size: 12px; font-weight: 600;"
        )

    def paintEvent(self, event):
        from .pcl_chrome import Theme
        if self._style_ver != Theme._version:
            self._style_ver = Theme._version
            self._apply_style()
        super().paintEvent(event)


class _ThumbHub(QObject):
    """缩略图线程池 → UI 线程的回传通道。worker 线程 emit，槽在主线程执行。"""

    loaded = Signal(str, str)  # url, 本地路径（失败为空串）


_THUMB_HUB = None
_THUMB_POOL = None
_THUMB_PIXCACHE: dict[str, QPixmap] = {}
_THUMB_PIXCACHE_CAP = 240


def _thumb_hub() -> _ThumbHub:
    global _THUMB_HUB
    if _THUMB_HUB is None:
        _THUMB_HUB = _ThumbHub()
    return _THUMB_HUB


def _thumb_pool() -> QThreadPool:
    global _THUMB_POOL
    if _THUMB_POOL is None:
        _THUMB_POOL = QThreadPool()
        _THUMB_POOL.setMaxThreadCount(4)
        _THUMB_POOL.setExpiryTimeout(20000)
    return _THUMB_POOL


def _pixcache_get(key: str):
    return _THUMB_PIXCACHE.get(key)


def _pixcache_put(key: str, pix: QPixmap):
    cache = _THUMB_PIXCACHE
    cache[key] = pix
    while len(cache) > _THUMB_PIXCACHE_CAP:
        cache.pop(next(iter(cache)), None)


class _ThumbJob(QRunnable):
    def __init__(self, url: str):
        super().__init__()
        self._url = url
        self.setAutoDelete(True)

    def run(self):
        path = ""
        try:
            from mclauncher.thumbnails import ensure_thumb
            path = ensure_thumb(self._url) or ""
        except Exception:
            path = ""
        _thumb_hub().loaded.emit(self._url, path)


class ThumbnailTile(QWidget):
    """圆角缩略图磁贴，从 URL 加载图片。

    加载顺序：内存像素缓存 → 本地缓存文件（同步，纯磁盘）→ 线程池
    异步下载后回主线程贴图。绝不在 UI 线程发起网络请求——搜索结果
    一页二三十行，以前每行构造时同步 `ensure_thumb`（超时 20s），
    首次搜索整页假死就是这么来的。
    """

    def __init__(self, text: str, thumb_url: str, size: int = 52, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._pixmap = None
        self._thumb_url = thumb_url
        self._text = text[:1].upper() if text else "?"
        self._size = size
        self._loaded = False
        self._color = QColor(pick_color(text))
        self._load_thumb()

    def _set_pixmap_from(self, path: str) -> bool:
        pix = QPixmap(path)
        if pix.isNull():
            return False
        pix = pix.scaled(self._size, self._size,
                         Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self._pixmap = pix
        self._loaded = True
        if self._thumb_url:
            _pixcache_put(f"{self._thumb_url}|{self._size}", pix)
        self.update()
        return True

    def _load_thumb(self):
        if not self._thumb_url:
            return
        cached = _pixcache_get(f"{self._thumb_url}|{self._size}")
        if cached is not None:
            self._pixmap = cached
            self._loaded = True
            return
        try:
            from mclauncher.thumbnails import thumb_path
            local = thumb_path(self._thumb_url)
        except Exception:
            local = ""
        if local and os.path.isfile(local) and self._set_pixmap_from(local):
            return
        hub = _thumb_hub()
        hub.loaded.connect(self._on_thumb_loaded)
        # 连接以本控件为接收者，行删除（C++ 销毁）后自动断；但 Python
        # 包装器和 _pixmap 会被 bound method 拽住，这里显式断干净，
        # 不然每搜一次页就攒一批 52px pixmap 释放不掉。
        self.destroyed.connect(lambda *_: self._disconnect_thumb_hub())
        _thumb_pool().start(_ThumbJob(self._thumb_url))

    def _disconnect_thumb_hub(self):
        try:
            _thumb_hub().loaded.disconnect(self._on_thumb_loaded)
        except (TypeError, RuntimeError):
            pass

    def _on_thumb_loaded(self, url: str, path: str):
        if url != self._thumb_url or not path:
            return
        try:
            if self._loaded:
                return
            self._set_pixmap_from(path)
        except RuntimeError:
            pass  # C++ 对象已随所在行一起销毁

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, 10, 10)
        if self._pixmap is not None:
            painter.setClipPath(path)
            painter.drawPixmap(self.rect(), self._pixmap)
        else:
            painter.fillPath(path, self._color)
            painter.setPen(Qt.NoPen)
            painter.setClipPath(path)
            font = painter.font()
            font.setPixelSize(int(self._size * 0.42))
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(QColor("white"))
            painter.drawText(self.rect(), Qt.AlignCenter, self._text)
        super().paintEvent(event)


class BannerWidget(QFrame):
    """启动页渐变 Hero 横幅。

    右上两团白色微光会缓慢漂移，让横幅有「活着」的感觉。

    性能：只重绘两团光所在的脏区（约横幅 1/4 面积），11fps 足够
    平滑——之前 25fps 全件重绘在低端机上肉眼可见地拖慢整个页面。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(180)
        self._g1 = QColor("#123B2A")
        self._g2 = QColor("#3E7C4F")
        self._g3 = QColor("#7BB661")
        self._glow_phase = 0.0
        self._glow_timer = QTimer(self)
        self._glow_timer.setInterval(90)
        self._glow_timer.timeout.connect(self._tick_glow)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(32, 26, 32, 26)

        left = QVBoxLayout()
        self.kicker = QLabel("MINECRAFT")
        self.kicker.setStyleSheet("color: rgba(255,255,255,170); background: transparent;"
                                  "font-size: 12px; letter-spacing: 4px; font-weight: 600;")
        self.title = QLabel(tr("准备启程"))
        self.title.setStyleSheet("color: white; background: transparent;"
                                 "font-size: 30px; font-weight: 700;")
        self.subtitle = QLabel("")
        self.subtitle.setStyleSheet("color: rgba(255,255,255,190); background: transparent;"
                                    "font-size: 13px;")
        left.addWidget(self.kicker)
        left.addSpacing(6)
        left.addWidget(self.title)
        left.addSpacing(6)
        left.addWidget(self.subtitle)
        left.addStretch(1)

        layout.addLayout(left, 1)
        self.right_area = QVBoxLayout()
        self.right_area.setSpacing(10)
        layout.addLayout(self.right_area)

    def showEvent(self, e):
        super().showEvent(e)
        from .motion_prefs import ui_motion_ok
        if ui_motion_ok():
            self._glow_timer.start()

    def hideEvent(self, e):
        super().hideEvent(e)
        self._glow_timer.stop()

    def _glow_rects(self):
        """两团光当前的包围盒（含 24px 漂移余量）。"""
        from PySide6.QtCore import QRect
        ph = self._glow_phase * 2 * math.pi
        dx = math.sin(ph) * 16
        dy = math.cos(ph * 0.7) * 10
        r = self.rect()
        e1 = QRect(int(r.right() - 260 + dx) - 24, int(-120 + dy) - 24, 320 + 48, 320 + 48)
        e2 = QRect(int(r.right() - 420 - dx) - 24,
                   int(r.bottom() - 140 - dy) - 24, 220 + 48, 220 + 48)
        return e1, e2

    def _tick_glow(self):
        old1, old2 = self._glow_rects()
        self._glow_phase = (self._glow_phase + 0.006) % 1.0
        new1, new2 = self._glow_rects()
        self.update(old1.united(new1).intersected(self.rect()))
        self.update(new2.united(old2).intersected(self.rect()))

    def set_info(self, title: str, subtitle: str):
        self.title.setText(title)
        self.subtitle.setText(subtitle)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, 12, 12)

        grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
        grad.setColorAt(0.0, self._g1)
        grad.setColorAt(0.55, self._g2)
        grad.setColorAt(1.0, self._g3)
        painter.fillPath(path, grad)

        painter.setClipPath(path)
        painter.setPen(Qt.NoPen)
        glow = QColor(255, 255, 255, 18)
        painter.setBrush(glow)
        ph = self._glow_phase * 2 * math.pi
        dx = math.sin(ph) * 16
        dy = math.cos(ph * 0.7) * 10
        painter.drawEllipse(int(rect.right() - 260 + dx), int(-120 + dy), 320, 320)
        painter.drawEllipse(int(rect.right() - 420 - dx),
                            int(rect.bottom() - 140 - dy), 220, 220)
        painter.setClipping(False)


class EmptyState(QWidget):
    """空状态提示。可选 action：给「现在该点哪里」一个真按钮。"""

    def __init__(self, icon, text: str, parent=None,
                 action_text: str = "", on_action=None):
        super().__init__(parent)
        self._icon = icon
        self._text = text
        self._style_ver = -1
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)
        self._icon_label = QLabel()
        self._icon_label.setAlignment(Qt.AlignCenter)
        self._text_label = CaptionLabel(text)
        self._text_label.setAlignment(Qt.AlignCenter)
        layout.addStretch(1)
        layout.addWidget(self._icon_label)
        layout.addSpacing(10)
        layout.addWidget(self._text_label)
        self.action_btn = None
        if action_text and callable(on_action):
            from qfluentwidgets import PushButton
            self.action_btn = PushButton(action_text)
            self.action_btn.clicked.connect(on_action)
            layout.addSpacing(12)
            layout.addWidget(self.action_btn, 0, Qt.AlignHCenter)
        layout.addStretch(1)
        self._apply_style()

    def restyle(self):
        self._apply_style()

    def _apply_style(self):
        from .pcl_chrome import Theme
        self._icon_label.setPixmap(self._icon.icon(color=QColor(140, 140, 140)).pixmap(48, 48))
        self._text_label.setStyleSheet(f"color: {Theme.muted}; background: transparent;")
