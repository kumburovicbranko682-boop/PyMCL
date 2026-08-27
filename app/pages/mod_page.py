# -*- coding: utf-8 -*-
"""模组管理页：查看已安装模组、启用/禁用、删除、导入、检查更新。

侧边栏一级入口。目录选择与安装目标一致：实例共享 mods + 开了版本隔离的版本。
"""

import html
from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, Qt, QTimer
from PySide6.QtGui import QGuiApplication, QPixmap
from PySide6.QtWidgets import (
    QFileDialog, QFrame, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget,
)
from qfluentwidgets import (
    Action, CaptionLabel, CheckBox, ComboBox, FluentIcon as FIF, InfoBar, InfoBarPosition,
    LineEdit, MessageBox, MessageBoxBase, PushButton, RoundMenu, SubtitleLabel, SwitchButton,
    TransparentPushButton, TransparentTogglePushButton, TransparentToolButton,
)

from mclauncher.config import CONFIG
from mclauncher.i18n import tr
from ..pcl_chrome import Theme, ghost_btn_qss, row_qss
from ..ui_alive import guard
from ..widgets import EmptyState, IconTile, Pill
from .catalog_page import PclCard


def _fmt_size(n) -> str:
    try:
        n = float(n or 0)
    except (TypeError, ValueError):
        return "—"
    if n >= 1024 ** 3:
        return f"{n / 1024 ** 3:.1f} GB"
    if n >= 1024 ** 2:
        return f"{n / 1024 ** 2:.1f} MB"
    if n >= 1024:
        return f"{n / 1024:.0f} KB"
    return f"{int(n)} B"


class _ModRow(QFrame):
    """单个已安装模组：图标 + 模组名/版本/描述 + 启用开关 + 删除。

    元数据（get_mod_details）还没回来时 entry 只有文件名，先按文件名显示，
    异步补齐后整个列表重填。
    """

    def __init__(self, entry: dict, page):
        super().__init__(page)
        self.entry = entry
        self._page = page
        self.setObjectName("modMgrRow")
        self.setStyleSheet(row_qss("modMgrRow"))
        self.setFixedHeight(60)
        filename = entry.get("filename") or "?"
        display = str(entry.get("name") or "").strip() or filename
        version = str(entry.get("version") or "").strip()
        loader = str(entry.get("loader") or "").strip()
        desc = str(entry.get("description") or "").strip()

        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(12)
        # 批量管理模式（PCL2 同款）：行首出勾选框
        if getattr(page, "_batch_mode", False):
            cb = CheckBox()
            cb.setChecked(filename in page._selected)
            cb.toggled.connect(lambda on, n=filename: page._on_row_check(n, on))
            lay.addWidget(cb)
        lay.addWidget(self._icon(entry.get("icon"), display))

        info = QVBoxLayout()
        info.setSpacing(1)
        title = QLabel(display)
        title.setStyleSheet(
            f"color: {Theme.title}; font-size: 13px; font-weight: 600; background: transparent;")
        info.addWidget(title)
        bits = [version, _fmt_size(entry.get("bytes"))]
        if display != filename:
            bits.append(filename)
        if not entry.get("enabled"):
            bits.append(tr("已禁用"))
        meta = CaptionLabel("  ·  ".join(b for b in bits if b))
        meta.setStyleSheet(f"color: {Theme.muted}; font-size: 11px; background: transparent;")
        info.addWidget(meta)
        lay.addLayout(info, 1)

        tip_head = html.escape(display) + (f" {html.escape(version)}" if version else "")
        tip_bits = [f"<b>{tip_head}</b>"]
        sub = " · ".join(x for x in (
            loader if loader not in ("", "unknown") else "",
            ", ".join(entry.get("authors") or [])) if x)
        if sub:
            tip_bits.append(html.escape(sub))
        if desc:
            tip_bits.append(html.escape(desc))
        tip_bits.append(html.escape(filename))
        self.setToolTip("<p style='white-space:normal; max-width: 420px;'>"
                        + "<br/>".join(tip_bits) + "</p>")

        self.switch = SwitchButton()
        self.switch.setChecked(bool(entry.get("enabled")))
        self.switch.setOnText(tr("启用"))
        self.switch.setOffText(tr("禁用"))
        self.switch.checkedChanged.connect(lambda on, n=filename: page._toggle(n, on, self))
        lay.addWidget(self.switch)

        btn = TransparentToolButton(FIF.DELETE)
        btn.setToolTip(tr("删除"))
        btn.clicked.connect(lambda _, n=filename: page._delete(n))
        lay.addWidget(btn)

    def contextMenuEvent(self, event):
        name = self.entry.get("filename") or "?"
        menu = RoundMenu(parent=self)
        ask_mod = Action(getattr(FIF, "CHAT", None) or FIF.HELP, tr("问 AI 这个模组"))
        ask_mod.triggered.connect(lambda: self._page._ask_ai_mod(self.entry))
        scan = Action(FIF.SEARCH, tr("让 AI 扫描模组冲突"))
        scan.triggered.connect(lambda: self._page._ask_ai_conflicts(name))
        menu.addAction(ask_mod)
        menu.addAction(scan)
        menu.exec(event.globalPos())

    @staticmethod
    def _icon(icon_path, display: str) -> QWidget:
        """jar 里带图标就显示真图标，否则回退首字母磁贴。"""
        if icon_path and Path(icon_path).is_file():
            pm = QPixmap(str(icon_path))
            if not pm.isNull():
                lab = QLabel()
                lab.setFixedSize(40, 40)
                lab.setAlignment(Qt.AlignCenter)
                lab.setStyleSheet("background: transparent;")
                lab.setPixmap(pm.scaled(40, 40, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                return lab
        return IconTile(display, size=40)


class ConflictReportDialog(MessageBoxBase):
    """模组冲突扫描结果：重复安装 / 加载器不匹配 / 缺依赖 / 互不兼容。"""

    def __init__(self, report: dict, parent=None):
        super().__init__(parent)
        report = report or {}
        self._type_labels = {
            "duplicate_id": tr("重复安装"),
            "loader_mismatch": tr("加载器不匹配"),
            "missing_dep": tr("缺少依赖"),
            "breaks": tr("互不兼容"),
        }
        self.viewLayout.addWidget(SubtitleLabel(tr("模组冲突扫描"), self))
        head = CaptionLabel(
            tr("实例 {inst} · {loader} {mc} · 共 {n} 个模组（启用 {on}）").format(
                inst=report.get("instance") or "?",
                loader=report.get("loader") or tr("未知加载器"),
                mc=report.get("mc_version") or "",
                n=report.get("mod_count") or 0,
                on=report.get("enabled") or 0), self)
        head.setWordWrap(True)
        self.viewLayout.addWidget(head)

        issues = list(report.get("issues") or [])
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        host = QWidget(self)
        lay = QVBoxLayout(host)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        for issue in issues:
            lay.addWidget(self._issue_row(issue))
        lay.addStretch(1)
        scroll.setWidget(host)
        scroll.setMinimumHeight(min(320, max(90, 64 * len(issues))))
        self.viewLayout.addWidget(scroll)

        self.yesButton.setText(tr("关闭"))
        self.cancelButton.hide()
        self.widget.setMinimumWidth(560)

    def _issue_row(self, issue: dict) -> QWidget:
        row = QFrame(self)
        row.setObjectName("conflictRow")
        row.setStyleSheet(row_qss("conflictRow"))
        lay = QHBoxLayout(row)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(10)
        label = self._type_labels.get(issue.get("type") or "", issue.get("type") or "?")
        lay.addWidget(Pill(label, "#D65C5C"))
        box = QVBoxLayout()
        box.setSpacing(1)
        msg = QLabel(str(issue.get("message") or ""))
        msg.setWordWrap(True)
        msg.setStyleSheet(f"color: {Theme.title}; font-size: 13px; background: transparent;")
        box.addWidget(msg)
        files = issue.get("files") or ([issue.get("file")] if issue.get("file") else [])
        bits = [", ".join(str(f) for f in files if f)]
        if issue.get("need"):
            bits.append(tr("需要: {dep}").format(dep=issue["need"]))
        if issue.get("other"):
            bits.append(tr("冲突对象: {mod}").format(mod=issue["other"]))
        detail = " · ".join(b for b in bits if b)
        if detail:
            cap = CaptionLabel(detail)
            cap.setStyleSheet(f"color: {Theme.muted}; font-size: 11px; background: transparent;")
            cap.setWordWrap(True)
            box.addWidget(cap)
        lay.addLayout(box, 1)
        return row


class ModUpdateDialog(MessageBoxBase):
    """模组更新列表（PCL2 同款）：勾选要更新的项，可「忽略此版本 / 不再提醒」。

    检查与批量更新都在后台线程跑，对话框保持可响应；
    已忽略的项默认不勾选、带「已忽略」标记，可一键取消忽略。
    """

    def __init__(self, page, instance: str):
        super().__init__(page.window())
        self.page = page
        self.backend = page.backend
        self.instance = instance
        self._rows: list[dict] = []
        self._checks: list = []           # [(CheckBox, row)]
        self._busy = False

        self.viewLayout.addWidget(SubtitleLabel(tr("模组更新"), self))
        self.head = CaptionLabel(tr("正在检查更新…"), self)
        self.head.setWordWrap(True)
        self.head.setStyleSheet(f"color: {Theme.muted}; background: transparent;")
        self.viewLayout.addWidget(self.head)

        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        host = QWidget(self)
        self.list_lay = QVBoxLayout(host)
        self.list_lay.setContentsMargins(0, 0, 0, 0)
        self.list_lay.setSpacing(6)
        self.scroll.setWidget(host)
        self.scroll.setMinimumHeight(180)
        self.viewLayout.addWidget(self.scroll)

        self.yesButton.setText(tr("更新所选"))
        self.yesButton.setEnabled(False)
        self.cancelButton.setText(tr("关闭"))
        self.widget.setMinimumWidth(640)

        self.backend.call_async(
            lambda: self.backend.check_mod_updates(self.instance, True),
            guard(self, self._filled), guard(self, self._failed))

    # ------------------------------------------------------------------
    def _filled(self, rows):
        self._rows = list(rows or [])
        self._refill()

    def _failed(self, err):
        self.head.setText(tr("检查更新失败：{err}").format(err=err))

    def _refill(self):
        self._checks = []
        while self.list_lay.count():
            item = self.list_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        if not self._rows:
            self.head.setText(tr("已装模组都是最新"))
            self.list_lay.addWidget(EmptyState(FIF.COMPLETED, tr("已装模组都是最新")))
            self.list_lay.addStretch(1)
            self.yesButton.setEnabled(False)
            return
        ignored = sum(1 for r in self._rows if r.get("ignored"))
        self.head.setText(tr("共 {n} 个可更新，已忽略 {m} 个").format(
            n=len(self._rows), m=ignored))
        for row in self._rows:
            self.list_lay.addWidget(self._row_widget(row))
        self.list_lay.addStretch(1)
        self.yesButton.setEnabled(not self._busy)

    def _row_widget(self, row: dict) -> QWidget:
        frame = QFrame(self)
        frame.setObjectName("modUpdRow")
        frame.setStyleSheet(row_qss("modUpdRow"))
        lay = QHBoxLayout(frame)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(10)

        cb = CheckBox()
        cb.setChecked(not row.get("ignored"))
        lay.addWidget(cb)
        self._checks.append((cb, row))

        box = QVBoxLayout()
        box.setSpacing(1)
        title = QLabel(str(row.get("name") or row.get("filename") or "?"))
        title.setStyleSheet(
            f"color: {Theme.title}; font-size: 13px; font-weight: 600; background: transparent;")
        box.addWidget(title)
        cap = CaptionLabel(f"{row.get('current') or '?'}  →  {row.get('latest') or '?'}"
                           f"  ·  {row.get('source') or ''}")
        cap.setStyleSheet(f"color: {Theme.muted}; font-size: 11px; background: transparent;")
        box.addWidget(cap)
        lay.addLayout(box, 1)

        if row.get("ignored"):
            lay.addWidget(Pill(tr("已忽略"), "#9E9E9E"))
            undo = TransparentPushButton(tr("取消忽略"))
            undo.clicked.connect(lambda _=False, r=row: self._unignore(r))
            lay.addWidget(undo)
        else:
            skip = TransparentPushButton(tr("忽略此版本"))
            skip.setToolTip(tr("这次的新版本不再提醒；再出更新的版本仍会提醒"))
            skip.clicked.connect(
                lambda _=False, r=row: self._ignore(r, str(r.get("latest") or "*")))
            mute = TransparentPushButton(tr("不再提醒"))
            mute.setToolTip(tr("这个模组以后所有更新都不再提醒"))
            mute.clicked.connect(lambda _=False, r=row: self._ignore(r, "*"))
            lay.addWidget(skip)
            lay.addWidget(mute)
        return frame

    # ------------------------------------------------------------------
    def _ignore(self, row: dict, latest: str):
        try:
            self.backend.ignore_mod_update(self.instance, row.get("project"), latest)
        except Exception as e:
            InfoBar.error(tr("忽略失败"), str(e), parent=self,
                          position=InfoBarPosition.TOP, duration=4000)
            return
        row["ignored"] = True
        self._refill()

    def _unignore(self, row: dict):
        try:
            self.backend.unignore_mod_update(self.instance, row.get("project"))
        except Exception as e:
            InfoBar.error(tr("操作失败"), str(e), parent=self,
                          position=InfoBarPosition.TOP, duration=4000)
            return
        row["ignored"] = False
        self._refill()

    # ------------------------------------------------------------------
    def validate(self) -> bool:
        """点「更新所选」：后台逐个下载替换，完成后自己关对话框。"""
        if self._busy:
            return False
        picked = [r for cb, r in self._checks if cb.isChecked()]
        if not picked:
            InfoBar.warning(tr("未选择"), tr("请先勾选要更新的模组。"), parent=self,
                            position=InfoBarPosition.TOP, duration=3000)
            return False
        self._busy = True
        self.yesButton.setEnabled(False)
        self.yesButton.setText(tr("更新中…"))
        self.cancelButton.setEnabled(False)

        def work():
            done, fails = [], []
            for r in picked:
                try:
                    done.append(self.backend.apply_mod_update(self.instance, r))
                except Exception as e:                      # noqa: BLE001
                    fails.append(f"{r.get('name') or r.get('filename')}: {e}")
            return done, fails

        def finish(result):
            done, fails = result
            self._busy = False
            self.page.reload_list()
            parent = self.page
            if fails:
                InfoBar.error(tr("部分更新失败"), "；".join(fails)[:400], parent=parent,
                              position=InfoBarPosition.TOP, duration=6000)
            if done:
                InfoBar.success(tr("模组已更新"),
                                tr("已更新 {n} 个模组").format(n=len(done)), parent=parent,
                                position=InfoBarPosition.TOP, duration=4000)
            self.accept()

        def fail(err):
            self._busy = False
            self.yesButton.setEnabled(True)
            self.yesButton.setText(tr("更新所选"))
            self.cancelButton.setEnabled(True)
            InfoBar.error(tr("更新失败"), str(err), parent=self,
                          position=InfoBarPosition.TOP, duration=5000)

        self.backend.call_async(work, guard(self, finish), guard(self, fail))
        return False


class ModManagerPage(QWidget):
    def __init__(self, backend, parent=None):
        super().__init__(parent)
        self.setObjectName("modsManagePage")
        self.backend = backend
        self.setStyleSheet("background: transparent;")
        self._entries: list[dict] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        card = PclCard()
        cv = QVBoxLayout(card)
        cv.setContentsMargins(16, 12, 16, 14)
        cv.setSpacing(10)

        head = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        self.title = SubtitleLabel(tr("模组管理"))
        self.title.setStyleSheet("font-size: 17px; font-weight: 700; background: transparent;")
        self.subtitle = CaptionLabel(tr("查看与管理已安装的模组"))
        self.subtitle.setStyleSheet(f"color: {Theme.muted}; background: transparent;")
        title_box.addWidget(self.title)
        title_box.addWidget(self.subtitle)
        head.addLayout(title_box, 1)
        self.count_pill = Pill(tr("0 个"), "#4C8BF5")
        head.addWidget(self.count_pill)
        cv.addLayout(head)

        bar = QHBoxLayout()
        bar.setSpacing(10)
        self.instance_box = ComboBox()
        self.instance_box.setFixedWidth(130)
        self.target_box = ComboBox()
        self.target_box.setFixedWidth(190)
        self.search = LineEdit()
        self.search.setPlaceholderText(tr("按名称或文件名筛选…"))
        self.search.setFixedWidth(200)
        bar.addWidget(self.instance_box)
        bar.addWidget(self.target_box)
        bar.addWidget(self.search)
        bar.addStretch(1)
        self.folder_btn = TransparentPushButton(FIF.FOLDER, tr("打开 mods 文件夹"))
        self.import_btn = TransparentPushButton(FIF.ADD, tr("导入 jar"))
        self.update_btn = TransparentPushButton(FIF.SYNC, tr("检查更新"))
        self.lock_btn = TransparentTogglePushButton(
            getattr(FIF, "LOCK", getattr(FIF, "PIN", FIF.SETTING)), tr("锁定更新"))
        self.lock_btn.setToolTip(
            tr("禁止本实例检查/更新 Mod（PCL 同款整合包保护，防误更新拆包）"))
        self.conflict_btn = TransparentPushButton(FIF.SEARCH, tr("冲突扫描"))
        self.export_btn = TransparentPushButton(
            getattr(FIF, "SAVE_AS", FIF.SAVE), tr("导出清单"))
        self.export_btn.setToolTip(tr("把模组列表导出成 Markdown 文件，方便分享"))
        self.batch_btn = TransparentTogglePushButton(
            getattr(FIF, "CHECKBOX", FIF.EDIT), tr("批量管理"))
        self.batch_btn.setToolTip(tr("勾选多个模组批量启用/禁用/删除"))
        self.ai_btn = TransparentPushButton(getattr(FIF, "CHAT", None) or FIF.HELP,
                                            tr("AI 查冲突"))
        for b in (self.folder_btn, self.import_btn, self.update_btn, self.lock_btn,
                  self.conflict_btn, self.export_btn, self.batch_btn,
                  self.ai_btn):
            b.setFixedHeight(32)
            bar.addWidget(b)
        cv.addLayout(bar)

        # 批量操作条（PCL2 同款）：默认隐藏，点「批量管理」出现
        self._batch_mode = False
        self._selected: set[str] = set()
        self.batch_bar = QWidget()
        bb = QHBoxLayout(self.batch_bar)
        bb.setContentsMargins(0, 0, 0, 0)
        bb.setSpacing(10)
        self.sel_label = CaptionLabel(tr("已选 {n} 个").format(n=0))
        self.sel_label.setStyleSheet(f"color: {Theme.muted}; background: transparent;")
        bb.addWidget(self.sel_label)
        self.sel_all_btn = TransparentPushButton(tr("全选"))
        self.sel_all_btn.setToolTip(tr("选中当前筛选出的全部模组；已全选时再点一次取消"))
        self.enable_sel_btn = TransparentPushButton(tr("启用所选"))
        self.disable_sel_btn = TransparentPushButton(tr("禁用所选"))
        self.delete_sel_btn = TransparentPushButton(FIF.DELETE, tr("删除所选"))
        for b in (self.sel_all_btn, self.enable_sel_btn, self.disable_sel_btn,
                  self.delete_sel_btn):
            b.setFixedHeight(30)
            bb.addWidget(b)
        bb.addStretch(1)
        self.batch_bar.hide()
        cv.addWidget(self.batch_bar)

        tip = CaptionLabel(
            tr("提示：在版本设置里开启「隔离 Mod」后，各版本会拥有独立 mods 目录，可在此切换查看。"))
        tip.setStyleSheet(f"color: {Theme.muted}; font-size: 11px; background: transparent;")
        tip.setWordWrap(True)
        cv.addWidget(tip)
        root.addWidget(card)

        list_card = PclCard()
        lv = QVBoxLayout(list_card)
        lv.setContentsMargins(8, 6, 8, 8)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        host = QWidget()
        self.list_layout = QVBoxLayout(host)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(0)
        scroll.setWidget(host)
        lv.addWidget(scroll)
        root.addWidget(list_card, 1)

        self.search.textChanged.connect(self._refill)
        self.instance_box.currentTextChanged.connect(lambda _t: self._reload_targets())
        self.target_box.currentTextChanged.connect(lambda _t: self.reload_list())
        self.folder_btn.clicked.connect(self._open_folder)
        self.import_btn.clicked.connect(self._import_local)
        self.update_btn.clicked.connect(self._check_updates)
        self.lock_btn.toggled.connect(self._toggle_update_lock)
        self.conflict_btn.clicked.connect(self._scan_conflicts)
        self.export_btn.clicked.connect(self._export_list)
        self.batch_btn.toggled.connect(self._toggle_batch)
        self.sel_all_btn.clicked.connect(self._select_all_visible)
        self.enable_sel_btn.clicked.connect(lambda: self._batch_apply("enable"))
        self.disable_sel_btn.clicked.connect(lambda: self._batch_apply("disable"))
        self.delete_sel_btn.clicked.connect(lambda: self._batch_apply("delete"))
        self.ai_btn.clicked.connect(lambda: self._ask_ai_conflicts())
        self.setAcceptDrops(True)

        # 目录监视（PCL2 同款）：在文件管理器里往 mods 文件夹放/删文件，
        # 切回启动器列表自动刷新，不用手动切换实例。600ms 去抖，
        # 批量复制几十个 jar 只触发一次重读。
        self._watcher = QFileSystemWatcher(self)
        self._watcher.directoryChanged.connect(self._on_dir_changed)
        self._watch_timer = QTimer(self)
        self._watch_timer.setSingleShot(True)
        self._watch_timer.setInterval(600)
        self._watch_timer.timeout.connect(self.reload_list)

        self._reload_instances()
        self._reload_targets()
        self.reload_list()

    # ------------------------------------------------------------------
    def _current_instance(self) -> str:
        return self.instance_box.currentText() or CONFIG.get("default_instance", "default") or "default"

    def _current_version(self) -> str:
        rows = getattr(self, "_target_rows", None) or []
        idx = self.target_box.currentIndex()
        return str(rows[idx].get("value") or "") if 0 <= idx < len(rows) else ""

    def _reload_instances(self):
        cur = self.instance_box.currentText()
        names = [i["name"] for i in self.backend.get_instances()]
        self.instance_box.blockSignals(True)
        self.instance_box.clear()
        self.instance_box.addItems(names)
        if cur in names:
            self.instance_box.setCurrentText(cur)
        elif CONFIG.get("default_instance") in names:
            self.instance_box.setCurrentText(CONFIG.get("default_instance"))
        self.instance_box.blockSignals(False)

    def _reload_targets(self):
        try:
            rows = self.backend.get_mods_targets(self._current_instance()) or []
        except Exception:
            rows = [{"label": tr("实例共享 mods 目录"), "value": ""}]
        self._target_rows = rows
        cur_idx = self.target_box.currentIndex()
        self.target_box.blockSignals(True)
        self.target_box.clear()
        for r in rows:
            self.target_box.addItem(r.get("label") or "?")
        if 0 <= cur_idx < len(rows):
            self.target_box.setCurrentIndex(cur_idx)
        self.target_box.blockSignals(False)
        self._sync_update_lock()
        self.reload_list()

    def _sync_update_lock(self):
        """按当前实例刷新「锁定更新」按钮状态（不触发写回）。"""
        try:
            locked = bool(self.backend.get_mod_update_lock(self._current_instance()))
        except Exception:
            locked = False
        self.lock_btn.blockSignals(True)
        self.lock_btn.setChecked(locked)
        self.lock_btn.blockSignals(False)

    def _toggle_update_lock(self, checked: bool):
        inst = self._current_instance()
        try:
            self.backend.set_mod_update_lock(inst, bool(checked))
        except Exception as e:
            InfoBar.error(tr("操作失败"), str(e), parent=self,
                          position=InfoBarPosition.TOP, duration=4000)
            self._sync_update_lock()
            return
        if checked:
            InfoBar.info(tr("已锁定 Mod 更新"),
                         tr("实例「{inst}」将不再检查/更新 Mod，防止整合包被误更新拆坏。").format(inst=inst),
                         parent=self, position=InfoBarPosition.TOP, duration=4000)
        else:
            InfoBar.success(tr("已解除锁定"),
                            tr("实例「{inst}」恢复 Mod 更新功能。").format(inst=inst),
                            parent=self, position=InfoBarPosition.TOP, duration=3000)

    # ------------------------------------------------------------------
    def reload(self):
        self._reload_instances()
        self._reload_targets()

    def reload_list(self):
        inst = self._current_instance()
        ver = self._current_version()
        try:
            self._entries = self.backend.get_installed_mod_entries(inst, ver) or []
        except Exception as e:
            self._entries = []
            InfoBar.error(tr("读取模组失败"), str(e), parent=self,
                          position=InfoBarPosition.TOP, duration=4000)
        self._apply_subtitle()
        self._refill()
        self._watch_current(inst, ver)
        self._load_details(inst, ver)

    def _watch_current(self, inst: str, ver: str):
        """让目录监视跟随当前实例/版本的 mods 目录。"""
        old = self._watcher.directories()
        if old:
            self._watcher.removePaths(old)
        try:
            folder = self.backend.get_mods_folder(inst, ver)
        except Exception:
            return
        if folder and Path(folder).is_dir():
            self._watcher.addPath(folder)

    def _on_dir_changed(self, _path):
        self._watch_timer.start()

    def _load_details(self, inst: str, ver: str):
        """后台解析 jar 元数据（有缓存），回来后把文件名列表换成模组名列表。"""
        if not self._entries:
            return
        self._detail_gen = getattr(self, "_detail_gen", 0) + 1
        gen = self._detail_gen

        def _done(rows):
            if gen != self._detail_gen or not rows:
                return
            if inst != self._current_instance() or ver != self._current_version():
                return
            self._entries = rows
            self._apply_subtitle()
            self._refill()

        self.backend.call_async(
            lambda i=inst, v=ver: self.backend.get_mod_details(i, v),
            guard(self, _done))

    def _apply_subtitle(self):
        total = len(self._entries)
        on = sum(1 for r in self._entries if r.get("enabled"))
        off = total - on
        size = sum(int(r.get("bytes") or 0) for r in self._entries)
        self.count_pill.setText(f"{on}/{total}")
        self.subtitle.setText(
            f"{tr('启用')} {on} · {tr('禁用')} {off} · {_fmt_size(size)} · "
            f"{self._current_instance()}{(' / ' + ver_label) if (ver_label := self._current_version()) else ''}")

    def _visible_rows(self) -> list[dict]:
        text = (self.search.text() or "").strip().lower()
        return [r for r in self._entries
                if not text or text in str(r.get("filename") or "").lower()
                or text in str(r.get("name") or "").lower()]

    def _refill(self, *_):
        if self._batch_mode:
            valid = {str(r.get("filename") or "") for r in self._entries}
            if not self._selected <= valid:
                self._selected &= valid
                self._update_batch_label()
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        rows = self._visible_rows()
        if not rows:
            if self._entries:
                self.list_layout.addWidget(EmptyState(FIF.SEARCH, tr("没有匹配的模组")))
            else:
                self.list_layout.addWidget(
                    EmptyState(FIF.TAG, tr("还没有安装模组，可点右上角「导入 jar」或到「下载」页安装")))
            self.list_layout.addStretch(1)
            return
        for row in rows:
            self.list_layout.addWidget(_ModRow(row, self))
        self.list_layout.addStretch(1)

    # ---------------------------------------------------------- 批量管理
    def _toggle_batch(self, on: bool):
        self._batch_mode = bool(on)
        self._selected.clear()
        self.batch_bar.setVisible(self._batch_mode)
        self._update_batch_label()
        self._refill()

    def _on_row_check(self, filename: str, checked: bool):
        if checked:
            self._selected.add(filename)
        else:
            self._selected.discard(filename)
        self._update_batch_label()

    def _select_all_visible(self):
        """全选当前筛选出的行；已经全选时再点一次取消这些行。"""
        vis = {str(r.get("filename") or "") for r in self._visible_rows()} - {""}
        if vis and vis <= self._selected:
            self._selected -= vis
        else:
            self._selected |= vis
        self._update_batch_label()
        self._refill()

    def _update_batch_label(self):
        n = len(self._selected)
        self.sel_label.setText(tr("已选 {n} 个").format(n=n))
        for b in (self.enable_sel_btn, self.disable_sel_btn, self.delete_sel_btn):
            b.setEnabled(n > 0)

    def _batch_apply(self, action: str):
        """批量启用/禁用/删除（PCL2 同款）。

        启用/禁用会改文件名（加/去 .disabled），用后端返回的新名字
        更新选中集合，操作完保持选中状态；失败的行留在选中集里。
        """
        files = [str(r.get("filename") or "") for r in self._entries
                 if str(r.get("filename") or "") in self._selected]
        if not files:
            return
        inst = self._current_instance()
        ver = self._current_version()
        if action == "delete":
            box = MessageBox(
                tr("删除确认"),
                tr("将删除所选 {n} 个模组文件（会尽量移入系统回收站，可找回）。").format(n=len(files)),
                self)
            box.yesButton.setText(tr("删除"))
            box.cancelButton.setText(tr("取消"))
            if not box.exec():
                return
        fails = []
        kept: set[str] = set()
        for fn in files:
            try:
                if action == "enable":
                    kept.add(str(self.backend.enable_mod(inst, fn, ver) or fn))
                elif action == "disable":
                    kept.add(str(self.backend.disable_mod(inst, fn, ver) or fn))
                else:
                    self.backend.delete_mod(inst, fn, ver)
            except Exception as e:  # noqa: BLE001
                fails.append(f"{fn}: {e}")
                kept.add(fn)
        self._selected = kept
        if fails:
            InfoBar.error(tr("部分操作失败"), "；".join(fails)[:400], parent=self,
                          position=InfoBarPosition.TOP, duration=6000)
        else:
            labels = {"enable": tr("已启用 {n} 个模组"),
                      "disable": tr("已禁用 {n} 个模组"),
                      "delete": tr("已删除 {n} 个模组")}
            InfoBar.success(tr("批量操作完成"), labels[action].format(n=len(files)),
                            parent=self, position=InfoBarPosition.TOP, duration=4000)
        self.reload_list()

    # ------------------------------------------------------------------
    def _toggle(self, filename: str, enabled: bool, row=None):
        inst = self._current_instance()
        ver = self._current_version()
        try:
            if enabled:
                self.backend.enable_mod(inst, filename, ver)
            else:
                self.backend.disable_mod(inst, filename, ver)
        except Exception as e:
            InfoBar.error(tr("切换失败"), str(e), parent=self,
                          position=InfoBarPosition.TOP, duration=4000)
        finally:
            self.reload_list()

    def _delete(self, filename: str):
        inst = self._current_instance()
        ver = self._current_version()
        box = MessageBox(tr("删除确认"),
                         tr("将删除模组文件「{name}」（会尽量移入系统回收站，可找回）。").format(name=filename),
                         self)
        box.yesButton.setText(tr("删除"))
        box.cancelButton.setText(tr("取消"))
        if not box.exec():
            return
        try:
            self.backend.delete_mod(inst, filename, ver)
        except Exception as e:
            InfoBar.error(tr("删除失败"), str(e), parent=self,
                          position=InfoBarPosition.TOP, duration=4000)
            return
        self.reload_list()

    def _open_folder(self):
        try:
            self.backend.open_mods_folder(self._current_instance(), self._current_version())
        except Exception as e:
            InfoBar.error(tr("打开失败"), str(e), parent=self,
                          position=InfoBarPosition.TOP, duration=4000)

    def _install_jars(self, paths):
        inst = self._current_instance()
        ver = self._current_version()
        win = self.window()
        for p in paths:
            extra = {"path": p, "instance": inst, "version": ver, "source": "本地"}
            if win is not None and hasattr(win, "fly_to_tasks"):
                win.fly_to_tasks(self.import_btn, Path(p).name)
            try:
                self.backend.install_mod(p, inst, extra=extra)
            except Exception as e:
                InfoBar.error(tr("导入失败"), str(e), parent=self,
                              position=InfoBarPosition.TOP, duration=4000)

    def _import_local(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, tr("选择模组 jar"), "", tr("模组 (*.jar)"))
        if paths:
            self._install_jars(paths)

    def _check_updates(self):
        inst = self._current_instance()
        try:
            if self.backend.get_mod_update_lock(inst):
                box = MessageBox(
                    tr("已锁定 Mod 更新"),
                    tr("实例「{inst}」开启了 Mod 更新锁定（整合包保护）。\n"
                       "要解除锁定并继续检查更新吗？").format(inst=inst),
                    self)
                box.yesButton.setText(tr("解除锁定并检查"))
                box.cancelButton.setText(tr("取消"))
                if not box.exec():
                    return
                self.backend.set_mod_update_lock(inst, False)
                self._sync_update_lock()
            ModUpdateDialog(self, inst).exec()
        except Exception as e:
            InfoBar.error(tr("检查更新失败"), str(e), parent=self,
                          position=InfoBarPosition.TOP, duration=4000)

    def _export_list(self):
        inst = self._current_instance()
        ver = self._current_version()
        self.export_btn.setEnabled(False)

        def _done(path):
            self.export_btn.setEnabled(True)
            InfoBar.success(tr("已导出模组清单"), str(path), parent=self,
                            position=InfoBarPosition.TOP, duration=5000)
            try:
                self.backend.open_media(str(path))
            except Exception:
                pass

        def _fail(msg):
            self.export_btn.setEnabled(True)
            InfoBar.error(tr("导出失败"), str(msg), parent=self,
                          position=InfoBarPosition.TOP, duration=4000)

        self.backend.call_async(
            lambda i=inst, v=ver: self.backend.export_mod_list(i, v),
            guard(self, _done), guard(self, _fail))

    def _scan_conflicts(self):
        inst = self._current_instance()
        ver = self._current_version()
        self.conflict_btn.setEnabled(False)
        self.conflict_btn.setText(tr("扫描中…"))

        def _restore():
            self.conflict_btn.setEnabled(True)
            self.conflict_btn.setText(tr("冲突扫描"))

        def _done(report):
            _restore()
            report = report or {}
            if not report.get("mod_count"):
                InfoBar.info(tr("没有模组"), tr("当前目录里没有可扫描的模组。"), parent=self,
                             position=InfoBarPosition.TOP, duration=3000)
                return
            if not report.get("issue_count"):
                InfoBar.success(
                    tr("未发现冲突"),
                    tr("已扫描 {n} 个模组，没有发现重复、缺依赖或不兼容。").format(
                        n=report.get("mod_count")),
                    parent=self, position=InfoBarPosition.TOP, duration=4000)
                return
            ConflictReportDialog(report, self.window()).exec()

        def _fail(err):
            _restore()
            InfoBar.error(tr("扫描失败"), str(err), parent=self,
                          position=InfoBarPosition.TOP, duration=4000)

        self.backend.call_async(
            lambda i=inst, v=ver: self.backend.scan_mod_conflicts(i, v),
            guard(self, _done), guard(self, _fail))

    def _open_ai(self, prompt: str):
        win = self.window()
        if hasattr(win, "open_ai_with_context"):
            win.open_ai_with_context(prompt, source=tr("模组管理页"))
        else:
            InfoBar.warning(tr("无法打开 AI"), tr("主窗口没有 AI 助手页"), parent=self,
                            position=InfoBarPosition.TOP, duration=3000)

    def _ask_ai_conflicts(self, focus: str = ""):
        inst = self._current_instance()
        prompt = tr("帮我扫描实例 {0} 的模组冲突、缺失依赖和加载器不匹配，"
                    "给出禁用或补装建议。").format(inst)
        if focus:
            prompt += tr("我比较担心 {0} 这个模组。").format(focus)
        self._open_ai(prompt)

    def _ask_ai_mod(self, entry: dict):
        inst = self._current_instance()
        name = entry.get("filename") or "?"
        state = tr("已启用") if entry.get("enabled") else tr("已禁用")
        prompt = tr("实例 {0} 里有个模组文件 {1}（{2}）。"
                    "这是干什么用的？和我现有模组会不会冲突、有没有缺依赖？"
                    ).format(inst, name, state)
        self._open_ai(prompt)

    # ------------------------------------------------------------------
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        paths = [u.toLocalFile() for u in event.mimeData().urls()
                 if u.toLocalFile() and u.toLocalFile().lower().endswith(".jar")]
        if paths:
            self._install_jars(paths)

    def showEvent(self, event):
        super().showEvent(event)
        # 拖拽/粘贴场景少，这里只做轻量刷新
        clip = QGuiApplication.clipboard().text().strip()
        low = clip.lower()
        if clip and ("modrinth.com" in low or "curseforge.com" in low):
            InfoBar.info(tr("识别到剪贴板链接"), tr("到「下载」页搜索框粘贴即可安装"), parent=self,
                         position=InfoBarPosition.TOP, duration=3000)
