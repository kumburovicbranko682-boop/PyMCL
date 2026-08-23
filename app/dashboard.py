# -*- coding: utf-8 -*-
"""自由布局画布：启动页可自定义 UI 的核心。

DashboardCanvas 是一个绝对定位容器，子项 DashboardCard 按布局文档
（layout_model.LayoutDoc）中的画布比例摆放。支持：

- 查看模式：卡片正常交互，右上角悬浮「编辑布局」入口；
- 编辑模式：整卡拖动、8 向手柄缩放、网格吸附（可调/可关）、
  顶层置前、删除卡片、添加卡片（调色板）、适应窗口、重置布局；
- 所有改动经 layout_changed 信号（300ms 防抖）通知宿主页持久化。

卡片类型由外部注册表提供（见 pages/home_cards.py），画布只负责
框架行为，不关心卡片内容。
"""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)
from qfluentwidgets import (
    BodyLabel, MessageBoxBase, PrimaryPushButton, PushButton, StrongBodyLabel,
    SubtitleLabel, ToolButton,
)
from qfluentwidgets import FluentIcon as FIF

from mclauncher.i18n import tr

from .layout_model import LayoutDoc, LayoutItem, default_doc, min_size_for
from . import motion

GRID_CHOICES = [0, 4, 8, 16, 24]

# 新增卡片时各类型的默认几何（画布比例）
_ADD_DEFAULT: dict[str, tuple[float, float, float, float]] = {
    "banner": (0.0, 0.0, 1.0, 0.24),
    "config": (0.0, 0.26, 0.34, 0.7),
    "log": (0.36, 0.26, 0.4, 0.7),
    "news": (0.78, 0.26, 0.22, 0.7),
    "quick": (0.32, 0.3, 0.34, 0.3),
    "notes": (0.32, 0.34, 0.28, 0.26),
    "playtime": (0.32, 0.36, 0.32, 0.22),
    "tasks": (0.32, 0.36, 0.32, 0.22),
}


class CardSpec:
    """注册表条目：一种卡片类型的元数据与构造方式。"""

    def __init__(self, key: str, title: Callable[[], str], icon, desc: Callable[[], str],
                 make_body: Callable[["DashboardCard", LayoutItem], Optional[QWidget]],
                 single: bool = False, chrome: bool = True,
                 on_settings: Callable | None = None,
                 on_removed: Callable[[QWidget, LayoutItem], None] | None = None):
        self.key = key
        self.title = title
        self.icon = icon
        self.desc = desc
        self.make_body = make_body
        self.single = single          # 单例：全画布只能有一张（绑定页面逻辑）
        self.chrome = chrome          # 是否常驻卡片标题栏（False 仅编辑时显示）
        self.on_settings = on_settings
        self.on_removed = on_removed


class _Shield(QWidget):
    """编辑模式下盖住卡片内容的透明盾牌：拦截鼠标转成整卡拖动。"""

    def __init__(self, card: "DashboardCard"):
        super().__init__(card)
        self.card = card

    def mousePressEvent(self, e):
        self.card._drag_begin(e.globalPosition().toPoint())
        e.accept()

    def mouseMoveEvent(self, e):
        self.card._drag_move(e.globalPosition().toPoint())
        e.accept()

    def mouseReleaseEvent(self, e):
        self.card._drag_end()
        e.accept()


class _Grip(QLabel):
    """缩放手柄。direction ∈ {n,s,e,w,ne,nw,se,sw}。"""

    def __init__(self, card: "DashboardCard", direction: str):
        super().__init__(card)
        self.card = card
        self.direction = direction
        self._start = None  # (global_pos, x, y, w, h)
        cursors = {
            "n": Qt.SizeVerCursor, "s": Qt.SizeVerCursor,
            "e": Qt.SizeHorCursor, "w": Qt.SizeHorCursor,
            "ne": Qt.SizeBDiagCursor, "sw": Qt.SizeBDiagCursor,
            "nw": Qt.SizeFDiagCursor, "se": Qt.SizeFDiagCursor,
        }
        self.setCursor(cursors[direction])
        self.setStyleSheet(
            "background: rgba(46,155,107,220); border: 1px solid white; border-radius: 3px;"
        )

    def mousePressEvent(self, e):
        self._start = (e.globalPosition().toPoint(),
                       self.card.x(), self.card.y(), self.card.width(), self.card.height())
        self.card._take_top()
        e.accept()

    def mouseMoveEvent(self, e):
        if self._start is None:
            return
        g0, x0, y0, w0, h0 = self._start
        dx = int(e.globalPosition().toPoint().x() - g0.x())
        dy = int(e.globalPosition().toPoint().y() - g0.y())
        self.card._resize_by(self.direction, x0, y0, w0, h0, dx, dy)
        e.accept()

    def mouseReleaseEvent(self, e):
        if self._start is not None:
            self._start = None
            self.card._commit_geometry()
        e.accept()


class DashboardCard(QFrame):
    """画布卡片：标题栏 + 内容宿主 + 编辑态交互（拖动/缩放/删除）。"""

    def __init__(self, canvas: "DashboardCanvas", item: LayoutItem, spec: CardSpec):
        super().__init__(canvas)
        self.canvas = canvas
        self.item = item
        self.spec = spec
        self.body: QWidget | None = None
        self._drag_anchor: QPoint | None = None

        self.setAttribute(Qt.WA_StyledBackground, True)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ---- 标题栏 ----
        self.header = QFrame(self)
        self.header.setObjectName("dashHeader")
        self.header.setAttribute(Qt.WA_StyledBackground, True)
        hl = QHBoxLayout(self.header)
        hl.setContentsMargins(12, 4, 6, 4)
        hl.setSpacing(6)
        self.icon_label = QLabel(self.header)
        self.icon_label.setFixedSize(16, 16)
        self.title_label = StrongBodyLabel(spec.title(), self.header)
        hl.addWidget(self.icon_label, 0, Qt.AlignVCenter)
        hl.addWidget(self.title_label, 1)
        self.settings_btn = ToolButton(FIF.SETTING, self.header)
        self.settings_btn.setFixedSize(26, 26)
        self.settings_btn.setToolTip(tr("卡片设置"))
        self.settings_btn.clicked.connect(self._on_settings)
        self.remove_btn = ToolButton(FIF.DELETE, self.header)
        self.remove_btn.setFixedSize(26, 26)
        self.remove_btn.setToolTip(tr("移除此卡片"))
        self.remove_btn.clicked.connect(lambda: self.canvas.remove_card(self))
        hl.addWidget(self.settings_btn, 0, Qt.AlignVCenter)
        hl.addWidget(self.remove_btn, 0, Qt.AlignVCenter)
        root.addWidget(self.header)

        # ---- 内容宿主：套一层滚动区。表单类正文（启动配置）的布局最小
        # 宽高很大（500px+），不套的话卡片会被内容的最小尺寸钉死，
        # 缩不下去也回不到默认比例（重置布局"大小没变"的根因）。
        # 套上后卡片可以任意缩到类型兜底值，装不下就滚动。
        from PySide6.QtWidgets import QScrollArea
        self.scroll = QScrollArea(self)
        self.scroll.setObjectName("dashScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setProperty("pymclTransparentScroll", True)
        # QScrollArea 会自画默认浅色底，且父样式表里的背景规则对它无效
        # （实测父表里 #dashScroll 规则不生效）——滚动区规则必须写在
        # 滚动区自己的样式表上，restyle 会同步刷新。
        self.scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }")
        self.body_host = QWidget()
        # viewport 的调色板底色关不掉（QSS/autofill 都管不到它的边缘条），
        # 索性让正文宿主整片铺卡片底色，把 viewport 完全盖住。restyle 同步刷。
        self.body_host.setObjectName("dashBody")
        self.body_host.setAttribute(Qt.WA_StyledBackground, True)
        bl = QVBoxLayout(self.body_host)
        bl.setContentsMargins(10, 6, 10, 10)
        bl.setSpacing(0)
        self.scroll.setWidget(self.body_host)
        root.addWidget(self.scroll, 1)

        body = spec.make_body(self, item)
        if body is not None:
            self.body = body
            self.body_host.layout().addWidget(body)

        # ---- 编辑态配件 ----
        self.shield = _Shield(self)
        self.shield.setVisible(False)
        self.grips = [_Grip(self, d) for d in ("n", "s", "e", "w", "ne", "nw", "se", "sw")]
        for g in self.grips:
            g.setVisible(False)

        # 类型兜底最小值 + 标题栏高度（banner 的正文最小值 155 < 兜底
        # 186，进度条/状态行不会被压扁——黑线回归见 banner.no_squeeze）
        mw, mh = min_size_for(item.type)
        self.setMinimumSize(mw, mh + 40)
        self.restyle()

    # ---- 公共 ----
    def set_title(self, text: str):
        self.title_label.setText(text)

    def refresh(self):
        if self.body is not None and hasattr(self.body, "refresh"):
            try:
                self.body.refresh()
            except Exception:
                pass

    def restyle(self):
        from .pcl_chrome import Theme
        editing = self.canvas.editing
        border = (f"1px dashed {Theme.green}" if editing
                  else f"1px solid {Theme.line}")
        self.setStyleSheet(
            f"DashboardCard {{ background: {Theme.card};"
            f" border: {border}; border-radius: 8px; }}"
            f"#dashHeader {{ background: transparent; border: none;"
            f" border-bottom: 1px solid {Theme.row_line}; }}"
        )
        # 注意：滚动区/滚动条的规则写在滚动区自己身上（见 __init__ 注释）；
        # 普通字符串段（无 f 前缀）必须写单括号，写成 }} 会解析失败。
        self.scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
            "QScrollBar:vertical { background: transparent; width: 6px; }"
            f"QScrollBar::handle:vertical {{ background: {Theme.line};"
            " border-radius: 3px; min-height: 24px; }"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
            "QScrollBar:horizontal { background: transparent; height: 6px; }"
            f"QScrollBar::handle:horizontal {{ background: {Theme.line};"
            " border-radius: 3px; min-width: 24px; }"
            "QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }"
        )
        self.body_host.setStyleSheet(f"#dashBody {{ background: {Theme.card}; }}")
        try:
            icon = self.spec.icon
            color = Theme.green
            self.icon_label.setPixmap(icon.icon(color=QColor(color)).pixmap(16, 16))
        except Exception:
            pass

    def set_edit_mode(self, on: bool):
        self.shield.setVisible(on)
        for g in self.grips:
            g.setVisible(on)
        show_header = on or self.spec.chrome
        self.header.setVisible(show_header)
        self.remove_btn.setVisible(on)
        self.settings_btn.setVisible(on and self.spec.on_settings is not None)
        self.shield.raise_()
        for g in self.grips:
            g.raise_()
        self._layout_extras()
        self.restyle()

    # ---- 内部：交互 ----
    def _on_settings(self):
        if self.spec.on_settings is not None:
            self.spec.on_settings(self.canvas, self, self.item)

    def _take_top(self):
        self.raise_()
        self.item.z = self.canvas.doc.next_z()
        for g in self.grips:
            g.raise_()

    def _snap(self, v: int) -> int:
        g = self.canvas.doc.grid
        if g and g > 0:
            return int(round(v / g) * g)
        return v

    def _drag_begin(self, global_pos: QPoint):
        self._drag_anchor = (global_pos - self.pos())
        self._take_top()

    def _drag_move(self, global_pos: QPoint):
        if self._drag_anchor is None:
            return
        canvas = self.canvas
        w, h = self.width(), self.height()
        x = self._snap(global_pos.x() - self._drag_anchor.x())
        y = self._snap(global_pos.y() - self._drag_anchor.y())
        x = max(0, min(x, canvas.width() - w))
        y = max(0, min(y, canvas.height() - h))
        self.move(x, y)

    def _drag_end(self):
        if self._drag_anchor is None:
            return
        self._drag_anchor = None
        self._commit_geometry()

    def _resize_by(self, direction, x0, y0, w0, h0, dx, dy):
        canvas = self.canvas
        # 用控件自身强制的最小值（含标题栏），低于它 Qt 也会顶回来
        mw, mh = self.minimumWidth(), self.minimumHeight()
        cw, ch = canvas.width(), canvas.height()
        x, y, w, h = x0, y0, w0, h0
        if "e" in direction:
            w = self._snap(w0 + dx)
        if "s" in direction:
            h = self._snap(h0 + dy)
        if "w" in direction:
            w = self._snap(w0 - dx)
        if "n" in direction:
            h = self._snap(h0 - dy)
        w = max(mw, min(w, cw))
        h = max(mh, min(h, ch))
        if "w" in direction:
            x = x0 + (w0 - w)
        if "n" in direction:
            y = y0 + (h0 - h)
        # 画布边界：拖哪条边就钳哪条边。向下/右扩到画布底时必须缩尺寸，
        # 不能让锚定的顶/左边被悄悄推走——那种隐性滑动会插进上面的卡片，
        # 联动逻辑管不到（随机拖拽模糊测试里 log.se 叠上 banner 的根因）。
        if "e" in direction and x + w > cw:
            w = max(mw, cw - x)
        if "w" in direction and x < 0:
            x = 0
            w = max(mw, x0 + w0)
        if "s" in direction and y + h > ch:
            h = max(mh, ch - y)
        if "n" in direction and y < 0:
            y = 0
            h = max(mh, y0 + h0)
        x = max(0, min(x, cw - w))
        y = max(0, min(y, ch - h))
        self.canvas._resize_linked(self, direction,
                                   QRect(x0, y0, w0, h0), QRect(x, y, w, h))

    def _commit_geometry(self):
        self.item.set_geometry_px(self.x(), self.y(), self.width(), self.height(),
                                  (self.canvas.width(), self.canvas.height()))
        self.canvas._flush_link_pending()
        self.canvas._touch()

    def mousePressEvent(self, e):
        if self.canvas.editing:
            self._drag_begin(e.globalPosition().toPoint())
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self.canvas.editing and self._drag_anchor is not None:
            self._drag_move(e.globalPosition().toPoint())
            e.accept()
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self.canvas.editing and self._drag_anchor is not None:
            self._drag_end()
            e.accept()
            return
        super().mouseReleaseEvent(e)

    def _layout_extras(self):
        """按当前几何摆放盾牌与 8 个手柄。"""
        hd = self.header.height() if self.header.isVisible() else 0
        if self.shield.isVisible():
            self.shield.setGeometry(0, hd, self.width(), max(0, self.height() - hd))
        gs = 10
        w, h = self.width(), self.height()
        for g in self.grips:
            d = g.direction
            gx = 4 if d in ("w", "nw", "sw") else (w - gs - 4 if d in ("e", "ne", "se") else (w - gs) // 2)
            gy = 4 if d in ("n", "ne", "nw") else (h - gs - 4 if d in ("s", "se", "sw") else (h - gs) // 2)
            if not self.header.isVisible() and d in ("n", "ne", "nw"):
                gy = 4
            g.setGeometry(gx, gy, gs, gs)
        if self.shield.isVisible():
            self.shield.raise_()
            for g in self.grips:
                g.raise_()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._layout_extras()


class _CardPalette(MessageBoxBase):
    """添加卡片调色板：点击条目即添加并关闭。"""

    def __init__(self, canvas: "DashboardCanvas", parent=None):
        super().__init__(parent)
        self.canvas = canvas
        self.viewLayout.addWidget(SubtitleLabel(tr("添加卡片"), self))
        hint = BodyLabel(tr("点击要添加到布局的卡片："), self)
        hint.setWordWrap(True)
        self.viewLayout.addWidget(hint)
        used_single = {c.item.type for c in canvas.cards if c.spec.single}
        for key in canvas.registry_order():
            spec = canvas.registry[key]
            if spec.single and key in used_single:
                continue
            row = QPushButton(spec.desc(), self)
            row.setStyleSheet("QPushButton { text-align: left; padding: 10px 12px; }")
            row.setIcon(spec.icon.icon(color=QColor("#2E9B6B")))
            row.clicked.connect(lambda _=False, k=key: self._pick(k))
            self.viewLayout.addWidget(row)
        self.yesButton.hide()
        self.cancelButton.setText(tr("关闭"))
        self.widget.setMinimumWidth(420)

    def _pick(self, key: str):
        self.canvas.add_card(key)
        self.accept()


class DashboardCanvas(QWidget):
    """绝对定位画布。宿主页把注册表和布局文档交进来即可。"""

    layout_changed = Signal()  # 布局有变化（防抖后），宿主页负责落盘

    def __init__(self, registry: dict[str, CardSpec], parent=None):
        super().__init__(parent)
        self.registry = registry
        self.doc = default_doc()
        self.cards: list[DashboardCard] = []
        self.editing = False
        self._grid_op = 0.0  # 编辑网格点阵的透明度（进出编辑模式时补间）
        self._link_pending: set[DashboardCard] = set()

        self._persist_timer = QTimer(self)
        self._persist_timer.setSingleShot(True)
        self._persist_timer.setInterval(300)
        self._persist_timer.timeout.connect(self.layout_changed)

        # 编辑模式工具条
        self.toolbar = QFrame(self)
        self.toolbar.setObjectName("dashToolbar")
        self.toolbar.setAttribute(Qt.WA_StyledBackground, True)
        tl = QHBoxLayout(self.toolbar)
        tl.setContentsMargins(10, 6, 10, 6)
        tl.setSpacing(8)
        self.add_btn = PushButton(FIF.ADD, tr("添加卡片"), self.toolbar)
        self.add_btn.clicked.connect(self._open_palette)
        grid_label = BodyLabel(tr("吸附"), self.toolbar)
        self.grid_box = QComboBox(self.toolbar)
        self.grid_box.addItem(tr("自由"), 0)
        for v in GRID_CHOICES[1:]:
            self.grid_box.addItem(f"{v}px", v)
        self.grid_box.currentIndexChanged.connect(self._on_grid_changed)
        fit_btn = PushButton(FIF.ZOOM if hasattr(FIF, "ZOOM") else FIF.SYNC, tr("适应窗口"), self.toolbar)
        fit_btn.clicked.connect(self.fit_to_window)
        reset_btn = PushButton(FIF.SYNC, tr("重置布局"), self.toolbar)
        reset_btn.clicked.connect(self.reset_layout)
        done_btn = PrimaryPushButton(FIF.ACCEPT, tr("完成"), self.toolbar)
        done_btn.clicked.connect(lambda: self.set_edit_mode(False))
        tl.addWidget(self.add_btn)
        tl.addWidget(grid_label)
        tl.addWidget(self.grid_box)
        tl.addWidget(fit_btn)
        tl.addWidget(reset_btn)
        tl.addStretch(1)
        tl.addWidget(done_btn)
        self.toolbar.setVisible(False)
        self._style_toolbar()

    # ------------------------------------------------------------------
    # 构建 / 重建
    # ------------------------------------------------------------------
    def registry_order(self) -> list[str]:
        return list(self.registry.keys())

    def build_from_doc(self, doc: LayoutDoc):
        self.doc = doc
        self._rebuild()

    def _rebuild(self):
        for card in self.cards:
            body = card.body
            if body is not None:
                body.setParent(None)
                if card.spec.on_removed is not None:
                    try:
                        card.spec.on_removed(body, card.item)
                    except Exception:
                        pass
            card.deleteLater()
        self.cards = []
        for item in self.doc.visible_items():
            spec = self.registry.get(item.type)
            if spec is None:
                continue
            card = DashboardCard(self, item, spec)
            self.cards.append(card)
            card.setVisible(True)
            card.set_edit_mode(self.editing)
            card.show()
        self._apply_geometry()
        self._apply_grid_box()
        self._layout_chrome()

    def _apply_geometry(self):
        """按文档比例重摆全部卡片（构建后 / 画布尺寸变化时调用）。"""
        cw, ch = max(1, self.width()), max(1, self.height())
        for card in self.cards:
            mw, mh = card.minimumWidth(), card.minimumHeight()
            x, y, w, h = card.item.geometry_px((cw, ch))
            w, h = max(w, mw), max(h, mh)
            # 画布比卡片最小值还小时，卡片贴边不越界
            x = max(0, min(x, max(0, cw - w)))
            y = max(0, min(y, max(0, ch - h)))
            card.setGeometry(x, y, w, h)
            card._layout_extras()

    def _apply_grid_box(self):
        v = self.doc.grid
        for i in range(self.grid_box.count()):
            if self.grid_box.itemData(i) == v:
                self.grid_box.blockSignals(True)
                self.grid_box.setCurrentIndex(i)
                self.grid_box.blockSignals(False)
                break

    def _on_grid_changed(self, *_):
        self.doc.grid = int(self.grid_box.currentData() or 0)
        self.layout_changed.emit()

    # ------------------------------------------------------------------
    # 缩放联动
    # ------------------------------------------------------------------
    # 判定“贴着被拖边”的缝隙范围（px）：0=贴边，负=略有重叠；
    # 上限覆盖默认布局 12px 的栏间缝。推挤时保持原缝不变。
    _LINK_GAP_MAX = 28
    _LINK_OVERLAP_MAX = 8

    def _link_followers(self, active: DashboardCard, direction: str, old: QRect) -> dict:
        """手势开始时锁定跟随者：卡片、原缝、固定不动的锚缘、起始矩形。

        每帧重算会因邻居已跟随移动、缝隙变大而“掉链子”，所以按
        (卡片, 方向, 起点矩形) 缓存，整个手势内保持不变。
        """
        key = (id(active), direction, old.getRect())
        if getattr(self, "_link_key", None) == key:
            return self._link_fols
        fols: dict[str, list] = {"e": [], "w": [], "s": [], "n": []}
        for c in self.cards:
            if c is active:
                continue
            r = QRect(c.x(), c.y(), c.width(), c.height())
            ov_v = old.y() < r.y() + r.height() and old.y() + old.height() > r.y()
            ov_h = old.x() < r.x() + r.width() and old.x() + old.width() > r.x()
            gap = c.x() - (old.x() + old.width())   # 右侧邻居（贴我的右边）
            if -self._LINK_OVERLAP_MAX <= gap <= self._LINK_GAP_MAX and ov_v:
                fols["e"].append((c, gap, c.x() + c.width(), r))
            gap = old.x() - (c.x() + c.width())     # 左侧邻居（贴我的左边）
            if -self._LINK_OVERLAP_MAX <= gap <= self._LINK_GAP_MAX and ov_v:
                fols["w"].append((c, gap, c.x(), r))
            gap = c.y() - (old.y() + old.height())  # 下方邻居
            if -self._LINK_OVERLAP_MAX <= gap <= self._LINK_GAP_MAX and ov_h:
                fols["s"].append((c, gap, c.y() + c.height(), r))
            gap = old.y() - (c.y() + c.height())    # 上方邻居
            if -self._LINK_OVERLAP_MAX <= gap <= self._LINK_GAP_MAX and ov_h:
                fols["n"].append((c, gap, c.y(), r))
        self._link_key = key
        self._link_fols = fols
        return fols

    def _resize_linked(self, active: DashboardCard, direction: str,
                       old: QRect, want: QRect):
        """联动缩放：与被拖动边贴合（含留有小缝）的相邻卡片跟随让位/补位。

        例：左卡 1/3 + 右卡 2/3 隔着一条竖边，把左卡拉宽到 2/3 时
        右卡自动缩成 1/3，两卡的相对缝隙保持不变，不重叠也不留大缝；
        反向拖则邻居自动补位变宽。

        障碍物规则：跟随者被推挤时撞到任何非跟随卡片就停（例如把右卡
        左推会压到别排卡片时，被拖的边停在障碍物前），保证缩放过程
        永不产生新的重叠。所有参与者都不小于各自最小尺寸。
        """
        x, y, w, h = want.x(), want.y(), want.width(), want.height()
        cw, ch = max(1, self.width()), max(1, self.height())
        F = self._link_followers(active, direction, old)
        touched: list[DashboardCard] = []

        def rect(c: DashboardCard) -> QRect:
            return QRect(c.x(), c.y(), c.width(), c.height())

        def ov_v(a: QRect, b: QRect) -> bool:  # 纵向有重叠段（左右关系）
            return a.y() < b.y() + b.height() and a.y() + a.height() > b.y()

        def ov_h(a: QRect, b: QRect) -> bool:  # 横向有重叠段（上下关系）
            return a.x() < b.x() + b.width() and a.x() + a.width() > b.x()

        def blockers(side_fols):
            fset = {id(c) for c, *_ in side_fols}
            return [c for c in self.cards
                    if c is not active and id(c) not in fset]

        if "e" in direction:
            edge = x + w
            fols = F["e"]
            lo = x + active.minimumWidth()
            hi = cw
            for c, gap, anchor_r, r0 in fols:
                hi = min(hi, anchor_r - gap - c.minimumWidth())
                for b in blockers(fols):  # 跟随者左移撞到它左侧的卡就停
                    br = rect(b)
                    if b.x() + b.width() <= r0.x() + self._LINK_GAP_MAX and ov_v(r0, br):
                        lo = max(lo, b.x() + b.width() - gap)
            for b in blockers(fols):  # 被拖卡拉宽压不到右侧非跟随卡
                br = rect(b)
                if (b.x() >= old.x() + old.width() - self._LINK_OVERLAP_MAX
                        and (ov_v(old, br) or ov_v(want, br))):
                    hi = min(hi, b.x())
            edge = max(0, min(hi, max(lo, edge)))
            for c, gap, anchor_r, _ in fols:
                c.setGeometry(edge + gap, c.y(), max(1, anchor_r - edge - gap), c.height())
                touched.append(c)
            w = edge - x
        if "w" in direction:
            edge = x
            fols = F["w"]
            lo = 0
            hi = old.x() + old.width() - active.minimumWidth()
            for c, gap, anchor_l, r0 in fols:
                lo = max(lo, anchor_l + c.minimumWidth() + gap)
                for b in blockers(fols):  # 跟随者右移撞到它右侧的卡就停
                    br = rect(b)
                    if b.x() >= r0.x() + r0.width() - self._LINK_GAP_MAX and ov_v(r0, br):
                        hi = min(hi, b.x() + gap)
            for b in blockers(fols):  # 被拖卡拉宽压不到左侧非跟随卡
                br = rect(b)
                if (b.x() + b.width() <= old.x() + self._LINK_OVERLAP_MAX
                        and (ov_v(old, br) or ov_v(want, br))):
                    lo = max(lo, b.x() + b.width())
            edge = min(cw, max(lo, min(hi, edge)))
            for c, gap, anchor_l, _ in fols:
                c.setGeometry(c.x(), c.y(), max(1, edge - gap - anchor_l), c.height())
                touched.append(c)
            w = old.x() + old.width() - edge
            x = edge
        if "s" in direction:
            edge = y + h
            fols = F["s"]
            lo = y + active.minimumHeight()
            hi = ch
            for c, gap, anchor_b, r0 in fols:
                hi = min(hi, anchor_b - gap - c.minimumHeight())
                for b in blockers(fols):  # 跟随者上移撞到它上方的卡就停
                    br = rect(b)
                    if b.y() + b.height() <= r0.y() + self._LINK_GAP_MAX and ov_h(r0, br):
                        lo = max(lo, b.y() + b.height() - gap)
            for b in blockers(fols):  # 被拖卡拉长压不到下方非跟随卡
                br = rect(b)
                if (b.y() >= old.y() + old.height() - self._LINK_OVERLAP_MAX
                        and (ov_h(old, br) or ov_h(want, br))):
                    hi = min(hi, b.y())
            edge = max(0, min(hi, max(lo, edge)))
            for c, gap, anchor_b, _ in fols:
                c.setGeometry(c.x(), edge + gap, c.width(), max(1, anchor_b - edge - gap))
                touched.append(c)
            h = edge - y
        if "n" in direction:
            edge = y
            fols = F["n"]
            lo = 0
            hi = old.y() + old.height() - active.minimumHeight()
            for c, gap, anchor_t, r0 in fols:
                lo = max(lo, anchor_t + c.minimumHeight() + gap)
                for b in blockers(fols):  # 跟随者下移撞到它下方的卡就停
                    br = rect(b)
                    if b.y() >= r0.y() + r0.height() - self._LINK_GAP_MAX and ov_h(r0, br):
                        hi = min(hi, b.y() + gap)
            for b in blockers(fols):  # 被拖卡拉长压不到上方非跟随卡
                br = rect(b)
                if (b.y() + b.height() <= old.y() + self._LINK_OVERLAP_MAX
                        and (ov_h(old, br) or ov_h(want, br))):
                    lo = max(lo, b.y() + b.height())
            edge = min(ch, max(lo, min(hi, edge)))
            for c, gap, anchor_t, _ in fols:
                c.setGeometry(c.x(), c.y(), c.width(), max(1, edge - gap - anchor_t))
                touched.append(c)
            h = old.y() + old.height() - edge
            y = edge

        x = max(0, min(x, cw - w))
        y = max(0, min(y, ch - h))
        active.setGeometry(x, y, w, h)
        if touched:
            self._link_pending.update(touched)

    def _set_grid_op(self, v):
        self._grid_op = float(v)
        self.update()

    def _flush_link_pending(self):
        """松手时把联动过的邻居几何一并写回布局文档（否则下次重摆会弹回）。"""
        if not self._link_pending:
            return
        size = (self.width(), self.height())
        for c in self._link_pending:
            c.item.set_geometry_px(c.x(), c.y(), c.width(), c.height(), size)
        self._link_pending = set()
        self._link_key = None  # 手势结束，下次重新锁定跟随者

    # ------------------------------------------------------------------
    # 编辑模式
    # ------------------------------------------------------------------
    def set_edit_mode(self, on: bool):
        if self.editing == on:
            return
        self.editing = on
        self.toolbar.setVisible(True)
        for card in self.cards:
            card.set_edit_mode(on)
        self._layout_chrome()
        if on:
            motion.slide_in(self.toolbar, dy=-10, ms=200)
        else:
            motion.fade(self.toolbar, 1.0, 0.0, ms=140, on_done=self.toolbar.hide)
        motion.tween(self._set_grid_op, 0.0 if on else 1.0,
                     1.0 if on else 0.0, ms=220)
        self.update()

    def _open_palette(self):
        dlg = _CardPalette(self, self.window())
        dlg.exec()

    # ------------------------------------------------------------------
    # 增删 / 排布
    # ------------------------------------------------------------------
    def add_card(self, card_type: str) -> DashboardCard | None:
        spec = self.registry.get(card_type)
        if spec is None:
            return None
        if spec.single and any(c.item.type == card_type for c in self.cards):
            return None
        fx, fy, fw, fh = _ADD_DEFAULT.get(card_type, (0.3, 0.3, 0.3, 0.26))
        x, y, w, h = self._find_free_spot(card_type, fx, fy, fw, fh)
        item = LayoutItem(card_type, 0.0, 0.0, 0.1, 0.1,
                          z=self.doc.next_z())
        item.set_geometry_px(x, y, w, h, (self.width(), self.height()))
        self.doc.items.append(item)
        card = DashboardCard(self, item, spec)
        self.cards.append(card)
        card.setGeometry(x, y, w, h)
        card.set_edit_mode(self.editing)
        card.show()
        card.raise_()
        motion.fade(card, 0.0, 1.0, ms=180)
        self._touch(structural=True)
        return card

    def remove_card(self, card: DashboardCard):
        if card not in self.cards:
            return
        self.cards.remove(card)
        body = card.body
        if body is not None:
            body.setParent(None)
            if card.spec.on_removed is not None:
                try:
                    card.spec.on_removed(body, card.item)
                except Exception:
                    pass
        self.doc.items = [it for it in self.doc.items if it is not card.item]
        motion.fade(card, 1.0, 0.0, ms=130, on_done=card.deleteLater)
        self._touch(structural=True)

    def _find_free_spot(self, card_type: str, fx, fy, fw, fh) -> tuple[int, int, int, int]:
        cw, ch = max(1, self.width()), max(1, self.height())
        w = max(min_size_for(card_type)[0], int(fw * cw))
        h = max(min_size_for(card_type)[1], int(fh * ch))
        w = min(w, cw - 16)
        h = min(h, ch - 16)
        rects = [QRect(c.x(), c.y(), c.width(), c.height()) for c in self.cards]
        step = 24
        for yy in range(8, max(9, ch - h - 8), step):
            for xx in range(8, max(9, cw - w - 8), step):
                cand = QRect(xx, yy, w, h)
                if not any(cand.intersects(r.adjusted(-8, -8, 8, 8)) for r in rects):
                    return xx, yy, w, h
        return 8, 8, w, h

    def fit_to_window(self):
        """把可见卡片的联合包围盒等比放大到铺满画布（留边距）。"""
        cw, ch = max(1, self.width()), max(1, self.height())
        vis = self.doc.visible_items()
        if not vis:
            return
        bx0 = min(it.x for it in vis)
        by0 = min(it.y for it in vis)
        bx1 = max(it.x + it.w for it in vis)
        by1 = max(it.y + it.h for it in vis)
        bw = max(1e-4, bx1 - bx0)
        bh = max(1e-4, by1 - by0)
        m = 12 / cw, 12 / ch
        sx = (1.0 - 2 * m[0]) / bw
        sy = (1.0 - 2 * m[1]) / bh
        for it in vis:
            mw, mh = min_size_for(it.type)
            it.x = m[0] + (it.x - bx0) * sx
            it.y = m[1] + (it.y - by0) * sy
            it.w = max(mw / cw, it.w * sx)
            it.h = max(mh / ch, it.h * sy)
        self._rebuild()
        self._touch(structural=True)

    def reset_layout(self):
        self.build_from_doc(default_doc())
        self._touch(structural=True)

    def current_doc(self) -> LayoutDoc:
        self.doc.normalize()
        return self.doc

    def refresh_cards(self):
        for card in self.cards:
            card.refresh()

    def _touch(self, structural: bool = False):
        """布局变化：结构改动立即广播，几何改动防抖合并；最终都会再发一次。"""
        if structural:
            self.layout_changed.emit()
        self._persist_timer.start()

    # ------------------------------------------------------------------
    # 视觉
    # ------------------------------------------------------------------
    def _style_toolbar(self):
        from .pcl_chrome import Theme
        self.toolbar.setStyleSheet(
            f"#dashToolbar {{ background: {Theme.card}; border: 1px solid {Theme.line};"
            " border-radius: 8px; }"
        )

    def restyle(self):
        self._style_toolbar()
        for card in self.cards:
            card.restyle()

    def _layout_chrome(self):
        """摆放编辑工具条，并保证层级在最上。"""
        if self.editing:
            m = 8
            self.toolbar.adjustSize()
            self.toolbar.move(max(m, self.width() - self.toolbar.width() - m), m)
            self.toolbar.raise_()
            for card in self.cards:
                card._layout_extras()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        # 比例布局：画布尺寸变化时按文档比例重摆，窗口缩放布局自适应
        self._apply_geometry()
        self._layout_chrome()

    def _grid_pixmap(self):
        """编辑模式参考点阵的缓存位图：直接逐点画一次上万 drawPoint，
        拖卡片每帧重绘会卡；只在尺寸/网格变化时重建。"""
        from PySide6.QtGui import QPixmap
        key = (self.width(), self.height(), self.doc.grid)
        if getattr(self, "_grid_key", None) != key:
            pix = QPixmap(max(1, self.width()), max(1, self.height()))
            pix.fill(Qt.transparent)
            p = QPainter(pix)
            pen = QPen(QColor(140, 140, 140, 70))
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            g = self.doc.grid
            for yy in range(g, self.height(), g):
                for xx in range(g, self.width(), g):
                    p.drawPoint(xx, yy)
            p.end()
            self._grid_pix = pix
            self._grid_key = key
        return self._grid_pix

    def paintEvent(self, e):
        if self._grid_op > 0.01 and self.doc.grid > 0:
            p = QPainter(self)
            p.setOpacity(self._grid_op)
            p.drawPixmap(0, 0, self._grid_pixmap())
            p.end()
        super().paintEvent(e)
