# -*- coding: utf-8 -*-
"""下载任务中心：进度、速度、可展开日志；侧栏红点计数；运行中的游戏。"""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from ..motion import SmoothProgressBar
from qfluentwidgets import (
    CaptionLabel, FluentIcon as FIF, MessageBox, PlainTextEdit, ProgressBar, PushButton,
    ScrollArea, SimpleCardWidget, StrongBodyLabel, SubtitleLabel, TransparentToolButton,
)

from ..widgets import EmptyState, IconTile
from mclauncher.i18n import tr

# 存中文原文而不是 tr() 的结果：模块级 tr() 在 import 那一刻就定死了当时的语言，
# 之后用户切成英文，任务标题变了、这张表没变，前缀就永远匹配不上。
_TASK_ICONS = [
    ("安装游戏", FIF.GAME, "#4C8BF5"),
    ("安装整合包", FIF.FOLDER, "#7C5CD6"),
    ("安装模组", FIF.TAG, "#2FA36B"),
    ("安装光影", FIF.BRIGHTNESS, "#E8862E"),
    ("安装资源包", FIF.PHOTO, "#2E9FB8"),
    ("下载 Java", FIF.CODE, "#D95568"),
    ("启动游戏", FIF.PLAY, "#D95568"),
    ("微软登录", FIF.PEOPLE, "#8A6FBD"),
]


def _icon_for(title: str):
    for prefix, icon, color in _TASK_ICONS:
        if title.startswith(tr(prefix)):
            return icon, color
    return FIF.DOWNLOAD, "#4C8BF5"


def split_progress_message(message: str):
    text = message or ""
    if "  |  " in text:
        status, speed = text.split("  |  ", 1)
        return status.strip(), speed.strip()
    return text, ""


def _is_download_title(title: str) -> bool:
    """哪些任务算「下载任务」。

    判定必须和 `BackendAPI.is_download_title` 完全一致，否则侧栏红点计数
    和这里显示的卡片会对不上（此前这份副本漏了「皮肤站登录」，
    皮肤站登录会出现在任务页和底部下载条里，但侧栏不计数）。
    所以直接复用后端那一份，不再各留一份。
    """
    from ..backend import BackendAPI
    return BackendAPI.is_download_title(title)


class TaskCard(SimpleCardWidget):
    def __init__(self, task_id: str, title: str, backend, parent=None):
        super().__init__(parent)
        self.task_id = task_id
        self.backend = backend
        self._expanded = tr("整合包") in (title or "")
        self.setMinimumHeight(96)

        icon, color = _icon_for(title)
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(8)

        row = QHBoxLayout()
        row.setSpacing(14)
        row.addWidget(IconTile(title.replace(tr("安装"), "").replace(tr("下载"), "").strip() or "T",
                               color, size=46))

        body = QVBoxLayout()
        body.setSpacing(6)
        top = QHBoxLayout()
        top.addWidget(StrongBodyLabel(title), 1)
        self.toggle_btn = TransparentToolButton(
            FIF.CARE_UP_SOLID if self._expanded else FIF.CARE_DOWN_SOLID)
        self.toggle_btn.setToolTip(tr("收起日志") if self._expanded else tr("显示日志"))
        self.cancel_btn = TransparentToolButton(FIF.CLOSE)
        self.cancel_btn.setToolTip(tr("取消任务"))
        top.addWidget(self.toggle_btn)
        top.addWidget(self.cancel_btn)
        body.addLayout(top)
        self.progress = SmoothProgressBar(self)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        body.addWidget(self.progress)
        status_row = QHBoxLayout()
        self.status = CaptionLabel(tr("排队中…"))
        self.speed = CaptionLabel("")
        self.speed.setAlignment(Qt.AlignRight)
        status_row.addWidget(self.status, 1)
        status_row.addWidget(self.speed, 0)
        body.addLayout(status_row)
        row.addLayout(body, 1)
        root.addLayout(row)

        self.log_edit = PlainTextEdit()
        self.log_edit.setReadOnly(True)
        self.log_edit.setPlaceholderText(tr("安装过程的详细日志会显示在这里"))
        self.log_edit.setFixedHeight(240)
        self.log_edit.setMaximumBlockCount(2500)
        self.log_edit.setVisible(self._expanded)
        root.addWidget(self.log_edit)

        self.toggle_btn.clicked.connect(self._toggle_log)
        self.cancel_btn.clicked.connect(self._cancel)

    def _toggle_log(self):
        self._expanded = not self._expanded
        self.log_edit.setVisible(self._expanded)
        self.toggle_btn.setIcon(FIF.CARE_UP_SOLID if self._expanded else FIF.CARE_DOWN_SOLID)
        self.toggle_btn.setToolTip(tr("收起日志") if self._expanded else tr("显示日志"))

    def _cancel(self):
        self.backend.cancel_task(self.task_id)
        self.status.setText(tr("正在取消…"))
        self.cancel_btn.setEnabled(False)

    def set_progress(self, current: int, total: int, message: str):
        if total > 0:
            self.progress.setValue(min(100, max(0, int(current * 100 / total))))
        status, speed = split_progress_message(message)
        self.status.setText(status or tr("处理中…"))
        self.speed.setText(speed)

    def append_log(self, text: str):
        if not text:
            return
        self.log_edit.appendPlainText(text)

    def set_finished(self, success: bool, message: str):
        self.cancel_btn.setEnabled(False)
        if success:
            self.progress.setValue(100)
            self.status.setText(f"✔ {message}")
        else:
            self.status.setText(f"✘ {message}")
        self.speed.setText("")
        if message:
            self.log_edit.appendPlainText(message)
        if not success and not self._expanded:
            self._toggle_log()


class DownloadDock(SimpleCardWidget):
    """任意页面底部的下载条：速度 + 倒三角展开日志。"""

    def __init__(self, backend, parent=None):
        super().__init__(parent)
        self.backend = backend
        self._expanded = False
        self._active: dict[str, str] = {}
        self._current = None
        self.setFixedWidth(560)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 10, 14, 10)
        root.setSpacing(6)

        bar = QHBoxLayout()
        bar.setSpacing(10)
        self.title = StrongBodyLabel(tr("下载任务"))
        self.status = CaptionLabel(tr("就绪"))
        self.speed = CaptionLabel("")
        self.toggle_btn = TransparentToolButton(FIF.CARE_DOWN_SOLID)
        self.toggle_btn.setToolTip(tr("显示日志"))
        bar.addWidget(self.title)
        bar.addWidget(self.status, 1)
        bar.addWidget(self.speed)
        bar.addWidget(self.toggle_btn)
        root.addLayout(bar)

        self.progress = SmoothProgressBar(self)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        root.addWidget(self.progress)

        self.log_edit = PlainTextEdit()
        self.log_edit.setReadOnly(True)
        self.log_edit.setPlaceholderText(tr("安装过程的详细日志会显示在这里"))
        self.log_edit.setFixedHeight(220)
        self.log_edit.setMaximumBlockCount(2500)
        self.log_edit.hide()
        root.addWidget(self.log_edit)

        self.toggle_btn.clicked.connect(self._toggle)
        backend.task_added.connect(self._add)
        backend.progress.connect(self._progress)
        backend.log.connect(self._log)
        backend.finished.connect(self._finished)
        self.hide()

    def _toggle(self):
        self._expanded = not self._expanded
        self.log_edit.setVisible(self._expanded)
        self.toggle_btn.setIcon(FIF.CARE_UP_SOLID if self._expanded else FIF.CARE_DOWN_SOLID)
        self.toggle_btn.setToolTip(tr("收起日志") if self._expanded else tr("显示日志"))
        self.adjustSize()
        parent = self.parent()
        if parent and hasattr(parent, "_place_download_dock"):
            parent._place_download_dock()

    def _add(self, task_id, title):
        if not _is_download_title(title):
            return
        self._active[task_id] = title
        self._current = task_id
        n = len(self._active)
        self.title.setText(f"下载任务（{n}）")
        self.status.setText(title)
        self.progress.setValue(0)
        self.speed.setText("")
        if n == 1:
            self.log_edit.clear()
        self.log_edit.appendPlainText(f"—— {title} ——")
        if tr("整合包") in (title or "") and not self._expanded:
            self._toggle()
        parent = self.parent()
        if parent and hasattr(parent, "_place_download_dock"):
            parent._place_download_dock()
        else:
            self.show()
            self.raise_()

    def _progress(self, task_id, current, total, message):
        if task_id not in self._active:
            return
        self._current = task_id
        if total > 0:
            self.progress.setValue(min(100, max(0, int(current * 100 / total))))
        status, speed = split_progress_message(message)
        title = self._active.get(task_id, "")
        n = len(self._active)
        self.title.setText(f"下载任务（{n}）")
        self.status.setText(status or title or tr("处理中…"))
        self.speed.setText(speed)

    def _log(self, task_id, text):
        if task_id not in self._active and self._current != task_id:
            return
        if text:
            self.log_edit.appendPlainText(text)

    def _finished(self, task_id, success, message):
        self._active.pop(task_id, None)
        n = len(self._active)
        if message:
            self.log_edit.appendPlainText(message)
        if n <= 0:
            self.title.setText(tr("下载任务"))
            self.status.setText(tr("✔ 全部完成") if success else (message or tr("已结束")))
            self.speed.setText("")
            self.progress.setValue(100 if success else self.progress.value())
            parent = self.parent()
            if parent and hasattr(parent, "_place_download_dock"):
                parent._place_download_dock()
            else:
                self.hide()
            return
        self.title.setText(f"下载任务（{n}）")
        self._current = next(iter(self._active))
        self.status.setText(self._active[self._current])


def _fmt_uptime(seconds) -> str:
    s = max(0, int(seconds or 0))
    if s >= 3600:
        return f"{s // 3600}h {(s % 3600) // 60}m"
    if s >= 60:
        return f"{s // 60}m {s % 60}s"
    return f"{s}s"


class RunningGameCard(SimpleCardWidget):
    """一个运行中的游戏进程：版本 / 实例 / 账号 / 运行时长 + 结束按钮。"""

    def __init__(self, row: dict, page, parent=None):
        super().__init__(parent)
        self.row = row
        self.page = page
        self.setMinimumHeight(64)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 10, 16, 10)
        lay.setSpacing(14)
        lay.addWidget(IconTile(row.get("version") or "G", "#2E9B6B", size=40))
        info = QVBoxLayout()
        info.setSpacing(2)
        info.addWidget(StrongBodyLabel(row.get("version") or "?"))
        self.meta = CaptionLabel("")
        info.addWidget(self.meta)
        lay.addLayout(info, 1)
        self.log_btn = PushButton(FIF.DOCUMENT, tr("日志"))
        self.log_btn.clicked.connect(self._show_log)
        lay.addWidget(self.log_btn)
        self.kill_btn = PushButton(FIF.CLOSE, tr("结束游戏进程"))
        self.kill_btn.clicked.connect(self._kill)
        lay.addWidget(self.kill_btn)
        self.refresh(row)

    def refresh(self, row: dict):
        self.row = row
        bits = [row.get("instance") or "", row.get("account") or "",
                tr("已运行 {t}").format(t=_fmt_uptime(row.get("uptime"))),
                f"PID {row.get('pid')}" if row.get("pid") else ""]
        self.meta.setText("  ·  ".join(b for b in bits if b))

    def _show_log(self):
        self.page.open_game_log(self.row)

    def _kill(self):
        box = MessageBox(
            tr("结束游戏进程"),
            tr("结束「{name}」？未保存的游戏进度可能丢失。").format(
                name=self.row.get("version") or "?"),
            self.page.window())
        box.yesButton.setText(tr("结束"))
        box.cancelButton.setText(tr("取消"))
        if not box.exec():
            return
        try:
            self.page.backend.kill_game(self.row.get("task_id") or "")
        except Exception:
            pass
        self.page.reload_running()


class TasksPage(QWidget):
    def __init__(self, backend, parent=None):
        super().__init__(parent)
        self.setObjectName("tasksPage")
        self.backend = backend
        self._cards: dict[str, TaskCard] = {}
        self._done: set[str] = set()
        self._running_cards: dict[str, RunningGameCard] = {}
        self._log_windows: dict[str, QWidget] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 20, 28, 20)
        root.setSpacing(14)
        head = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.addWidget(SubtitleLabel(tr("下载任务")))
        title_box.addWidget(CaptionLabel(tr("下载板块内所有安装任务的实时进度；整合包安装会自动展开详细日志")))
        head.addLayout(title_box, 1)
        self.clear_btn = PushButton(tr("清除已完成"))
        self.clear_btn.setIcon(FIF.BROOM)
        self.clear_btn.setEnabled(False)
        self.clear_btn.clicked.connect(self._clear_finished)
        head.addWidget(self.clear_btn, 0, Qt.AlignTop)
        root.addLayout(head)

        # 运行中的游戏（对标 HMCL 游戏管理：多开时每个进程都可见可结束）
        self.running_host = QWidget(self)
        rv = QVBoxLayout(self.running_host)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(8)
        rv.addWidget(SubtitleLabel(tr("运行中的游戏")))
        self.running_list = QVBoxLayout()
        self.running_list.setSpacing(8)
        rv.addLayout(self.running_list)
        self.running_host.hide()
        root.addWidget(self.running_host)

        self._running_timer = QTimer(self)
        self._running_timer.setInterval(5000)
        self._running_timer.timeout.connect(self.reload_running)
        backend.game_started.connect(self.reload_running)
        backend.game_exited.connect(lambda *_: self.reload_running())

        scroll = ScrollArea(self)
        scroll.setWidgetResizable(True)
        host = QWidget()
        self.list_layout = QVBoxLayout(host)
        self.list_layout.setContentsMargins(0, 0, 8, 0)
        self.list_layout.setSpacing(10)
        scroll.setWidget(host)
        root.addWidget(scroll, 1)

        self.empty = EmptyState(FIF.DOWNLOAD, tr("暂无任务 —— 去下载板块里的版本 / 整合包 / 模组 / 光影 / 资源包 / Java 发起"))
        self.list_layout.addWidget(self.empty)
        self.list_layout.addStretch(1)

        backend.task_added.connect(self._add)
        backend.progress.connect(self._progress)
        backend.log.connect(self._log)
        backend.finished.connect(self._finished)
        self.reload_running()

    # ------------------------------------------------------------------
    # 运行中的游戏
    # ------------------------------------------------------------------
    def open_game_log(self, row: dict):
        """打开（或激活）某个运行中游戏的实时日志窗口。"""
        from .game_log_window import GameLogWindow
        tid = str(row.get("task_id") or "")
        win = self._log_windows.get(tid)
        if win is not None:
            try:
                win.show()
                win.raise_()
                win.activateWindow()
                return
            except RuntimeError:  # 已被销毁
                self._log_windows.pop(tid, None)
        win = GameLogWindow(self.backend, tid, row.get("version") or "",
                            parent=self.window())
        self._log_windows[tid] = win
        win.destroyed.connect(lambda *_: self._log_windows.pop(tid, None))
        win.show()

    def reload_running(self):
        try:
            rows = self.backend.list_running_games() or []
        except Exception:
            rows = []
        alive = {r.get("task_id"): r for r in rows}
        for tid in list(self._running_cards):
            if tid not in alive:
                card = self._running_cards.pop(tid)
                self.running_list.removeWidget(card)
                card.setParent(None)
                card.deleteLater()
        for tid, row in alive.items():
            card = self._running_cards.get(tid)
            if card is None:
                card = RunningGameCard(row, self)
                self._running_cards[tid] = card
                self.running_list.addWidget(card)
            else:
                card.refresh(row)
        if rows:
            self.running_host.show()
            if not self._running_timer.isActive():
                self._running_timer.start()
        else:
            self.running_host.hide()
            self._running_timer.stop()

    def _add(self, task_id, title):
        if not _is_download_title(title):
            return
        self.empty.hide()
        card = TaskCard(task_id, title, self.backend)
        self._cards[task_id] = card
        self.list_layout.insertWidget(self.list_layout.count() - 1, card)

    def _progress(self, task_id, current, total, message):
        card = self._cards.get(task_id)
        if card:
            card.set_progress(current, total, message)

    def _log(self, task_id, text):
        card = self._cards.get(task_id)
        if card:
            card.append_log(text)

    def _finished(self, task_id, success, message):
        card = self._cards.get(task_id)
        if card:
            card.set_finished(success, message)
            self._done.add(task_id)
            self.clear_btn.setEnabled(True)

    def _clear_finished(self):
        for task_id in list(self._done):
            card = self._cards.pop(task_id, None)
            if card is not None:
                self.list_layout.removeWidget(card)
                card.setParent(None)
                card.deleteLater()
        self._done.clear()
        self.clear_btn.setEnabled(False)
        if not self._cards:
            self.empty.show()
