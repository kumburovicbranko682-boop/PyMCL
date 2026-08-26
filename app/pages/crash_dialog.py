# -*- coding: utf-8 -*-
"""PCL 同款：Minecraft 崩溃 / 启动器未捕获异常弹窗（含一键修复）。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QVBoxLayout
from qfluentwidgets import (
    BodyLabel, CaptionLabel, InfoBar, InfoBarPosition, PlainTextEdit,
    PrimaryPushButton, PushButton, StrongBodyLabel, SubtitleLabel,
)

from mclauncher.crash import HELP_FOOTER, export_report, open_path
from mclauncher.i18n import tr
from ..pcl_chrome import Theme


class CrashDialog(QDialog):
    def __init__(self, report: dict | None = None, parent=None, *,
                 title: str = "", detail: str = "", backend=None):
        super().__init__(parent)
        self.report = report or {}
        self.backend = backend or getattr(parent, "backend", None)
        self.setWindowTitle(title or self.report.get("title") or tr("Minecraft 出现错误"))
        self.setMinimumSize(560, 420)
        self.resize(640, 520)
        self.setModal(True)
        # 裸 QDialog 不吃 qfluentwidgets 的主题：深色下背景仍是系统浅灰，
        # 而 Fluent 的标签已经被刷成白字，不显式铺底就是白字压浅底。
        self.setAutoFillBackground(True)
        self.setStyleSheet(
            f"CrashDialog {{ background: {Theme.bg}; }}"
            f"CrashDialog QLabel {{ color: {Theme.text}; background: transparent; }}"
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 18, 22, 16)
        root.setSpacing(10)

        head = SubtitleLabel(self.windowTitle(), self)
        root.addWidget(head)

        headline = self.report.get("headline") or ""
        if headline and headline != self.windowTitle():
            root.addWidget(BodyLabel(headline, self))

        body = PlainTextEdit(self)
        body.setReadOnly(True)
        text = (detail or self.report.get("detail") or "").strip()
        body.setPlainText(text)
        root.addWidget(body, 1)

        hint = CaptionLabel(self.report.get("help") or HELP_FOOTER, self)
        hint.setWordWrap(True)
        root.addWidget(hint)

        actions = list(self.report.get("actions") or [])
        if actions:
            root.addWidget(StrongBodyLabel(tr("建议操作"), self))
            act_row = QHBoxLayout()
            act_row.setSpacing(8)
            for action in actions:
                btn = PushButton(tr(action.get("label") or action.get("id") or "修复"), self)
                btn.clicked.connect(lambda _=False, a=action, b=btn: self._run_action(a, b))
                act_row.addWidget(btn)
            act_row.addStretch(1)
            root.addLayout(act_row)

        btns = QHBoxLayout()
        btns.addStretch(1)
        self.relaunch_btn = PushButton(tr("重新启动"), self)
        self.ok_btn = PrimaryPushButton(tr("确定"), self)
        self.view_btn = PushButton(tr("查看输出"), self)
        self.export_btn = PushButton(tr("导出错误报告"), self)
        self.send_btn = PushButton(tr("发送给开发者"), self)
        self.want_relaunch = False
        self.relaunch_btn.clicked.connect(self._relaunch)
        self.ok_btn.clicked.connect(self.accept)
        self.view_btn.clicked.connect(self._view)
        self.export_btn.clicked.connect(self._export)
        self.send_btn.clicked.connect(self._send)
        has_file = bool(self.report.get("direct_file") or self.report.get("output_tail"))
        can_relaunch = bool(self.report.get("instance") and self.report.get("version"))
        self.relaunch_btn.setVisible(can_relaunch)
        self.view_btn.setVisible(has_file)
        self.export_btn.setVisible(bool(self.report))
        btns.addWidget(self.relaunch_btn)
        btns.addWidget(self.view_btn)
        btns.addWidget(self.export_btn)
        btns.addWidget(self.send_btn)
        btns.addWidget(self.ok_btn)
        root.addLayout(btns)

    def _relaunch(self):
        self.want_relaunch = True
        self.accept()

    def _run_action(self, action: dict, btn: PushButton | None = None):
        backend = self.backend or getattr(self.parent(), "backend", None)
        if backend is None:
            InfoBar.error(tr("无法执行"), tr("没有连接到启动器后端"), parent=self,
                          position=InfoBarPosition.TOP, duration=3500)
            return
        if btn is not None:
            btn.setEnabled(False)
        try:
            result = backend.apply_crash_action(action, self.report)
        except Exception as exc:
            if btn is not None:
                btn.setEnabled(True)
            InfoBar.error(tr("操作失败"), str(exc), parent=self,
                          position=InfoBarPosition.TOP, duration=4500)
            return
        ok = bool((result or {}).get("ok"))
        msg = (result or {}).get("message") or (tr("已完成") if ok else tr("失败"))
        if ok:
            InfoBar.success(tr("已处理"), msg, parent=self,
                            position=InfoBarPosition.TOP, duration=4000)
            if (action or {}).get("id") == "disable_mods" and btn is not None:
                btn.setText(tr("已禁用"))
        else:
            if btn is not None:
                btn.setEnabled(True)
            InfoBar.error(tr("操作失败"), msg, parent=self,
                          position=InfoBarPosition.TOP, duration=4500)

    def _view(self):
        path = self.report.get("direct_file") or ""
        if path:
            open_path(path)
            return
        tail = self.report.get("output_tail") or self.report.get("detail") or ""
        if not tail:
            return
        from mclauncher import utils
        dest = utils.ROOT / tr("游戏崩溃前的输出.txt")
        try:
            dest.write_text(tail, encoding="utf-8")
            open_path(dest)
        except OSError as exc:
            InfoBar.error(tr("无法打开输出"), str(exc), parent=self,
                          position=InfoBarPosition.TOP, duration=4000)

    def _export(self):
        if not self.report:
            return
        try:
            path = export_report(self.report)
            open_path(path)
        except OSError as exc:
            InfoBar.error(tr("导出失败"), str(exc), parent=self,
                          position=InfoBarPosition.TOP, duration=4000)

    def _send(self):
        backend = self.backend or getattr(self.parent(), "backend", None)
        if backend is None:
            InfoBar.error(tr("无法发送"), tr("没有连接到启动器后端"), parent=self,
                          position=InfoBarPosition.TOP, duration=3500)
            return
        from mclauncher import feedback as fb
        if not fb.has_consent():
            from ..widgets import prompt_feedback_consent
            parent = self.parent() or self
            win = getattr(parent, "window", lambda: parent)()
            if not prompt_feedback_consent(win or self):
                InfoBar.warning(tr("未同意"), tr("不同意上传则不会发送"), parent=self,
                                position=InfoBarPosition.TOP, duration=3000)
                return
        self.send_btn.setEnabled(False)
        self.send_btn.setText(tr("发送中…"))
        report = dict(self.report or {})

        def work():
            return backend.submit_crash_feedback(report)

        def ok(_data):
            self.send_btn.setText(tr("已发送"))
            InfoBar.success(tr("已发给开发者"), tr("反馈中心会实时显示这条崩溃和本机配置"),
                            parent=self, position=InfoBarPosition.TOP, duration=3500)

        def err(exc):
            self.send_btn.setEnabled(True)
            self.send_btn.setText(tr("发送给开发者"))
            InfoBar.error(tr("发送失败"), str(exc), parent=self,
                          position=InfoBarPosition.TOP, duration=5000)

        backend.call_async(work, ok, err)


def show_launcher_error(parent, kind: str, text: str, log_file: str = ""):
    title = tr("启动器出现错误") if kind != "thread" else tr("启动器后台线程出错")
    dlg = CrashDialog(
        {
            "title": title,
            "headline": tr("未捕获异常已写入日志"),
            "detail": (text or "")[-8000:],
            "help": f"完整日志：{log_file}" if log_file else HELP_FOOTER,
            "direct_file": log_file,
            "files": [log_file] if log_file else [],
            "output_tail": text or "",
            "actions": [],
        },
        parent,
        title=title,
        backend=getattr(parent, "backend", None),
    )
    dlg.exec()
