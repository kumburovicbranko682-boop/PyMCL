# -*- coding: utf-8 -*-
"""游戏实时日志窗口（HMCL「显示日志」同款）：级别高亮 / 筛选 / 搜索 / 导出。"""
import html

from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    CaptionLabel, CheckBox, LineEdit, MessageBox, PushButton, TextEdit,
    TransparentTogglePushButton, FluentIcon as FIF, InfoBar, InfoBarPosition,
)

from mclauncher import game_log
from mclauncher.i18n import tr

# 展示用五档：TRACE 并进 DEBUG
_FILTERS = ("fatal", "error", "warn", "info", "debug")
_FILTER_NAMES = {
    "fatal": "FATAL", "error": "ERROR", "warn": "WARN",
    "info": "INFO", "debug": "DEBUG",
}
_COLORS = {
    "fatal": "#FF4D4F",
    "error": "#E8564C",
    "warn": "#DFA53A",
    "debug": "#8A8A8A",
    "trace": "#8A8A8A",
}
_MAX_ROWS = 4000


def _bucket(level: str) -> str:
    return "debug" if level == "trace" else (level if level in _FILTERS else "info")


class GameLogWindow(QWidget):
    """非模态窗口，1 秒轮询 backend.game_log 增量拉新行。"""

    def __init__(self, backend, task_id: str = "", title: str = "", parent=None):
        super().__init__(parent)
        self.backend = backend
        self.task_id = task_id or ""
        self.setWindowFlag(Qt.Window)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowTitle(tr("游戏日志") + (f" · {title}" if title else ""))
        self.resize(920, 560)

        self._rows: list[tuple[str, str]] = []
        self._since = 0
        self._prev_level = "info"
        self._counts = {k: 0 for k in _FILTERS}
        self._polling = False

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(8)

        bar = QHBoxLayout()
        bar.setSpacing(10)
        self.filters: dict[str, CheckBox] = {}
        for key in _FILTERS:
            box = CheckBox(_FILTER_NAMES[key])
            box.setChecked(True)
            box.toggled.connect(self._refill)
            self.filters[key] = box
            bar.addWidget(box)
        self.search = LineEdit()
        self.search.setPlaceholderText(tr("搜索日志…"))
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._refill)
        bar.addWidget(self.search, 1)
        self.auto_btn = TransparentTogglePushButton(tr("自动滚动"))
        self.auto_btn.setChecked(True)
        bar.addWidget(self.auto_btn)
        self.export_btn = PushButton(FIF.SAVE, tr("导出"))
        self.export_btn.clicked.connect(self._export)
        bar.addWidget(self.export_btn)
        # HMCL 日志窗口同款：游戏卡死时导出线程转储 / 直接结束进程
        self.dump_btn = PushButton(FIF.CODE, tr("导出运行栈"))
        self.dump_btn.setToolTip(
            tr("游戏卡死时点这里：不打断游戏，导出 jstack 线程转储用于定位卡顿"))
        self.dump_btn.clicked.connect(self._dump_stack)
        bar.addWidget(self.dump_btn)
        self.kill_btn = PushButton(FIF.CLOSE, tr("结束游戏进程"))
        self.kill_btn.clicked.connect(self._kill)
        bar.addWidget(self.kill_btn)
        root.addLayout(bar)

        self.view = TextEdit(self)
        self.view.setReadOnly(True)
        self.view.setLineWrapMode(TextEdit.LineWrapMode.NoWrap)
        font = QFont("Consolas" if hasattr(QFont, "Monospace") else "monospace")
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setPointSize(9)
        self.view.setFont(font)
        root.addWidget(self.view, 1)

        self.status = CaptionLabel(tr("等待游戏输出…"))
        root.addWidget(self.status)

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._poll)
        self._timer.start()
        self._poll()

    # ---------------- 轮询 ----------------

    def _poll(self):
        if self._polling:
            return
        self._polling = True
        fetch = lambda: self.backend.game_log(self.task_id, self._since)
        call_async = getattr(self.backend, "call_async", None)
        if callable(call_async):
            call_async(fetch, self._on_chunk, self._on_fail)
        else:
            try:
                self._on_chunk(fetch())
            except Exception:
                self._on_fail()

    def _on_fail(self, *_):
        self._polling = False

    def _on_chunk(self, out):
        self._polling = False
        out = out or {}
        lines = out.get("lines") or []
        self._since = int(out.get("total") or self._since)
        if lines:
            rows = game_log.annotate(lines, self._prev_level)
            self._prev_level = rows[-1][0]
            self._rows += rows
            for level, _line in rows:
                self._counts[_bucket(level)] += 1
            if len(self._rows) > _MAX_ROWS:
                self._rows = self._rows[-_MAX_ROWS:]
            self._append_rows(rows)
            self._update_labels()
        if not out.get("running"):
            self._timer.stop()
            # 进程没了，运行栈导不出来、也没得结束（HMCL 同款置灰）
            self.dump_btn.setEnabled(False)
            self.kill_btn.setEnabled(False)
            self.status.setText(tr("游戏已退出，日志共 {n} 行").format(n=self._since))
        elif lines:
            self.status.setText(tr("日志共 {n} 行").format(n=self._since))

    # ---------------- 渲染 ----------------

    def _passes(self, level: str, text: str) -> bool:
        box = self.filters.get(_bucket(level))
        if box is not None and not box.isChecked():
            return False
        needle = self.search.text().strip().lower()
        return not needle or needle in text.lower()

    @staticmethod
    def _to_html(level: str, text: str) -> str:
        safe = html.escape(text) or "&nbsp;"
        color = _COLORS.get(level)
        if not color:
            return f"<div style='white-space:pre'>{safe}</div>"
        weight = "font-weight:bold;" if level in ("fatal", "error") else ""
        return f"<div style='white-space:pre;color:{color};{weight}'>{safe}</div>"

    def _append_rows(self, rows):
        parts = [self._to_html(lv, tx) for lv, tx in rows if self._passes(lv, tx)]
        if not parts:
            return
        cursor = self.view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertHtml("".join(parts))
        if self.auto_btn.isChecked():
            bar = self.view.verticalScrollBar()
            bar.setValue(bar.maximum())

    def _refill(self, *_):
        self.view.clear()
        self._append_rows(self._rows)

    def _update_labels(self):
        for key, box in self.filters.items():
            n = self._counts.get(key) or 0
            box.setText(f"{_FILTER_NAMES[key]} ({n})" if n else _FILTER_NAMES[key])

    # ---------------- 导出 ----------------

    def _export(self):
        path, _ = QFileDialog.getSaveFileName(
            self, tr("导出日志"), "game.log", tr("日志文件 (*.log *.txt)"))
        if not path:
            return
        try:
            game_log.export_lines([tx for _lv, tx in self._rows], path)
        except OSError as e:
            InfoBar.error(tr("导出失败"), str(e), parent=self,
                          position=InfoBarPosition.TOP, duration=4000)
            return
        InfoBar.success(tr("已导出"), path, parent=self,
                        position=InfoBarPosition.TOP, duration=3000)

    # ---------------- 运行栈 / 结束进程 ----------------

    def _dump_stack(self):
        """游戏卡死时导出线程转储（HMCL「导出游戏运行栈」同款）。"""
        self.dump_btn.setEnabled(False)
        job = lambda: self.backend.dump_game_stack(self.task_id)
        call_async = getattr(self.backend, "call_async", None)
        if callable(call_async):
            call_async(job, self._on_dump_ok, self._on_dump_fail)
            return
        try:
            self._on_dump_ok(job())
        except Exception as e:
            self._on_dump_fail(str(e))

    def _on_dump_ok(self, path):
        self.dump_btn.setEnabled(self._timer.isActive())
        InfoBar.success(tr("已导出运行栈"), str(path), parent=self,
                        position=InfoBarPosition.TOP, duration=5000)
        from mclauncher.crash import open_path
        open_path(Path(str(path)).parent)

    def _on_dump_fail(self, message):
        self.dump_btn.setEnabled(self._timer.isActive())
        InfoBar.error(tr("导出运行栈失败"), str(message), parent=self,
                      position=InfoBarPosition.TOP, duration=6000)

    def _kill(self):
        box = MessageBox(
            tr("结束游戏进程"),
            tr("结束这局游戏？未保存的游戏进度可能丢失。"),
            self.window())
        box.yesButton.setText(tr("结束"))
        box.cancelButton.setText(tr("取消"))
        if not box.exec():
            return
        try:
            self.backend.kill_game(self.task_id)
        except Exception as e:
            InfoBar.error(tr("操作失败"), str(e), parent=self,
                          position=InfoBarPosition.TOP, duration=4000)

    def closeEvent(self, event):
        self._timer.stop()
        super().closeEvent(event)
