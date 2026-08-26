# -*- coding: utf-8 -*-
"""游戏实时日志窗口（HMCL「显示日志」同款）：级别高亮 / 筛选 / 搜索 / 导出。"""
import html

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    CaptionLabel, CheckBox, LineEdit, PushButton, TextEdit,
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

    def closeEvent(self, event):
        self._timer.stop()
        super().closeEvent(event)
