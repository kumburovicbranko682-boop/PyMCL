# -*- coding: utf-8 -*-
"""游玩时长统计展示页。"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QStackedWidget, QVBoxLayout, QWidget,
)
from qfluentwidgets import (
    FluentIcon as FIF, InfoBar, PushButton, ScrollArea, StrongBodyLabel,
)

from ..pcl_chrome import Theme
from ..widgets import EmptyState, Pill
from mclauncher.i18n import tr

_CLOCK_ICON = getattr(FIF, "CLOCK", None) or getattr(FIF, "DATE_TIME", None) or FIF.HELP


class PlaytimePage(QWidget):
    """游玩时长页面。"""

    def __init__(self, backend, parent=None):
        super().__init__(parent)
        self.backend = backend
        self._instance = ""

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 顶栏
        self.topBar = QFrame()
        self.topBar.setObjectName("topBar")
        self.topBar.setStyleSheet(f"#topBar {{ background: {Theme.card}; border-bottom: 1px solid {Theme.line}; }}")
        self.topBar.setFixedHeight(56)
        tl = QHBoxLayout(self.topBar)
        tl.setContentsMargins(24, 0, 24, 0)
        self._title_lab = StrongBodyLabel(tr("游玩时长"))
        self._title_lab.setStyleSheet(f"color: {Theme.text}; font-size: 18px;")
        tl.addWidget(self._title_lab)
        tl.addStretch(1)
        clear_btn = PushButton(tr("清除记录"))
        clear_btn.clicked.connect(self._on_clear)
        tl.addWidget(clear_btn)
        root.addWidget(self.topBar)

        # 内容
        scroll = ScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self._content = QWidget()
        self._content.setStyleSheet("background: transparent;")
        self._lay = QVBoxLayout(self._content)
        self._lay.setContentsMargins(24, 24, 24, 24)
        self._lay.setSpacing(16)
        scroll.setWidget(self._content)
        self._scroll = scroll

        self.empty = EmptyState(_CLOCK_ICON, tr("还没有游玩记录\n启动游戏后会自动记录"))
        self.empty.hide()
        # 内容区和空状态占同一格：以前空状态是单独一行且 stretch=0，
        # 隐藏内容后滚动区还占着全部高度，空状态被压成底部一条。
        self._body = QStackedWidget()
        self._body.addWidget(scroll)
        self._body.addWidget(self.empty)
        root.addWidget(self._body, 1)

    def reload(self, instance: str = ""):
        self._instance = instance or ""
        self._render()

    def restyle(self):
        """主题切换时刷新一次性样式。"""
        self.topBar.setStyleSheet(f"#topBar {{ background: {Theme.card}; border-bottom: 1px solid {Theme.line}; }}")
        self._title_lab.setStyleSheet(f"color: {Theme.text}; font-size: 18px;")
        if hasattr(self.empty, "restyle") and self.empty.isVisible():
            self.empty.restyle()
        self._render()

    def _render(self):
        # 清空
        while self._lay.count():
            w = self._lay.takeAt(0).widget()
            if w:
                w.deleteLater()

        try:
            data = self.backend.get_playtime(self._instance) if self._instance else self.backend.get_all_playtime()
        except Exception:
            data = {}

        rows = []
        if isinstance(data, dict) and "total" in data:
            if data.get("total", 0) > 0:
                rows.append((None, data))
        elif isinstance(data, dict):
            rows = [(name, d) for name, d in data.items()
                    if isinstance(d, dict) and d.get("total", 0) > 0]
        # 只按顶层 total 判空会漏掉「所有实例时长都是 0」这种情况，那样会渲染出一整页空白
        if not rows:
            self._body.setCurrentWidget(self.empty)
            self.empty.show()
            return
        self._body.setCurrentWidget(self._scroll)

        for name, row in rows:
            if name is None:
                self._add_stat(row)
            else:
                self._add_instance_card(name, row)

    def _add_instance_card(self, name: str, data: dict):
        card = QFrame()
        card.setObjectName("ptCard")
        card.setStyleSheet(
            f"#ptCard {{ background: {Theme.card}; border: 1px solid {Theme.line};"
            " border-radius: 10px; }"
        )
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 12, 16, 12)
        # 标题行
        tl = QHBoxLayout()
        tl.setSpacing(8)
        n = QLabel(name)
        n.setStyleSheet(f"color: {Theme.title}; font-size: 15px; font-weight: 700; background: transparent;")
        tl.addWidget(n)
        total_s = data.get("total", 0)
        total_l = QLabel(self._fmt(total_s))
        total_l.setStyleSheet(f"color: {Theme.text}; font-size: 14px; background: transparent;")
        tl.addWidget(total_l)
        tl.addStretch(1)
        lay.addLayout(tl)
        # 版本列表
        for vid, secs in sorted(data.get("versions", {}).items(), key=lambda x: -x[1]):
            if secs <= 0:
                continue
            rl = QHBoxLayout()
            rl.setSpacing(8)
            v = QLabel(vid)
            v.setStyleSheet(f"color: {Theme.text}; font-size: 12px; background: transparent;")
            rl.addWidget(v)
            pill = Pill(self._fmt(secs))
            rl.addWidget(pill)
            rl.addStretch(1)
            lay.addLayout(rl)
        self._lay.addWidget(card)

    def _add_stat(self, data: dict):
        total = data.get("total", 0)
        versions = data.get("versions", {})
        card = QFrame()
        card.setObjectName("ptCard")
        card.setStyleSheet(
            f"#ptCard {{ background: {Theme.card}; border: 1px solid {Theme.line};"
            " border-radius: 10px; }"
        )
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 12, 16, 12)
        # 总时长
        tl = QHBoxLayout()
        tl.setSpacing(8)
        n = QLabel(tr("总时长"))
        n.setStyleSheet(f"color: {Theme.title}; font-size: 16px; font-weight: 700; background: transparent;")
        tl.addWidget(n)
        v = QLabel(self._fmt(total))
        v.setStyleSheet(f"color: {Theme.text}; font-size: 20px; font-weight: 700; background: transparent;")
        tl.addWidget(v)
        tl.addStretch(1)
        lay.addLayout(tl)
        # 分版本
        for vid, secs in sorted(versions.items(), key=lambda x: -x[1]):
            if secs <= 0:
                continue
            rl = QHBoxLayout()
            rl.setSpacing(8)
            vn = QLabel(vid)
            vn.setStyleSheet(f"color: {Theme.text}; font-size: 12px; background: transparent;")
            rl.addWidget(vn)
            pill = Pill(self._fmt(secs))
            rl.addWidget(pill)
            rl.addStretch(1)
            lay.addLayout(rl)
        self._lay.addWidget(card)

    def _fmt(self, seconds: int) -> str:
        try:
            return self.backend.format_playtime(seconds)
        except Exception:
            s = seconds or 0
            h = s // 3600
            m = (s % 3600) // 60
            if h > 0:
                return tr("{0} 小时 {1} 分钟").format(h, m)
            return tr("{0} 分钟").format(m)

    def _on_clear(self):
        from qfluentwidgets import MessageBox
        box = MessageBox(tr("确认清除"), tr("清除所有游玩时长记录？此操作不可恢复。"), self)
        if box.exec():
            try:
                self.backend.clear_playtime(self._instance)
                InfoBar.success(tr("已清除"), "", duration=2000, parent=self)
                self.reload(self._instance)
            except Exception as e:
                InfoBar.error(tr("清除失败"), str(e), duration=3000, parent=self)