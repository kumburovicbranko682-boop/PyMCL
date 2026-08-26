# -*- coding: utf-8 -*-
"""AI 助手页：多会话、流式、确认、进度。"""

from __future__ import annotations

import html
import json
import re
import threading

from PySide6.QtCore import Qt, QTimer, QThread, Signal
from PySide6.QtGui import QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QFrame, QHBoxLayout, QListWidget, QListWidgetItem,
    QSizePolicy, QVBoxLayout, QWidget,
)
from qfluentwidgets import (
    BodyLabel, CaptionLabel, CheckBox, ComboBox, FluentIcon as FIF, InfoBar,
    InfoBarPosition, LineEdit, MessageBoxBase, PlainTextEdit, PrimaryPushButton,
    ProgressBar, PushButton, RadioButton, ScrollArea, SettingCard, SubtitleLabel,
    SwitchButton, TransparentPushButton, TransparentToolButton,
)

from mclauncher.ai.agent import AgentCancelled, run_agent
from mclauncher.ai.client import AIClientError, HttpCancel
from mclauncher.ai import store as chat_store
from mclauncher.ai.defaults import DEFAULT_MODEL
from ..pcl_chrome import Theme
from mclauncher.i18n import tr

_STOP = {tr("已停止"), tr("已取消")}
_CHIPS = (tr("下一款游戏 1.20.1 Fabric"), tr("装钠和光影"), tr("启动闪退了帮我看"))
_WELCOME = (
    tr("我是启动器助手。可以帮你下游戏、装模组和整合包、看启动报错、查模组冲突、改常用配置。\n"
    "直接说你想做什么就行。写操作我会先让你确认。")
)
_WELCOME_NOCONFIRM = (
    tr("我是启动器助手。可以帮你下游戏、装模组和整合包、看启动报错、查模组冲突、改常用配置。\n"
    "直接说你想做什么就行。写操作会直接执行，不逐条询问。")
)
_FENCE = re.compile(r"```(?:\w+)?\n([\s\S]*?)```")
_CODE = re.compile(r"`([^`]+)`")
_BOLD = re.compile(r"\*\*(.+?)\*\*")


def _md(text: str) -> str:
    raw = text or ""
    pre_bg = "#2B2B2B" if Theme.dark else "#F4F6F5"
    pre_fg = "#E8E8E8" if Theme.dark else "#2B2B2B"
    parts = []
    idx = 0
    for m in _FENCE.finditer(raw):
        parts.append(_md_inline(raw[idx:m.start()]))
        code = html.escape(m.group(1).rstrip())
        parts.append(
            f"<pre style='background:{pre_bg};color:{pre_fg};padding:8px;border-radius:6px;"
            "white-space:pre-wrap;font-family:Consolas,monospace;font-size:12px;'>"
            f"{code}</pre>"
        )
        idx = m.end()
    parts.append(_md_inline(raw[idx:]))
    return "".join(parts)


def _md_inline(text: str) -> str:
    t = html.escape(text or "")
    t = _CODE.sub(r"<code>\1</code>", t)
    t = _BOLD.sub(r"<b>\1</b>", t)
    t = t.replace("\n", "<br>")
    return t


class AgentThread(QThread):
    delta = Signal(str)
    status = Signal(str, dict)
    need_confirm = Signal(str, dict, str)
    need_ask = Signal(list, str)
    done = Signal(str)
    failed = Signal(str)

    def __init__(self, backend, settings, history, user_text, parent=None):
        super().__init__(parent)
        self.backend = backend
        self.settings = settings
        self.history = history
        self.user_text = user_text
        self._cancel = False
        self._http = HttpCancel()
        self._confirm_ev = threading.Event()
        self._confirm_ok = False
        self._ask_ev = threading.Event()
        self._ask_result = None

    def cancel(self):
        self._cancel = True
        self._http.abort()
        self.answer_confirm(False)
        self.answer_ask(None)

    def answer_confirm(self, ok: bool):
        self._confirm_ok = bool(ok)
        self._confirm_ev.set()

    def answer_ask(self, result):
        self._ask_result = result
        self._ask_ev.set()

    def run(self):
        def on_delta(text):
            self.delta.emit(text)

        def on_status(kind, payload):
            self.status.emit(kind, payload or {})

        def confirm_fn(name, args, label):
            self._confirm_ev.clear()
            self.need_confirm.emit(name, args, label)
            self._confirm_ev.wait()
            return self._confirm_ok

        def ask_fn(questions, title):
            self._ask_ev.clear()
            self._ask_result = None
            self.need_ask.emit(list(questions or []), str(title or ""))
            self._ask_ev.wait()
            return self._ask_result

        def cancelled():
            return self._cancel

        try:
            text = run_agent(
                self.backend, self.settings, self.history, self.user_text,
                on_delta=on_delta, on_status=on_status,
                confirm_fn=confirm_fn, ask_fn=ask_fn, cancelled=cancelled,
                http_cancel=self._http,
            )
            if self._cancel:
                self.failed.emit(tr("已停止"))
                return
            self.done.emit(text or "")
        except AgentCancelled:
            self.failed.emit(tr("已停止"))
        except AIClientError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


class Bubble(QFrame):
    def __init__(self, role: str, text: str = "", parent=None):
        super().__init__(parent)
        self.role = role
        self._plain = text or ""
        self._live = False
        mine = role == "user"
        err = role == "error"
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(4)
        head = QHBoxLayout()
        self.who = CaptionLabel(tr("我") if mine else (tr("出错") if err else tr("助手")))
        head.addWidget(self.who)
        head.addStretch(1)
        if not mine:
            copy = TransparentPushButton(tr("复制"))
            copy.setFixedHeight(22)
            copy.clicked.connect(self._copy)
            head.addWidget(copy)
        lay.addLayout(head)
        self.body = BodyLabel("")
        self.body.setWordWrap(True)
        self.body.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.LinksAccessibleByMouse)
        self.body.setOpenExternalLinks(True)
        lay.addWidget(self.body)
        self._apply_style()
        self.set_text(text)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        self.setMaximumWidth(640)

    def _apply_style(self):
        err = self.role == "error"
        if self.role == "user":
            bg = "#1E3A2E" if Theme.dark else "#E8F6EF"
        elif err:
            bg = "#3A1E1E" if Theme.dark else "#FDECEC"
        else:
            bg = Theme.card
        self.setStyleSheet(
            "Bubble { background: %s; border: 1px solid %s; border-radius: 10px; }"
            % (bg, "#E07A7A" if err else Theme.line)
        )
        self.who.setStyleSheet(f"color: {'#C23A3A' if err else Theme.muted};")
        self.body.setStyleSheet(f"color: {Theme.text};")

    def restyle(self):
        self._apply_style()
        self.set_text(self._plain, live=self._live)

    def set_text(self, text: str, *, live: bool = False):
        self._plain = text or ""
        self._live = live
        if self.role == "user" or live:
            self.body.setTextFormat(Qt.PlainText)
            self.body.setText(self._plain)
            return
        self.body.setTextFormat(Qt.RichText)
        self.body.setText(_md(self._plain) or "…")

    def _copy(self):
        QApplication.clipboard().setText(self._plain or "")
        InfoBar.success(tr("已复制"), "", parent=self.window() or self,
                        position=InfoBarPosition.TOP, duration=1200)


class ToolLine(QFrame):
    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 6, 10, 6)
        lay.setSpacing(4)
        self.lab = CaptionLabel(text)
        self.lab.setWordWrap(True)
        self.restyle()
        self.bar = ProgressBar()
        self.bar.setRange(0, 100)
        self.bar.hide()
        lay.addWidget(self.lab)
        lay.addWidget(self.bar)
        self.task_id = ""

    def restyle(self):
        self.setStyleSheet(f"ToolLine {{ background: {Theme.hover}; border-radius: 8px; }}")
        self.lab.setStyleSheet(f"color: {Theme.green};")

    def set_text(self, text: str):
        self.lab.setText(text)

    def bind_task(self, task_id: str):
        self.task_id = task_id or ""
        if self.task_id:
            self.bar.show()

    def set_progress(self, current: int, total: int, message: str = ""):
        if total:
            self.bar.setValue(min(100, max(0, int(current * 100 / total))))
        if message:
            self.lab.setText(message.split("  |  ", 1)[0])


class ConfirmCard(QFrame):
    accepted = Signal()
    rejected = Signal()

    def __init__(self, label: str, detail: str = "", parent=None):
        super().__init__(parent)
        self.restyle()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.addWidget(BodyLabel(tr("需要你点一下确认：")))
        desc = BodyLabel(label)
        desc.setWordWrap(True)
        lay.addWidget(desc)
        if detail:
            box = PlainTextEdit()
            box.setReadOnly(True)
            box.setPlainText(detail)
            box.setFixedHeight(min(160, 40 + detail.count("\n") * 16))
            lay.addWidget(box)
        row = QHBoxLayout()
        yes = PrimaryPushButton(tr("确认执行"))
        no = PushButton(tr("取消"))
        yes.clicked.connect(self.accepted.emit)
        no.clicked.connect(self.rejected.emit)
        row.addWidget(yes)
        row.addWidget(no)
        row.addStretch(1)
        lay.addLayout(row)

    def restyle(self):
        el_bg = "#3A2E10" if Theme.dark else "#FFF8E8"
        el_border = "#8A7A3A" if Theme.dark else "#F0D48A"
        self.setStyleSheet(
            f"ConfirmCard {{ background: {el_bg}; border: 1px solid {el_border}; border-radius: 10px; }}"
        )


class AskCard(QFrame):
    submitted = Signal(object)
    cancelled = Signal()

    def __init__(self, questions: list, title: str = "", parent=None):
        super().__init__(parent)
        self.restyle()
        self._qs = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(10)
        if title:
            t = BodyLabel(title)
            t.setWordWrap(True)
            lay.addWidget(t)
        for q in questions or []:
            block = _AskBlock(q)
            self._qs.append(block)
            lay.addWidget(block)
        row = QHBoxLayout()
        ok = PrimaryPushButton(tr("确定"))
        no = PushButton(tr("跳过"))
        ok.clicked.connect(self._submit)
        no.clicked.connect(self.cancelled.emit)
        row.addWidget(ok)
        row.addWidget(no)
        row.addStretch(1)
        lay.addLayout(row)

    def restyle(self):
        bg = "#1E3A2E" if Theme.dark else "#F3F7F5"
        border = "#3A6B52" if Theme.dark else "#C9E4D6"
        self.setStyleSheet(
            f"AskCard {{ background: {bg}; border: 1px solid {border}; border-radius: 10px; }}"
        )

    def _submit(self):
        answers = {}
        for block in self._qs:
            row = block.collect()
            if row is None:
                InfoBar.warning(tr("还没选"), block.prompt, parent=self.window() or self,
                                position=InfoBarPosition.TOP, duration=2200)
                return
            answers[block.qid] = row
        self.setEnabled(False)
        self.submitted.emit({"answers": answers})


class _AskBlock(QWidget):
    def __init__(self, q: dict, parent=None):
        super().__init__(parent)
        self.qid = str(q.get("id") or "q1")
        self.prompt = str(q.get("prompt") or tr("请选择"))
        self.multi = bool(q.get("allow_multiple"))
        self._opts = list(q.get("options") or [])
        self._group = None
        self._radios = []
        self._checks = []
        self._other = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)
        hint = BodyLabel(self.prompt + (tr("（可多选）") if self.multi else ""))
        hint.setWordWrap(True)
        root.addWidget(hint)
        if self.multi:
            for o in self._opts:
                box = CheckBox(o.get("label") or o.get("id") or "")
                box.setProperty("opt_id", o.get("id"))
                if o.get("id") == "other":
                    self._other = box
                    box.stateChanged.connect(self._sync_other)
                self._checks.append(box)
                root.addWidget(box)
        else:
            self._group = QButtonGroup(self)
            self._group.setExclusive(True)
            for i, o in enumerate(self._opts):
                btn = RadioButton(o.get("label") or o.get("id") or "")
                btn.setProperty("opt_id", o.get("id"))
                if o.get("id") == "other":
                    self._other = btn
                self._group.addButton(btn, i)
                self._radios.append(btn)
                root.addWidget(btn)
            if self._group:
                self._group.buttonToggled.connect(self._sync_other)
        self.other_edit = LineEdit()
        self.other_edit.setPlaceholderText(tr("选「其他」时在这里填"))
        self.other_edit.hide()
        root.addWidget(self.other_edit)

    def _sync_other(self, *_a):
        on = False
        if self.multi and self._other:
            on = self._other.isChecked()
        elif self._other:
            on = self._other.isChecked()
        self.other_edit.setVisible(on)

    def collect(self):
        picked = []
        if self.multi:
            for box in self._checks:
                if box.isChecked():
                    picked.append({"id": box.property("opt_id"), "label": box.text()})
        else:
            btn = self._group.checkedButton() if self._group else None
            if btn:
                picked.append({"id": btn.property("opt_id"), "label": btn.text()})
        extra = self.other_edit.text().strip()
        other_on = any(p.get("id") == "other" or tr("其他") in (p.get("label") or "") for p in picked)
        if extra and not other_on:
            picked.append({"id": "other", "label": tr("其他")})
            other_on = True
        if other_on and not extra and len(picked) == 1:
            return None
        if not picked:
            return None
        return {
            "ids": [p["id"] for p in picked],
            "labels": [p["label"] for p in picked],
            "other_text": extra if other_on else "",
        }


class PermissionDialog(MessageBoxBase):
    """AI 助手权限管理：变更前确认（开关）+ 完全访问（下拉）。

    两个控件改完立即落盘，不用点额外保存；on_changed 用来让 AI 页
    同步输入框旁的状态标签和快捷下拉。
    """

    def __init__(self, backend, on_changed=None, parent=None):
        super().__init__(parent)
        self.backend = backend
        self._on_changed = on_changed
        self.viewLayout.addWidget(SubtitleLabel(tr("权限管理"), self))
        self.viewLayout.addWidget(CaptionLabel(tr("控制 AI 助手改东西前要不要先问你")))
        self.viewLayout.addSpacing(8)

        s = backend.get_settings()
        confirm_on = bool(s.get("ai_confirm_writes", True))
        mode = s.get("ai_permission_mode") or "standard"

        self.confirm_card = SettingCard(FIF.INFO, tr("变更前确认"), tr("改文件前先问我"))
        self.confirm_sw = SwitchButton(self.confirm_card)
        self.confirm_sw.setChecked(confirm_on)
        self.confirm_card.hBoxLayout.addWidget(self.confirm_sw, 0, Qt.AlignRight)
        self.confirm_card.hBoxLayout.addSpacing(16)
        self.viewLayout.addWidget(self.confirm_card)

        self.mode_card = SettingCard(FIF.FINGERPRINT, tr("完全访问"), tr("减少确认次数"))
        self.mode_box = ComboBox(self.mode_card)
        self.mode_box.addItems([tr("标准"), tr("完全访问")])
        self.mode_box.setCurrentIndex(1 if mode == "full" else 0)
        self.mode_box.setFixedWidth(150)
        self.mode_card.hBoxLayout.addWidget(self.mode_box, 0, Qt.AlignRight)
        self.mode_card.hBoxLayout.addSpacing(16)
        self.viewLayout.addWidget(self.mode_card)

        self.viewLayout.addSpacing(4)
        tip = CaptionLabel(tr(
            "关掉「变更前确认」后写操作全部直接执行；「完全访问」仍会在删除实例、"
            "删除模组、改配置前询问。"))
        tip.setWordWrap(True)
        self.viewLayout.addWidget(tip)

        self.confirm_sw.checkedChanged.connect(self._save)
        self.mode_box.currentIndexChanged.connect(self._save)

        self.yesButton.setText(tr("关闭"))
        self.cancelButton.hide()
        self.widget.setMinimumWidth(480)

    def _save(self, *_a):
        mode = "full" if self.mode_box.currentIndex() == 1 else "standard"
        try:
            self.backend.save_settings({
                "ai_confirm_writes": bool(self.confirm_sw.isChecked()),
                "ai_permission_mode": mode,
            })
        except Exception as exc:  # noqa: BLE001
            InfoBar.error(tr("保存失败"), str(exc), parent=self,
                          position=InfoBarPosition.TOP, duration=4000)
            return
        if self._on_changed:
            self._on_changed()


class ChatInput(PlainTextEdit):
    submitted = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setPlaceholderText(tr("问我要下什么、哪报错、模组怎么配…  Enter 发送，Shift+Enter 换行"))
        self.setFixedHeight(48)
        self._preedit = ""
        self.textChanged.connect(self._grow)

    def _grow(self):
        h = int(self.document().size().height()) + 20
        self.setFixedHeight(min(140, max(48, h)))

    def inputMethodEvent(self, event):
        self._preedit = event.preeditString() or ""
        super().inputMethodEvent(event)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter) and not (e.modifiers() & Qt.ShiftModifier):
            if self._preedit:
                super().keyPressEvent(e)
                return
            text = self.toPlainText().strip()
            if text:
                self.submitted.emit(text)
            e.accept()
            return
        super().keyPressEvent(e)


class AiPage(QWidget):
    def __init__(self, backend, parent=None):
        super().__init__(parent)
        self.setObjectName("aiPage")
        self.backend = backend
        self._store = chat_store.load()
        self._history = []
        self._worker = None
        self._assistant_bubble = None
        self._stream = ""
        self._queue = []
        self._tool_lines = {}
        self._task_lines = {}
        self._notes = []
        self._pending_user = None
        self._flush_timer = QTimer(self)
        self._flush_timer.setSingleShot(True)
        self._flush_timer.setInterval(33)
        self._flush_timer.timeout.connect(self._flush_stream)
        self._scroll_timer = QTimer(self)
        self._scroll_timer.setSingleShot(True)
        self._scroll_timer.setInterval(50)
        self._scroll_timer.timeout.connect(self._do_scroll)

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._side = QFrame()
        self._side.setFixedWidth(200)
        sl = QVBoxLayout(self._side)
        sl.setContentsMargins(10, 14, 10, 14)
        sl.setSpacing(8)
        new_btn = PrimaryPushButton(getattr(FIF, "ADD", FIF.PLAY), tr("新对话"))
        new_btn.setFixedHeight(32)
        new_btn.clicked.connect(self._new_chat)
        sl.addWidget(new_btn)
        self.chat_list = QListWidget()
        self.chat_list.currentItemChanged.connect(self._on_pick_chat)
        sl.addWidget(self.chat_list, 1)
        del_btn = TransparentPushButton(FIF.DELETE, tr("删除对话"))
        del_btn.clicked.connect(self._delete_chat)
        sl.addWidget(del_btn)
        root.addWidget(self._side)

        main = QVBoxLayout()
        main.setContentsMargins(24, 20, 24, 20)
        main.setSpacing(10)

        head = QHBoxLayout()
        title = SubtitleLabel(tr("AI 助手"))
        title.setFont(QFont(title.font().family(), 16, QFont.DemiBold))
        head.addWidget(title)
        self.status = CaptionLabel("")
        head.addWidget(self.status)
        head.addStretch(1)
        # 空闲时不摆一排灰按钮：停止只在回复中出现，重试只在有历史时出现
        self.stop_btn = PushButton(FIF.CLOSE, tr("停止"))
        self.stop_btn.setEnabled(False)
        self.stop_btn.setVisible(False)
        self.retry_btn = TransparentPushButton(FIF.SYNC, tr("重试"))
        self.retry_btn.setEnabled(False)
        self.retry_btn.setVisible(False)
        head.addWidget(self.stop_btn)
        head.addWidget(self.retry_btn)
        main.addLayout(head)

        # 建议话术只在空对话时展示，聊起来后让位给内容
        self._chips_host = QWidget()
        chips = QHBoxLayout(self._chips_host)
        chips.setContentsMargins(0, 0, 0, 0)
        chips.setSpacing(8)
        for t in _CHIPS:
            b = PushButton(t)
            b.setFixedHeight(28)
            b.clicked.connect(lambda *_a, s=t: self._send_text(s))
            chips.addWidget(b)
        chips.addStretch(1)
        main.addWidget(self._chips_host)

        self.scroll = ScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        host = QWidget()
        self.chat = QVBoxLayout(host)
        self.chat.setContentsMargins(0, 0, 8, 0)
        self.chat.setSpacing(10)
        self.chat.addStretch(1)
        self.scroll.setWidget(host)
        self._host = host
        main.addWidget(self.scroll, 1)

        self._input_box = QFrame()
        row = QHBoxLayout(self._input_box)
        row.setContentsMargins(10, 8, 10, 8)
        self.input = ChatInput()
        # 输入框旁的权限快捷区：下拉直接切三档，齿轮开完整说明面板
        self.perm_combo = ComboBox()
        self.perm_combo.setFixedHeight(34)
        # 最小宽而非定宽：英文 “No confirmations” 在 112px 里被省略成没意义的词头
        self.perm_combo.setMinimumWidth(112)
        self.perm_combo.setToolTip(tr("AI 权限等级"))
        self.perm_combo.addItems([tr("标准"), tr("完全访问"), tr("免确认")])
        self.perm_combo.currentIndexChanged.connect(self._on_perm_level)
        self.perm_btn = TransparentToolButton(FIF.SETTING)
        self.perm_btn.setFixedSize(34, 34)
        self.perm_btn.setToolTip(tr("点击管理 AI 权限"))
        self.perm_btn.clicked.connect(self._open_permissions)
        self.send_btn = PrimaryPushButton(getattr(FIF, "SEND", FIF.PLAY), tr("发送"))
        self.send_btn.setFixedHeight(34)
        row.addWidget(self.input, 1)
        row.addWidget(self.perm_combo)
        row.addWidget(self.perm_btn)
        row.addWidget(self.send_btn)
        main.addWidget(self._input_box)

        wrap = QWidget()
        wrap.setLayout(main)
        root.addWidget(wrap, 1)

        self.send_btn.clicked.connect(lambda: self._send_text(self.input.toPlainText()))
        self.input.submitted.connect(self._send_text)
        self.stop_btn.clicked.connect(self._stop)
        self.retry_btn.clicked.connect(self._retry)
        self._esc = QShortcut(QKeySequence(Qt.Key_Escape), self)
        self._esc.activated.connect(self._stop)
        self.backend.progress.connect(self._on_task_progress)
        self.backend.finished.connect(self._on_task_finished)

        self.restyle()
        self._reload_list()
        self._load_active()
        self._refresh_perm_ui()

    def reload(self):
        self._refresh_status()
        self._refresh_perm_ui()

    def restyle(self):
        self._side.setStyleSheet(
            f"QFrame {{ background: {Theme.card}; border-right: 1px solid {Theme.line}; }}")
        self.chat_list.setStyleSheet(
            f"QListWidget {{ border: none; background: transparent; color: {Theme.text}; }}"
            f"QListWidget::item {{ padding: 8px; border-radius: 6px; }}"
            # 不写 color 的话选中项走 Qt 默认高亮白字，浅色 hover 底上直接隐身
            f"QListWidget::item:selected {{ background: {Theme.hover}; color: {Theme.text}; }}"
        )
        self._input_box.setStyleSheet(
            f"QFrame {{ background: {Theme.card}; border: 1px solid {Theme.line}; border-radius: 10px; }}"
        )
        self.status.setStyleSheet(f"color: {Theme.muted};")
        # 已经贴在对话流里的气泡 / 工具行 / 卡片不会自己跟主题走，逐个刷一遍
        for kind in (Bubble, ToolLine, ConfirmCard, AskCard):
            for w in self._host.findChildren(kind):
                w.restyle()
        self._refresh_status()

    def _refresh_status(self):
        s = self.backend.get_settings()
        mode = s.get("ai_mode") or "public"
        if mode == "custom":
            label = f"{tr('自定义')} · {s.get('ai_model') or DEFAULT_MODEL}"
        else:
            label = f"{tr('公益接口')} · {DEFAULT_MODEL}"
        self.status.setText(label)

    def _perm_level(self) -> str:
        s = self.backend.get_settings()
        if not bool(s.get("ai_confirm_writes", True)):
            return "noconfirm"
        return "full" if (s.get("ai_permission_mode") or "standard") == "full" else "standard"

    def _refresh_perm_ui(self):
        level = self._perm_level()
        self.perm_combo.blockSignals(True)
        self.perm_combo.setCurrentIndex({"standard": 0, "full": 1, "noconfirm": 2}[level])
        self.perm_combo.blockSignals(False)

    def _on_perm_level(self, index: int):
        data = {
            0: {"ai_confirm_writes": True, "ai_permission_mode": "standard"},
            1: {"ai_confirm_writes": True, "ai_permission_mode": "full"},
            2: {"ai_confirm_writes": False},
        }.get(index)
        if not data:
            return
        try:
            self.backend.save_settings(data)
        except Exception as exc:  # noqa: BLE001
            InfoBar.error(tr("保存失败"), str(exc), parent=self.window() or self,
                          position=InfoBarPosition.TOP, duration=4000)
        self._refresh_perm_ui()

    def _open_permissions(self):
        dlg = PermissionDialog(self.backend, on_changed=self._refresh_perm_ui,
                               parent=self.window())
        dlg.exec()

    def _launch_prefs(self) -> dict:
        win = self.window()
        lp = getattr(win, "launch_page", None)
        if lp is None:
            return {}
        java = ""
        try:
            java = lp._selected_java()
        except Exception:
            java = lp.java_box.currentText() if hasattr(lp, "java_box") else ""
        return {
            "instance": lp.instance_box.currentText() if hasattr(lp, "instance_box") else "",
            "version": lp.version_box.currentText() if hasattr(lp, "version_box") else "",
            "account": lp.account_box.currentText() if hasattr(lp, "account_box") else "",
            "username": lp.username_edit.text().strip() if hasattr(lp, "username_edit") else "Player",
            "memory_mb": lp.memory_slider.value() if hasattr(lp, "memory_slider") else 0,
            "width": lp.width_spin.value() if hasattr(lp, "width_spin") else 0,
            "height": lp.height_spin.value() if hasattr(lp, "height_spin") else 0,
            "java": java,
        }

    def _reload_list(self):
        self.chat_list.blockSignals(True)
        self.chat_list.clear()
        active = self._store.get("active_id")
        pick = None
        for c in self._store.get("chats") or []:
            # 存盘里的默认标题是中文原文，展示时过一遍 tr，英文界面不漏翻
            title = c.get("title") or "对话"
            item = QListWidgetItem(tr(title) if title in ("新对话", "对话") else title)
            item.setData(Qt.UserRole, c.get("id"))
            self.chat_list.addItem(item)
            if c.get("id") == active:
                pick = item
        if pick:
            self.chat_list.setCurrentItem(pick)
        self.chat_list.blockSignals(False)

    def _wipe_messages(self):
        while self.chat.count() > 1:
            item = self.chat.takeAt(0)
            w = item.widget()
            if w:
                w.hide()
                w.deleteLater()
        self._assistant_bubble = None
        self._stream = ""
        self._tool_lines.clear()
        self._task_lines.clear()

    def _load_active(self):
        chat = chat_store.get_chat(self._store, self._store.get("active_id") or "")
        self._history = list((chat or {}).get("messages") or [])
        self._wipe_messages()
        if not self._history:
            s = self.backend.get_settings()
            welcome = _WELCOME if bool(s.get("ai_confirm_writes", True)) else _WELCOME_NOCONFIRM
            self._add_bubble("assistant", welcome)
        else:
            for m in self._history:
                role = m.get("role") or "assistant"
                if role in ("user", "assistant", "error"):
                    self._add_bubble(role, m.get("content") or "")
        self._chips_host.setVisible(not self._history)
        self.retry_btn.setVisible(bool(self._history) and not self._worker)
        self.retry_btn.setEnabled(bool(self._history) and not self._worker)
        self._scroll_bottom()

    def _persist(self):
        cid = self._store.get("active_id") or ""
        chat_store.upsert_messages(self._store, cid, self._history)
        self._reload_list()

    def _new_chat(self):
        self._stop(wait=True)
        chat_store.new_chat(self._store)
        self._load_active()
        self._reload_list()

    def _delete_chat(self):
        self._stop(wait=True)
        cid = self._store.get("active_id")
        chat_store.delete_chat(self._store, cid)
        self._load_active()
        self._reload_list()

    def _on_pick_chat(self, item, _prev=None):
        if item is None or self._worker:
            return
        cid = item.data(Qt.UserRole)
        if cid == self._store.get("active_id"):
            return
        chat_store.set_active(self._store, cid)
        self._load_active()

    def _add_widget(self, w: QWidget):
        wrap = QWidget()
        row = QHBoxLayout(wrap)
        row.setContentsMargins(0, 0, 0, 0)
        mine = isinstance(w, Bubble) and w.role == "user"
        if mine:
            row.addStretch(1)
            row.addWidget(w, 0, Qt.AlignRight)
        else:
            row.addWidget(w, 0, Qt.AlignLeft)
            row.addStretch(1)
        self.chat.insertWidget(self.chat.count() - 1, wrap)
        self._scroll_bottom()
        return wrap

    def _add_bubble(self, role: str, text: str) -> Bubble:
        b = Bubble(role, text)
        self._add_widget(b)
        return b

    def _scroll_bottom(self):
        if not self._scroll_timer.isActive():
            self._scroll_timer.start()

    def _do_scroll(self):
        bar = self.scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _flush_stream(self):
        if self._assistant_bubble:
            self._assistant_bubble.set_text(self._stream or "…", live=True)
        self._scroll_bottom()

    def _busy(self, on: bool):
        self.send_btn.setEnabled(not on)
        self.stop_btn.setEnabled(on)
        self.stop_btn.setVisible(on)
        retry_on = not on and bool(self._history)
        self.retry_btn.setEnabled(retry_on)
        self.retry_btn.setVisible(retry_on)

    def _send_text(self, text: str):
        text = (text or "").strip()
        if not text:
            return
        if self._worker:
            self._queue.append(text)
            self.input.clear()
            # 这里已经把气泡贴出去了，出队时 _send 不能再贴一次
            self._add_bubble("user", text)
            InfoBar.info(tr("已排队"), tr("这条会在当前回复结束后发出"), parent=self.window() or self,
                         position=InfoBarPosition.TOP, duration=1800)
            return
        self.input.clear()
        self._send(text)

    def _send(self, text: str, *, echo: bool = True):
        if echo:
            self._add_bubble("user", text)
        self._chips_host.setVisible(False)
        self._stream = ""
        self._notes = []
        self._tool_lines = {}
        self._task_lines = {}
        self._assistant_bubble = self._add_bubble("assistant", tr("正在想…"))
        settings = self.backend.get_settings()
        self.backend._ui_launch = self._launch_prefs()
        # 只截取最近 24 条喂给模型；完整历史留在 self._history 里，不能跟着截
        worker = AgentThread(
            self.backend, settings, chat_store.api_messages(self._history[-24:]), text, self)
        self._worker = worker
        worker.delta.connect(self._on_delta, Qt.QueuedConnection)
        worker.status.connect(self._on_status, Qt.QueuedConnection)
        worker.need_confirm.connect(self._on_confirm, Qt.QueuedConnection)
        worker.need_ask.connect(self._on_ask, Qt.QueuedConnection)
        worker.done.connect(self._on_done, Qt.QueuedConnection)
        worker.failed.connect(self._on_fail, Qt.QueuedConnection)
        worker.finished.connect(worker.deleteLater)
        self._pending_user = text
        self._busy(True)
        worker.start()

    def _retry(self):
        last = None
        for m in reversed(self._history):
            if m.get("role") == "user" and (m.get("content") or "").strip():
                last = m["content"]
                break
        if last and not self._worker:
            self._send(last)

    def _on_delta(self, piece: str):
        if not piece:
            return
        self._stream += piece
        if not self._flush_timer.isActive():
            self._flush_timer.start()

    def _on_status(self, kind: str, payload: dict):
        label = payload.get("label") or payload.get("name") or kind
        name = payload.get("name") or ""
        if kind == "think":
            if not self._stream:
                if payload.get("after_tools"):
                    tip = tr("搜完了，正在整理…")
                else:
                    tip = tr("正在想…")
                if self._assistant_bubble:
                    self._assistant_bubble.set_text(tip)
            return
        if kind == "tool":
            if name == "ask_user":
                if self._assistant_bubble and not self._stream:
                    self._assistant_bubble.set_text(tr("请在下面选一下"))
                return
            line = self._tool_lines.get(name)
            if line:
                line.set_text(tr("准备：") + label)
            else:
                line = ToolLine(tr("准备：") + label)
                self._tool_lines[name] = line
                self._add_widget(line)
            return
        line = self._tool_lines.get(name)
        if kind == "tool_run":
            if line:
                line.set_text(tr("执行中：") + label)
            else:
                line = ToolLine(tr("执行中：") + label)
                self._tool_lines[name] = line
                self._add_widget(line)
        elif kind == "tool_done":
            if line:
                line.set_text(tr("完成：") + label)
            else:
                line = ToolLine(tr("完成：") + label)
                self._tool_lines[name] = line
                self._add_widget(line)
            tid = payload.get("task_id") or ""
            if not tid:
                raw = payload.get("result") or ""
                try:
                    data = json.loads(raw) if isinstance(raw, str) and raw.startswith("{") else {}
                    tid = data.get("task_id") or ""
                except Exception:
                    tid = ""
            if tid and line:
                line.bind_task(tid)
                self._task_lines[tid] = line
            if label:
                self._notes.append(label)
        elif kind == "tool_skip":
            if line:
                line.set_text(tr("已跳过：") + label)
            else:
                self._add_widget(ToolLine(tr("已跳过：") + label))
        self._scroll_bottom()

    def _on_confirm(self, name: str, args: dict, label: str):
        detail = ""
        if name == "write_mod_config":
            detail = str((args or {}).get("content") or "")[:4000]
        elif name == "delete_instance":
            detail = tr("删掉后文件找不回来。")
        card = ConfirmCard(label, detail)
        worker = self._worker

        def yes():
            card.setEnabled(False)
            if worker:
                worker.answer_confirm(True)

        def no():
            card.setEnabled(False)
            if worker:
                worker.answer_confirm(False)

        card.accepted.connect(yes)
        card.rejected.connect(no)
        self._add_widget(card)

    def _on_ask(self, questions: list, title: str):
        card = AskCard(questions, title)
        worker = self._worker

        def ok(payload):
            if worker:
                worker.answer_ask(payload)
            try:
                labels = []
                for row in (payload or {}).get("answers", {}).values():
                    labels.extend(row.get("labels") or [])
                if labels:
                    self._notes.append(tr("已选 ") + "、".join(labels))
            except Exception:
                pass

        def skip():
            card.setEnabled(False)
            if worker:
                worker.answer_ask(None)

        card.submitted.connect(ok)
        card.cancelled.connect(skip)
        self._add_widget(card)
        if self._assistant_bubble and not self._stream:
            self._assistant_bubble.set_text(tr("请在下面选一下"))

    def _compose_assistant(self, text: str) -> str:
        body = (text or "").strip()
        if self._notes:
            extra = tr("（本轮：") + "；".join(self._notes[:8]) + "）"
            if extra not in body:
                body = (body + "\n\n" + extra).strip()
        return body

    def _finish(self, assistant_text: str, ok: bool):
        self._flush_timer.stop()
        shown = self._compose_assistant(assistant_text if ok else (assistant_text or tr("已停止")))
        if self._assistant_bubble:
            self._assistant_bubble.set_text(shown or (tr("已停止") if not ok else ""))
        user = getattr(self, "_pending_user", None)
        if user:
            self._history.append({"role": "user", "content": user})
            self._history.append({"role": "assistant" if ok else "error", "content": shown or ""})
            self._persist()
        self._pending_user = None
        self._worker = None
        self._assistant_bubble = None
        self._stream = ""
        self._busy(False)
        self.input.setFocus()
        self._scroll_bottom()
        if self._queue:
            nxt = self._queue.pop(0)
            QTimer.singleShot(30, lambda: self._send(nxt, echo=False))

    def _on_done(self, text: str):
        self._finish(text or self._stream, True)

    def _on_fail(self, msg: str):
        self._flush_timer.stop()
        text = (msg or "").strip() or tr("接口没返回具体原因")
        if self._assistant_bubble:
            self._assistant_bubble.set_text(text)
        else:
            self._add_bubble("error", text)
        if text in _STOP:
            InfoBar.info(tr("已停止"), tr("可以继续说下一句"), parent=self.window() or self,
                         position=InfoBarPosition.TOP, duration=2200)
        else:
            InfoBar.error(tr("助手出错"), text, parent=self.window() or self,
                          position=InfoBarPosition.TOP, duration=12000)
        self._finish(text, text in _STOP)

    def _stop(self, wait=False):
        if self._worker:
            self._worker.cancel()
            if wait:
                self._worker.wait(2500)

    def _on_task_progress(self, task_id, current, total, message):
        line = self._task_lines.get(task_id)
        if line:
            line.set_progress(current, total, message or "")

    def _on_task_finished(self, task_id, success, message):
        line = self._task_lines.get(task_id)
        if not line:
            return
        line.set_text((tr("完成：") if success else tr("失败：")) + (message or ""))
        if hasattr(line, "bar"):
            line.bar.setValue(100 if success else line.bar.value())
