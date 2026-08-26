# -*- coding: utf-8 -*-
"""下载分区：顶部分类横条 + 内容页。"""

from PySide6.QtCore import (
    QAbstractAnimation, QEasingCurve, QParallelAnimationGroup, QPoint,
    QPropertyAnimation, QRect, Qt, QTimer, Signal,
)
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtWidgets import (
    QButtonGroup, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QStackedWidget, QVBoxLayout, QWidget,
)

from ..motion import fade as _mcl_fade
from ..pcl_chrome import Theme


class SlideHStack(QStackedWidget):
    """左右滑页：先盖住旧帧，切到真页后再抓新帧，动画层盖住切换。"""

    DURATION = 260

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background: transparent;")
        self._from = QLabel(self)
        self._to = QLabel(self)
        for lab in (self._from, self._to):
            lab.setScaledContents(True)
            lab.hide()
        self._ani = None
        self._pending = None
        self._grab_gen = 0
        self._last_slide_ms = 0

    def slide_to(self, widget):
        if widget is None:
            return
        if widget is self.currentWidget() or self.indexOf(widget) < 0:
            return
        from ..motion_prefs import ui_motion_ok
        import time
        now = int(time.monotonic() * 1000)
        rapid = (now - self._last_slide_ms) < 180
        self._last_slide_ms = now
        if (not ui_motion_ok()) or rapid:
            if self._ani and self._ani.state() == QAbstractAnimation.Running:
                self._ani.stop()
            self._finish_now(widget)
            return
        if self._ani and self._ani.state() == QAbstractAnimation.Running:
            self._ani.stop()
            self._finish_now(self._pending or widget)
        elif self._pending is not None and self._ani is None:
            # 上一帧 grab 尚未回来，直接落到目标页
            self._grab_gen += 1
            self._finish_now(widget)
            return
        old = self.currentWidget()
        if old is None or self.width() < 8:
            super().setCurrentWidget(widget)
            return
        direction = 1 if self.indexOf(widget) > self.indexOf(old) else -1
        w, h = self.width(), self.height()
        pix_old = old.grab()
        if pix_old.isNull():
            super().setCurrentWidget(widget)
            return
        self._from.setPixmap(pix_old)
        self._from.setGeometry(0, 0, w, h)
        self._from.show()
        self._from.raise_()
        super().setCurrentWidget(widget)
        widget.resize(w, h)
        widget.ensurePolished()
        lay = widget.layout()
        if lay is not None:
            lay.activate()
        # setCurrentWidget 后立刻 grab 常抓到未布局完的空白帧；推迟到下一事件循环再抓
        self._pending = widget
        self._grab_gen += 1
        gen = self._grab_gen
        QTimer.singleShot(0, lambda: self._grab_new_and_animate(widget, direction, w, h, gen))

    def _grab_new_and_animate(self, widget, direction, w, h, gen):
        if gen != self._grab_gen or self._pending is not widget:
            return
        if self.currentWidget() is not widget or self.indexOf(widget) < 0:
            self._clear_slides()
            return
        pix_new = widget.grab()
        if pix_new.isNull():
            self._clear_slides()
            return
        self._to.setPixmap(pix_new)
        self._to.setGeometry(direction * w, 0, w, h)
        self._to.show()
        self._to.raise_()

        group = QParallelAnimationGroup(self)
        a1 = QPropertyAnimation(self._from, b"pos", self)
        a1.setEndValue(QPoint(-direction * w, 0))
        a2 = QPropertyAnimation(self._to, b"pos", self)
        a2.setEndValue(QPoint(0, 0))
        for ani in (a1, a2):
            ani.setDuration(self.DURATION)
            ani.setEasingCurve(QEasingCurve.OutCubic)
            ani.setStartValue(ani.targetObject().pos())
            group.addAnimation(ani)
        group.finished.connect(self._clear_slides)
        self._ani = group
        group.start()

    def _finish_now(self, widget):
        self._grab_gen += 1
        self._clear_slides()
        if widget is not None and self.indexOf(widget) >= 0:
            super().setCurrentWidget(widget)

    def _clear_slides(self):
        self._from.hide()
        self._to.hide()
        self._from.clear()
        self._to.clear()
        self._ani = None
        self._pending = None

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._from.isVisible():
            target = self._pending or self.currentWidget()
            if self._ani:
                self._ani.stop()
            self._finish_now(target)


NAV_MIME = "application/x-pymcl-nav"


class _DragButton(QPushButton):
    """分类按钮 + 拖拽源：拖到侧栏即"固定为一级导航项"。"""

    def __init__(self, title: str, nav_key: str = "", parent=None):
        super().__init__(title, parent)
        self._nav_key = nav_key
        if nav_key:
            self.setProperty("navkey", nav_key)
        self._press_pos = None

    def mousePressEvent(self, e):
        self._press_pos = e.position().toPoint() if self._nav_key else None
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        self._press_pos = None
        super().mouseReleaseEvent(e)

    def mouseMoveEvent(self, e):
        if self._press_pos is not None and (e.position().toPoint() - self._press_pos).manhattanLength() > 8:
            self._press_pos = None
            self._start_nav_drag()
            return
        super().mouseMoveEvent(e)

    def _start_nav_drag(self):
        from PySide6.QtCore import QMimeData, QPoint
        from PySide6.QtGui import QDrag
        drag = QDrag(self)
        mime = QMimeData()
        mime.setData(NAV_MIME, self._nav_key.encode("utf-8"))
        mime.setText(self.text())
        drag.setMimeData(mime)
        pix = self.grab()
        if not pix.isNull():
            drag.setPixmap(pix)
            drag.setHotSpot(QPoint(pix.width() // 2, pix.height() // 2))
        drag.exec(Qt.CopyAction)


class DownloadCatBar(QFrame):
    currentChanged = Signal(object)
    unpinRequested = Signal(str)   # 拖回分类条：取消固定

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("downloadCatBar")
        self.setFixedHeight(48)
        self._scroll = QScrollArea(self)
        self._scroll.setObjectName("catScroll")
        self._scroll.setWidgetResizable(False)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setFixedHeight(48)

        self._host = QWidget()
        self._layout = QHBoxLayout(self._host)
        self._layout.setContentsMargins(16, 0, 16, 4)
        self._layout.setSpacing(4)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons = {}
        self._lazy = {}  # index -> (btn, title)：bind 先建的懒按钮，页面构造后 wire
        self._layout.addStretch(1)
        self._scroll.setWidget(self._host)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._scroll)

        self.setAcceptDrops(True)
        self._indicator = QFrame(self._host)
        self._indicator.setObjectName("catIndicator")
        self._indicator.setFixedHeight(2)
        self._indicator.hide()
        self._ind_anim = QPropertyAnimation(self._indicator, b"geometry", self)
        self._ind_anim.setDuration(240)
        self._ind_anim.setEasingCurve(QEasingCurve.OutCubic)
        self.restyle()

    def restyle(self):
        self.setStyleSheet(
            f"#downloadCatBar {{ background: transparent; border-bottom: 1px solid {Theme.line}; }}"
        )
        self._scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
            "QScrollBar:horizontal { height: 6px; background: transparent; }"
            f"QScrollBar::handle:horizontal {{ background: {Theme.line}; border-radius: 3px; min-width: 24px; }}"
            "QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }"
        )
        self._indicator.setStyleSheet(
            f"#catIndicator {{ background: {Theme.green}; border: none; border-radius: 1px; }}"
        )
        for btn, _ in self._buttons.values():
            self._style_btn(btn)
        for btn, _ in self._lazy.values():
            self._style_btn(btn)

    def _style_btn(self, btn):
        btn.setStyleSheet(
            f"QPushButton {{ border: none; background: transparent; color: {Theme.muted};"
            " font-size: 14px; padding: 0 16px; }"
            f"QPushButton:hover {{ color: {Theme.text}; background: {Theme.hover}; }}"
            f"QPushButton:checked {{ color: {Theme.green}; font-weight: 700; }}"
        )

    def add_item(self, title: str, page):
        btn = self._make_btn(title)
        self._buttons[id(page)] = (btn, page)
        btn.clicked.connect(lambda _, p=page: self.currentChanged.emit(p))
        self._add_btn(btn)

    def add_lazy_item(self, title: str, owner, index: int, nav_key: str = ""):
        """bind 阶段先建按钮（页面还没构造）：点击时回调 owner._open_pending。"""
        btn = self._make_btn(title, nav_key)
        self._lazy[index] = (btn, title)
        btn.clicked.connect(lambda _, o=owner, i=index: o._open_pending(i))
        self._add_btn(btn)
        _mcl_fade(btn, 0.0, 1.0, ms=150)

    def wire_item(self, title: str, page) -> bool:
        """页面真正构造好后，把同名的懒建按钮接到页面上（不重复建按钮）。"""
        for index, (btn, t) in list(self._lazy.items()):
            if t == title:
                del self._lazy[index]
                self._buttons[id(page)] = (btn, page)
                try:
                    btn.clicked.disconnect()
                except TypeError:
                    pass
                btn.clicked.connect(lambda _, p=page: self.currentChanged.emit(p))
                return True
        return False

    def _make_btn(self, title: str, nav_key: str = "") -> QPushButton:
        btn = _DragButton(title, nav_key)
        btn.setCheckable(True)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setFixedHeight(40)
        # 选中态加粗（font-weight:700）：拉丁字母加粗后明显变宽，而
        # sizeHint 按常规字重算，英文下选中项两端会被裁掉；按粗体量宽。
        # 注意要按样式表里的 14px 量，btn.font() 此刻还是默认字号。
        bold = QFont(btn.font())
        bold.setPixelSize(14)
        bold.setBold(True)
        btn.setMinimumWidth(QFontMetrics(bold).horizontalAdvance(title) + 36)
        self._style_btn(btn)
        self._group.addButton(btn)
        return btn

    def _add_btn(self, btn):
        self._layout.insertWidget(self._layout.count() - 1, btn)
        self._host.adjustSize()
        self._host.setMinimumWidth(max(self._host.sizeHint().width(), self._layout.sizeHint().width()))
        if len(self._group.buttons()) == 1:
            btn.setChecked(True)

    def select_page(self, page, animate: bool = True):
        hit = self._buttons.get(id(page))
        if not hit:
            return
        btn, _ = hit
        btn.setChecked(True)
        self._move_indicator(btn, animate=animate)
        self._scroll.ensureWidgetVisible(btn, 24, 0)

    def dragEnterEvent(self, e):
        if e.mimeData().hasFormat(NAV_MIME):
            e.acceptProposedAction()

    def dropEvent(self, e):
        key = bytes(e.mimeData().data(NAV_MIME)).decode("utf-8", "ignore")
        if key:
            self.unpinRequested.emit(key)
        e.acceptProposedAction()

    def _indicator_rect(self, btn) -> QRect:
        r = btn.geometry()
        pad = 16
        return QRect(r.x() + pad, self._host.height() - 6, max(16, r.width() - pad * 2), 2)

    def _move_indicator(self, btn, animate: bool = True):
        if btn is None:
            return
        target = self._indicator_rect(btn)
        self._indicator.show()
        self._indicator.raise_()
        if (not animate) or (not self._indicator.geometry().isValid()) or self._indicator.width() < 4:
            self._ind_anim.stop()
            self._indicator.setGeometry(target)
            return
        self._ind_anim.stop()
        self._ind_anim.setStartValue(self._indicator.geometry())
        self._ind_anim.setEndValue(target)
        self._ind_anim.start()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        btn = self._group.checkedButton()
        if btn:
            self._move_indicator(btn, animate=False)

    def showEvent(self, event):
        super().showEvent(event)
        btn = self._group.checkedButton()
        if btn:
            self._move_indicator(btn, animate=False)


class DownloadSection(QWidget):
    """侧栏「下载」：分类横条切换子页。

    子页懒加载：bind 收 (标题, getter)，第一次进入分区或点开某页时
    getter 才真正构造页面（MainWindow._ensure_sub 负责构造+注册+回填
    本分区）。冷启动不用再为 8 个搜索页各建一整套表单。
    """

    def __init__(self, backend, parent=None):
        super().__init__(parent)
        self.setObjectName("downloadSection")
        self.backend = backend
        self.hub = self
        self._by_widget = {}
        self._pending = []  # [(title, getter)]，按声明顺序

        self.cat = DownloadCatBar()
        self.cat.currentChanged.connect(self.show_page)
        self.stack = SlideHStack(self)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self.cat)
        root.addWidget(self.stack, 1)

    def add_page(self, page, title: str = ""):
        if page is None or page in self._by_widget:
            return
        self.stack.addWidget(page)
        self._by_widget[page] = title
        if title and not self.cat.wire_item(title, page):
            # bind 没建过懒按钮（如重建分区时顺序错位）才直接补一个
            self.cat.add_item(title, page)
        if self.stack.count() == 1:
            self.stack.setCurrentWidget(page)
            self.cat.select_page(page, animate=False)

    def bind(self, items: list, opener=None):
        del opener
        for spec in items:
            title, getter = spec[0], spec[1]
            key = spec[2] if len(spec) > 2 else ""
            index = len(self._pending)
            self._pending.append((title, getter))
            # 按钮立刻建（横条完整），页面留到第一次点击/进入才构造
            self.cat.add_lazy_item(title, self, index, key)

    def _open_pending(self, index: int):
        """懒按钮被点：先构造对应子页（getter → _ensure_sub → add_page），再切换。"""
        if not (0 <= index < len(self._pending)):
            return
        _title, getter = self._pending[index]
        page = getter()
        if page is not None:
            self.show_page(page)

    def ensure_first(self):
        """第一次进入分区时构造第一个子页（add_page 会把它设为当前页）。"""
        if self._by_widget or not self._pending:
            return
        _title, getter = self._pending[0]
        getter()

    def has_page(self, page) -> bool:
        return page is self or page in self._by_widget

    def current_page(self):
        return self.stack.currentWidget()

    def pages(self) -> list:
        return list(self._by_widget)

    def pending_specs(self) -> list:
        return list(self._pending)

    def show_hub(self):
        self.ensure_first()
        if self._by_widget:
            self.show_page(next(iter(self._by_widget)))

    def show_page(self, page):
        if page is self:
            self.show_hub()
            return
        if page not in self._by_widget:
            return
        if page is not self.stack.currentWidget():
            self.stack.slide_to(page)
        self.cat.select_page(page)
        win = self.window()
        fn = getattr(win, "_reload_page", None)
        if callable(fn):
            fn(page)


class MoreSection(DownloadSection):
    """侧栏「更多」：杂项页（实例/模组/账号/联机/服务器/时长/反馈/设置）共用横条切换壳。"""
