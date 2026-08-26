# -*- coding: utf-8 -*-
"""启动页卡片内容：8 种卡片类型的正文与注册表。

单例卡片（banner/config/log/news）与页面逻辑强绑定（选择框、日志、
新闻），正文由页面缓存复用；多例卡片（快捷入口/便签/时长/任务摘要）
每次新建，数据全部存在 LayoutItem.settings 里随布局落盘。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFormLayout, QGridLayout, QHBoxLayout, QVBoxLayout, QWidget,
)
from qfluentwidgets import (
    BodyLabel, CaptionLabel, CheckBox, ComboBox, CompactSpinBox,
    FluentIcon as FIF, LineEdit, MessageBoxBase, PlainTextEdit,
    PrimaryPushButton, PushButton, Slider, StrongBodyLabel, SubtitleLabel,
    TransparentPushButton, setFont,
)

from mclauncher.config import CONFIG
from mclauncher.i18n import tr

from ..dashboard import CardSpec
from ..pcl_chrome import form_label
from ..widgets import BannerWidget

# 快捷入口卡片可选的导航目标：(key, icon)
QUICK_TARGETS: list[tuple[str, object]] = [
    ("launch", FIF.HOME),
    ("version", FIF.DOWNLOAD),
    ("mod", FIF.EMBED),
    ("modpack", FIF.FOLDER),
    ("datapack", FIF.FOLDER),
    ("resource", FIF.PHOTO),
    ("shader", FIF.BRIGHTNESS),
    ("world", FIF.GLOBE),
    ("java", FIF.CAFE),
    ("instance", FIF.FOLDER),
    ("mods", FIF.EMBED),
    ("account", FIF.PEOPLE),
    ("multiplayer", FIF.WIFI),
    ("servers", FIF.GLOBE),
    ("playtime", FIF.HISTORY),
    ("feedback", FIF.CHAT),
    ("settings", FIF.SETTING),
    ("tasks", FIF.CLOUD_DOWNLOAD),
]

_QUICK_LABELS: dict[str, str] = {
    "launch": "启动",
    "version": "原版游戏",
    "mod": "Mod",
    "modpack": "整合包",
    "datapack": "数据包",
    "resource": "资源包",
    "shader": "光影包",
    "world": "世界",
    "java": "Java",
    "instance": "实例",
    "mods": "模组",
    "account": "账号",
    "multiplayer": "联机",
    "servers": "服务器",
    "playtime": "时长",
    "feedback": "反馈",
    "settings": "设置",
    "tasks": "下载任务",
}


def quick_label(key: str) -> str:
    return tr(_QUICK_LABELS.get(key, key))


def quick_icon(key: str):
    for k, icon in QUICK_TARGETS:
        if k == key:
            return icon
    return FIF.SETTING


# ======================================================================
# 单例卡片正文（页面级逻辑，控件引用挂到 LaunchPage 上）
# ======================================================================
class BannerBody(QWidget):
    key = "banner"

    def __init__(self, page, card, item):
        super().__init__(card)
        self.page = page
        self.card = card
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        page.banner = BannerWidget(self)
        page.launch_btn = PrimaryPushButton(FIF.PLAY, tr("启动游戏"))
        page.launch_btn.setFixedSize(170, 46)
        setFont(page.launch_btn, 15, QFont.DemiBold)
        page.stop_btn = PushButton(FIF.CLOSE, tr("停止"))
        page.stop_btn.setFixedSize(170, 30)
        page.stop_btn.setEnabled(False)
        # 渐进披露：空闲时不摆一个灰色大按钮和一条 0% 进度条
        # （压扁的空进度条叠在渐变底上看着就是一条脏线）。
        # 启动开始时由 LaunchPage 显示、结束后再收起。
        page.stop_btn.setVisible(False)
        # 多开管理入口：有游戏在跑时显示「运行中 ×N」，点开列表可结束指定游戏
        page.running_btn = TransparentPushButton(FIF.GAME, tr("运行中"))
        page.running_btn.setFixedSize(170, 26)
        page.running_btn.hide()
        page.running_btn.clicked.connect(page._show_running_games)
        page.banner.right_area.addStretch(1)
        page.banner.right_area.addWidget(page.launch_btn, 0, Qt.AlignRight)
        page.banner.right_area.addWidget(page.stop_btn, 0, Qt.AlignRight)
        page.banner.right_area.addWidget(page.running_btn, 0, Qt.AlignRight)

        from ..motion import SmoothProgressBar
        page.progress = SmoothProgressBar(self)
        page.progress.setRange(0, 100)
        page.progress.setValue(0)
        page.progress.setVisible(False)
        page.status_label = CaptionLabel(tr("就绪"))

        root.addWidget(page.banner, 1)
        root.addWidget(page.progress)
        root.addWidget(page.status_label)
        # 卡片空间不足时允许横幅收缩（180 → 120），给进度条和状态行
        # 留出自然高度——否则二者被布局压成 4px/0px，压扁的进度条
        # 叠在渐变上就是一条"黑线"。
        page.banner.setMinimumHeight(120)

    def attach(self, card, item):
        self.card = card


class ConfigBody(QWidget):
    key = "config"

    def __init__(self, page, card, item):
        super().__init__(card)
        self.page = page
        self.card = card
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        host = QWidget(self)
        cfg = QFormLayout(host)
        cfg.setContentsMargins(8, 8, 8, 8)
        cfg.setVerticalSpacing(10)
        cfg.setHorizontalSpacing(8)
        cfg.setLabelAlignment(Qt.AlignLeft)
        # 放不下的行把标签折到字段上方，而不是冒出横向滚动条
        cfg.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        cfg.addRow(StrongBodyLabel(tr("启动配置")))

        page.instance_box = ComboBox()
        page.version_box = ComboBox()
        page.account_box = ComboBox()
        page.java_box = ComboBox()
        page.username_edit = LineEdit()
        page.username_edit.setPlaceholderText(tr("离线用户名"))
        page.username_edit.setText("Player")

        page.memory_slider = Slider(Qt.Horizontal)
        page.memory_slider.setRange(512, 32768)
        page.memory_slider.setSingleStep(256)
        page.memory_slider.setValue(int(CONFIG.get("memory_mb", 4096)))
        page.memory_label = CaptionLabel(f"{page.memory_slider.value()} MB")
        page.memory_slider.valueChanged.connect(page._on_memory_changed)
        from qfluentwidgets import CheckBox
        page.memory_auto_check = CheckBox(tr("自动"))
        page.memory_auto_check.setChecked(bool(CONFIG.get("memory_auto", True)))
        page.memory_auto_check.setToolTip(tr("按启动时的系统可用内存自动分配"))
        page.memory_auto_check.toggled.connect(page._on_memory_auto_toggled)
        mem_row = QHBoxLayout()
        mem_row.addWidget(page.memory_auto_check)
        mem_row.addWidget(page.memory_slider, 1)
        mem_row.addWidget(page.memory_label)
        page._apply_memory_auto_ui()

        # CompactSpinBox（82px）替代 SpinBox（136px）：默认布局下配置卡
        # 只有 ~350px 宽，两个大号 SpinBox 一排直接把卡片撑出横向滚动条，
        # 分辨率行、服务器输入框全被裁掉。
        page.width_spin = CompactSpinBox()
        page.width_spin.setRange(320, 7680)
        page.width_spin.setValue(int(CONFIG.get("width", 854)))
        page.height_spin = CompactSpinBox()
        page.height_spin.setRange(240, 4320)
        page.height_spin.setValue(int(CONFIG.get("height", 480)))
        res_row = QHBoxLayout()
        res_row.addWidget(page.width_spin)
        res_row.addWidget(BodyLabel("×"))
        res_row.addWidget(page.height_spin)
        res_row.addStretch(1)
        page.width_spin.valueChanged.connect(page._persist_launch_defaults)
        page.height_spin.valueChanged.connect(page._persist_launch_defaults)

        cfg.addRow(form_label(tr("实例")), page.instance_box)
        cfg.addRow(form_label(tr("版本")), page.version_box)
        cfg.addRow(form_label(tr("账号")), page.account_box)
        cfg.addRow(form_label(tr("用户名")), page.username_edit)
        cfg.addRow(form_label(tr("Java（本实例）")), page.java_box)
        cfg.addRow(form_label(tr("内存")), mem_row)
        cfg.addRow(form_label(tr("分辨率")), res_row)
        page.server_edit = LineEdit()
        page.server_edit.setPlaceholderText(tr("直连服务器 host 或 host:port"))
        cfg.addRow(form_label(tr("服务器")), page.server_edit)
        # 动作行通栏靠左（原来塞在表单字段列里，看起来像悬在半空的
        # 居中按钮）。「刷新新闻」搬去了新闻卡标题栏——它管的是新闻卡，
        # 不是启动配置。
        setup_btn = TransparentPushButton(FIF.SETTING, tr("此版本设置…"))
        setup_btn.clicked.connect(page._version_setup)
        setup_row = QHBoxLayout()
        setup_row.addWidget(setup_btn)
        setup_row.addStretch(1)
        cfg.addRow(setup_row)
        ms_btn = TransparentPushButton(FIF.PEOPLE, tr("使用微软账户登录…"))
        ms_btn.clicked.connect(page._login)
        ms_row = QHBoxLayout()
        ms_row.addWidget(ms_btn)
        ms_row.addStretch(1)
        cfg.addRow(ms_row)

        root.addWidget(host)
        root.addStretch(1)

    def attach(self, card, item):
        self.card = card


class LogBody(QWidget):
    key = "log"

    def __init__(self, page, card, item):
        super().__init__(card)
        self.page = page
        self.card = card
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)
        page.log_edit = PlainTextEdit(self)
        page.log_edit.setReadOnly(True)
        page.log_edit.setMaximumBlockCount(5000)
        page.log_edit.setPlaceholderText(tr("启动日志将输出到这里…"))
        cmd_row = QHBoxLayout()
        cmd_row.setSpacing(8)
        page.cmd_btn = PushButton(tr("复制启动命令"), self)
        page.cmd_btn.clicked.connect(page._copy_cmd)
        cmd_row.addStretch(1)
        cmd_row.addWidget(page.cmd_btn)
        root.addWidget(page.log_edit, 1)
        root.addLayout(cmd_row)

    def attach(self, card, item):
        self.card = card


class NewsBody(QWidget):
    key = "news"

    def __init__(self, page, card, item):
        super().__init__(card)
        self.page = page
        self.card = card
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        page.news_host = QVBoxLayout()
        root.addLayout(page.news_host)
        page.news_body = self

    def set_title(self, text: str):
        if self.card is not None:
            self.card.set_title(text)

    def attach(self, card, item):
        self.card = card
        page = self.page
        if page is not None:
            page.news_body = self


# ======================================================================
# 多例卡片正文
# ======================================================================
class QuickBody(QWidget):
    key = "quick"

    def __init__(self, page, card, item):
        super().__init__(card)
        self.page = page
        self.item = item
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(2, 2, 2, 2)
        self.grid.setSpacing(6)
        self._rebuild()

    def _targets(self) -> list[str]:
        targets = self.item.settings.get("targets")
        if not isinstance(targets, list) or not targets:
            return ["version", "mod", "modpack", "mods", "instance",
                    "account", "settings", "tasks"]
        return [t for t in targets if any(k == t for k, _ in QUICK_TARGETS)]

    def _rebuild(self):
        while self.grid.count():
            it = self.grid.takeAt(0)
            w = it.widget()
            if w is not None:
                w.deleteLater()
        targets = self._targets()
        cols = 4 if len(targets) > 6 else 3
        for i, key in enumerate(targets):
            btn = PushButton(quick_icon(key), quick_label(key), self)
            btn.clicked.connect(lambda _=False, k=key: self.page.nav_to(k))
            self.grid.addWidget(btn, i // cols, i % cols)

    def apply_targets(self, targets: list[str]):
        self.item.settings["targets"] = list(targets)
        self._rebuild()


class QuickSettingsDialog(MessageBoxBase):
    def __init__(self, body: QuickBody, parent=None):
        super().__init__(parent)
        self.body = body
        self.viewLayout.addWidget(SubtitleLabel(tr("选择快捷入口"), self))
        hint = BodyLabel(tr("勾选要显示在卡片上的入口："), self)
        hint.setWordWrap(True)
        self.viewLayout.addWidget(hint)
        self._boxes: list[tuple[CheckBox, str]] = []
        current = set(body._targets())
        for key, _icon in QUICK_TARGETS:
            cb = CheckBox(quick_label(key), self)
            cb.setChecked(key in current)
            self.viewLayout.addWidget(cb)
            self._boxes.append((cb, key))
        self.yesButton.setText(tr("确定"))
        self.cancelButton.setText(tr("取消"))
        self.widget.setMinimumWidth(360)

    def accept(self):
        picked = [key for cb, key in self._boxes if cb.isChecked()]
        if picked:
            self.body.apply_targets(picked)
            self.body.page.persist_layout_soon()
        super().accept()


class NotesBody(QWidget):
    key = "notes"

    def __init__(self, page, card, item):
        super().__init__(card)
        self.page = page
        self.item = item
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.edit = PlainTextEdit(self)
        self.edit.setPlaceholderText(tr("写点什么，自动保存"))
        self.edit.setPlainText(str(item.settings.get("text") or ""))
        root.addWidget(self.edit)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(600)
        self._timer.timeout.connect(self._save)
        self.edit.textChanged.connect(self._timer.start)

    def _save(self):
        self.item.settings["text"] = self.edit.toPlainText()
        self.page.persist_layout_soon()


class PlaytimeBody(QWidget):
    key = "playtime"

    def __init__(self, page, card, item):
        super().__init__(card)
        self.page = page
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(8)
        self.total = StrongBodyLabel(tr("总时长") + "：—", self)
        self.detail = CaptionLabel("", self)
        self.detail.setWordWrap(True)
        root.addWidget(self.total)
        root.addWidget(self.detail)
        root.addStretch(1)
        self.refresh()

    def refresh(self):
        try:
            total = int(self.page.backend.get_total_playtime() or 0)
            all_pt = self.page.backend.get_all_playtime() or {}
        except Exception:
            return
        from ..widgets import fmt_duration
        self.total.setText(tr("总时长") + "：" + fmt_duration(total))
        rows = sorted(
            ((name, info.get("total", 0)) for name, info in all_pt.items()),
            key=lambda r: r[1], reverse=True)[:5]
        self.detail.setText("\n".join(f"{name}  {fmt_duration(sec)}"
                                      for name, sec in rows if sec > 0))


class TasksBody(QWidget):
    key = "tasks"

    def __init__(self, page, card, item):
        super().__init__(card)
        self.page = page
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(8)
        self.count = StrongBodyLabel(tr("下载任务") + "：0", self)
        self.last = CaptionLabel(tr("暂无任务"), self)
        self.last.setWordWrap(True)
        root.addWidget(self.count)
        root.addWidget(self.last)
        root.addStretch(1)
        be = page.backend
        be.task_count_changed.connect(self._on_count)
        be.finished.connect(self._on_finished)
        self._on_count(getattr(be, "_download_task_count", lambda: 0)())

    def _on_count(self, n: int):
        self.count.setText(tr("下载任务") + f"：{n}")
        if n == 0:
            self.last.setText(tr("暂无任务"))

    def _on_finished(self, _tid, success, message):
        self.last.setText(("✓ " if success else "✗ ") + str(message or "")[:120])


# ======================================================================
# 注册表
# ======================================================================
def _keep(_body, _item):
    pass


def _drop(body, _item):
    body.setParent(None)
    body.deleteLater()


def build_registry(page) -> dict[str, CardSpec]:
    cache = page._body_cache

    def single_maker(BodyCls):
        def maker(card, item):
            body = cache.get(BodyCls.key)
            if body is None:
                body = BodyCls(page, card, item)
                cache[BodyCls.key] = body
            else:
                body.attach(card, item)
            return body
        return maker

    def quick_settings(canvas, card, item):
        body = card.body
        if isinstance(body, QuickBody):
            QuickSettingsDialog(body, canvas.window()).exec()

    return {
        "banner": CardSpec(
            "banner", lambda: tr("启动横幅"), FIF.HOME,
            lambda: tr("启动横幅 — 大标题与启动/停止按钮、进度条"),
            single_maker(BannerBody), single=True, chrome=False,
            on_removed=_keep),
        "config": CardSpec(
            "config", lambda: tr("启动配置"), FIF.SETTING,
            lambda: tr("启动配置 — 实例/版本/账号/内存等表单"),
            single_maker(ConfigBody), single=True, chrome=False,
            on_removed=_keep),
        "log": CardSpec(
            "log", lambda: tr("实时日志"), FIF.DOCUMENT,
            lambda: tr("实时日志 — 游戏输出与启动命令"),
            single_maker(LogBody), single=True,
            on_removed=_keep),
        "news": CardSpec(
            "news", lambda: tr("主页"), FIF.SYNC,
            lambda: tr("主页 — Minecraft 新闻 / 自定义主页"),
            single_maker(NewsBody), single=True,
            on_removed=_keep,
            header_action=(FIF.SYNC, tr("刷新新闻"), page._load_news)),
        "quick": CardSpec(
            "quick", lambda: tr("快捷入口"), FIF.TILES,
            lambda: tr("快捷入口 — 一键跳转到常用页面，可配置显示哪些"),
            lambda card, item: QuickBody(page, card, item),
            on_settings=quick_settings, on_removed=_drop),
        "notes": CardSpec(
            "notes", lambda: tr("便签"), FIF.EDIT,
            lambda: tr("便签 — 自动保存的随手记"),
            lambda card, item: NotesBody(page, card, item),
            on_removed=_drop),
        "playtime": CardSpec(
            "playtime", lambda: tr("游戏时长"), FIF.HISTORY,
            lambda: tr("游戏时长 — 总量与各实例排行"),
            lambda card, item: PlaytimeBody(page, card, item),
            on_removed=_drop),
        "tasks": CardSpec(
            "tasks", lambda: tr("任务摘要"), FIF.CLOUD_DOWNLOAD,
            lambda: tr("任务摘要 — 下载任务数量与最近结果"),
            lambda card, item: TasksBody(page, card, item),
            on_removed=_drop),
    }
