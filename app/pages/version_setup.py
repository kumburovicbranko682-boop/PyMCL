# -*- coding: utf-8 -*-
"""单版本设置：隔离 / JVM / 登录 / Nide8 / 窗口。"""
from qfluentwidgets import (
    BodyLabel, CaptionLabel, CheckBox, ComboBox, LineEdit, MessageBoxBase, SubtitleLabel, TextEdit,
)
from PySide6.QtWidgets import QFormLayout, QWidget

from mclauncher.gc import LABELS as GC_LABELS
from mclauncher.version_settings import (
    FULLSCREEN_MODES, ISOLATION_HINTS, ISOLATION_LABELS, PRIORITY_LABELS,
)
from mclauncher.i18n import tr
from ..pcl_chrome import form_label, paint_theme_surfaces


class VersionSetupDialog(MessageBoxBase):
    def __init__(self, backend, instance: str, version: str, parent=None):
        super().__init__(parent)
        self.backend = backend
        self.instance = instance
        self.version = version
        self._java_opts = []
        self.viewLayout.addWidget(SubtitleLabel(f"版本设置 · {version}", self))
        hint = BodyLabel(tr("这些选项只作用于当前版本，对齐 PCL 的「版本设置」。"), self)
        hint.setWordWrap(True)
        self.viewLayout.addWidget(hint)

        data = backend.get_version_settings(instance, version)
        form_host = QWidget(self)
        form = QFormLayout(form_host)
        form.setContentsMargins(0, 8, 0, 0)

        self.iso = ComboBox()
        self.iso.addItems(list(ISOLATION_LABELS.values()))
        cur = ISOLATION_LABELS.get(data.get("isolation") or "none", ISOLATION_LABELS["none"])
        self.iso.setCurrentText(cur)
        # 与首次向导/设置页共用一份档位解释，随选项联动
        self.iso_hint = CaptionLabel("", self)
        self.iso_hint.setWordWrap(True)
        self._iso_inv = {v: k for k, v in ISOLATION_LABELS.items()}
        self.iso.currentTextChanged.connect(self._update_iso_hint)
        self._update_iso_hint()

        self.memory = LineEdit()
        self.memory.setPlaceholderText(tr("留空则用启动页滑条"))
        if data.get("memory_mb"):
            self.memory.setText(str(data["memory_mb"]))

        self.java = ComboBox()
        self._java_opts = backend.java_combo_options(instance, False) or []
        labels = [o["label"] for o in self._java_opts] or [tr("自动选择")]
        self.java.addItems(labels)
        want = data.get("java") or tr("自动选择")
        picked = ""
        for o in self._java_opts:
            if o.get("value") == want or o.get("label") == want:
                picked = o["label"]
                break
        if picked:
            self.java.setCurrentText(picked)
        elif want and want not in labels:
            self.java.addItem(want)
            self.java.setCurrentText(want)
        call_async = getattr(backend, "call_async", None)
        if callable(call_async):
            call_async(
                lambda: backend.java_combo_options(instance, True),
                self._fill_java,
                lambda *_: None,
            )

        self.jvm = TextEdit()
        self.jvm.setPlaceholderText(tr("-XX:+UseG1GC 等，一行或空格分隔"))
        self.jvm.setFixedHeight(64)
        self.jvm.setPlainText(data.get("jvm_args") or "")

        self.gc = ComboBox()
        self.gc.addItems([tr("跟随全局")] + list(GC_LABELS.values()))
        gck = data.get("gc") or ""
        if gck and gck in GC_LABELS:
            self.gc.setCurrentText(GC_LABELS[gck])

        self.game = LineEdit()
        self.game.setPlaceholderText(tr("附加游戏参数"))
        self.game.setText(data.get("game_args") or "")

        self.login = ComboBox()
        self.login.addItem(tr("跟随启动页"))
        accounts = backend.get_accounts() or []
        self.login.addItems(accounts)
        if data.get("login_account") and data["login_account"] in accounts:
            self.login.setCurrentText(data["login_account"])

        self.nide8 = LineEdit()
        self.nide8.setPlaceholderText(tr("32 位服务器 ID 或通行证链接"))
        self.nide8.setText(data.get("nide8_id") or "")
        self.auth_server = LineEdit()
        self.auth_server.setPlaceholderText(tr("自定义认证服 API（可选）"))
        self.auth_server.setText(data.get("auth_server") or "")

        self.server = LineEdit()
        self.server.setPlaceholderText(tr("启动后直连，例如 play.example.com"))
        self.server.setText(data.get("server") or "")
        self.port = LineEdit()
        self.port.setPlaceholderText("25565")
        self.port.setText(str(data.get("port") or ""))

        self.title = LineEdit()
        self.title.setPlaceholderText(tr("自定义窗口标题"))
        self.title.setText(data.get("window_title") or "")
        self.win_mode = ComboBox()
        self.win_mode.addItems([tr("窗口"), tr("全屏")])
        self.win_mode.setCurrentText(
            tr("全屏") if data.get("window_mode") in FULLSCREEN_MODES else tr("窗口"))
        self.win_w = LineEdit()
        self.win_w.setPlaceholderText(tr("留空则用设置页的全局分辨率"))
        self.win_w.setText(str(data.get("window_width") or ""))
        self.win_h = LineEdit()
        self.win_h.setPlaceholderText(tr("留空则用设置页的全局分辨率"))
        self.win_h.setText(str(data.get("window_height") or ""))

        self.skin = ComboBox()
        self.skin.addItems([tr("默认"), "Steve", "Alex"])
        skin_map = {"steve": "Steve", "alex": "Alex"}
        self.skin.setCurrentText(skin_map.get(data.get("offline_skin") or "default", tr("默认")))

        self.pre = LineEdit()
        self.pre.setPlaceholderText(tr("启动前命令（cmd / 脚本）"))
        self.pre.setText(data.get("pre_launch") or "")
        self.wait = CheckBox(tr("等待启动前命令结束"))
        self.wait.setChecked(bool(data.get("pre_launch_wait", True)))
        self.post = LineEdit()
        self.post.setPlaceholderText(tr("退出后命令"))
        self.post.setText(data.get("post_launch") or "")

        # 整个对话框都是人话，唯独这里曾直接摆出内部 token（low/normal/high）
        self.priority = ComboBox()
        self.priority.addItems([tr(v) for v in PRIORITY_LABELS.values()])
        cur_pri = data.get("process_priority") or "normal"
        self.priority.setCurrentText(tr(PRIORITY_LABELS.get(cur_pri, PRIORITY_LABELS["normal"])))

        form.addRow(form_label(tr("隔离")), self.iso)
        form.addRow("", self.iso_hint)
        form.addRow(form_label(tr("内存 MB")), self.memory)
        form.addRow(form_label("Java"), self.java)
        form.addRow(form_label("GC"), self.gc)
        form.addRow(form_label(tr("JVM 参数")), self.jvm)
        form.addRow(form_label(tr("游戏参数")), self.game)
        form.addRow(form_label(tr("绑定账号")), self.login)
        form.addRow(form_label(tr("统一通行证")), self.nide8)
        form.addRow(form_label(tr("认证服")), self.auth_server)
        form.addRow(form_label(tr("服务器")), self.server)
        form.addRow(form_label(tr("端口")), self.port)
        form.addRow(form_label(tr("窗口标题")), self.title)
        form.addRow(form_label(tr("窗口模式")), self.win_mode)
        form.addRow(form_label(tr("窗口宽度")), self.win_w)
        form.addRow(form_label(tr("窗口高度")), self.win_h)
        form.addRow(form_label(tr("离线皮肤")), self.skin)
        form.addRow(form_label(tr("启动前")), self.pre)
        form.addRow("", self.wait)
        form.addRow(form_label(tr("退出后")), self.post)
        form.addRow(form_label(tr("优先级")), self.priority)
        self.viewLayout.addWidget(form_host)
        self.yesButton.setText(tr("保存"))
        self.cancelButton.setText(tr("取消"))
        self.widget.setMinimumWidth(540)
        # 对话框里保持实底，不透出主窗背景图
        paint_theme_surfaces(form_host, allow_transparent=False)

    def _update_iso_hint(self, *_a):
        key = self._iso_inv.get(self.iso.currentText(), "none")
        self.iso_hint.setText(tr(ISOLATION_HINTS.get(key, "")))

    def _fill_java(self, opts):
        self._java_opts = opts or []
        cur = self.java.currentText()
        labels = [o["label"] for o in self._java_opts] or [tr("自动选择")]
        self.java.blockSignals(True)
        self.java.clear()
        self.java.addItems(labels)
        if cur in labels:
            self.java.setCurrentText(cur)
        self.java.blockSignals(False)

    def payload(self) -> dict:
        inv = {v: k for k, v in ISOLATION_LABELS.items()}
        gc_inv = {v: k for k, v in GC_LABELS.items()}
        mem = self.memory.text().strip()
        java = self.java.currentText().strip() or tr("自动选择")
        for o in self._java_opts:
            if o.get("label") == java:
                java = o.get("value") or java
                break
        login = self.login.currentText()
        if login == tr("跟随启动页"):
            login = ""
        skin = {"Steve": "steve", "Alex": "alex"}.get(self.skin.currentText(), "default")

        def size_of(edit):
            text = edit.text().strip()
            return int(text) if text.isdigit() and int(text) > 0 else None

        return {
            "isolation": inv.get(self.iso.currentText(), "none"),
            "memory_mb": int(mem) if mem.isdigit() else None,
            "java": java,
            "jvm_args": self.jvm.toPlainText().strip(),
            "game_args": self.game.text().strip(),
            "server": self.server.text().strip(),
            "port": self.port.text().strip(),
            "pre_launch": self.pre.text().strip(),
            "post_launch": self.post.text().strip(),
            "pre_launch_wait": self.wait.isChecked(),
            "process_priority": {tr(v): k for k, v in PRIORITY_LABELS.items()}.get(
                self.priority.currentText(), "normal"),
            "login_account": login,
            "nide8_id": self.nide8.text().strip(),
            "auth_server": self.auth_server.text().strip(),
            "gc": gc_inv.get(self.gc.currentText(), ""),
            "window_title": self.title.text().strip(),
            "window_mode": "maximize" if self.win_mode.currentText() == tr("全屏") else "window",
            "window_width": size_of(self.win_w),
            "window_height": size_of(self.win_h),
            "offline_skin": skin,
        }

    def save(self) -> dict:
        return self.backend.save_version_settings(self.instance, self.version, self.payload())
