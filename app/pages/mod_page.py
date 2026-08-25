# -*- coding: utf-8 -*-
"""模组管理页：查看已安装模组、启用/禁用、删除、导入、检查更新。

侧边栏一级入口。目录选择与安装目标一致：实例共享 mods + 开了版本隔离的版本。
"""

from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QFileDialog, QFrame, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget,
)
from qfluentwidgets import (
    BodyLabel, CaptionLabel, ComboBox, FluentIcon as FIF, InfoBar, InfoBarPosition,
    LineEdit, MessageBox, MessageBoxBase, PushButton, ScrollArea, SubtitleLabel,
    SwitchButton, TransparentPushButton, TransparentToolButton,
)

from mclauncher.config import CONFIG
from mclauncher.i18n import tr
from ..pcl_chrome import Theme, ghost_btn_qss, row_qss
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
    """单个已安装模组：图标 + 模组名(中文译名) + 版本/文件名 + mcmod 链接 + 开关 + 删除。"""

    def __init__(self, entry: dict, page):
        super().__init__(page)
        self.entry = entry
        self.setObjectName("modMgrRow")
        self.setStyleSheet(row_qss("modMgrRow"))
        self.setFixedHeight(60)
        name = entry.get("filename") or "?"

        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(12)
        lay.addWidget(IconTile(name, size=40))

        # 标题：真实模组名优先；有中文译名显示「中文名 (English)」（HMCL 同款）
        display = entry.get("mod_name") or name
        name_cn = entry.get("name_cn") or ""
        text = f"{name_cn} ({display})" if name_cn and name_cn != display else display
        info = QVBoxLayout()
        info.setSpacing(1)
        title = QLabel(text)
        title.setStyleSheet(
            f"color: {Theme.title}; font-size: 13px; font-weight: 600; background: transparent;")
        info.addWidget(title)
        bits = []
        if entry.get("mod_version"):
            bits.append(str(entry["mod_version"]))
        if entry.get("loader"):
            bits.append(str(entry["loader"]))
        if entry.get("mod_name"):   # 标题已不是文件名时，补文件名方便对照磁盘
            bits.append(name)
        bits.append(_fmt_size(entry.get("bytes")))
        if not entry.get("enabled"):
            bits.append(tr("已禁用"))
        meta = CaptionLabel("  ·  ".join(bits))
        meta.setStyleSheet(f"color: {Theme.muted}; font-size: 11px; background: transparent;")
        info.addWidget(meta)
        lay.addLayout(info, 1)

        url = entry.get("mcmod_url") or ""
        if url:
            link = TransparentToolButton(FIF.GLOBE)
            link.setToolTip(tr("mcmod 百科"))
            link.clicked.connect(lambda _, u=url: QDesktopServices.openUrl(QUrl(u)))
            lay.addWidget(link)

        self.switch = SwitchButton()
        self.switch.setChecked(bool(entry.get("enabled")))
        self.switch.setOnText(tr("启用"))
        self.switch.setOffText(tr("禁用"))
        self.switch.checkedChanged.connect(lambda on, n=name: page._toggle(n, on, self))
        lay.addWidget(self.switch)

        btn = TransparentToolButton(FIF.DELETE)
        btn.setToolTip(tr("删除"))
        btn.clicked.connect(lambda _, n=name: page._delete(n))
        lay.addWidget(btn)


_ISSUE_LABELS = {
    "duplicate_id": "重复安装",
    "loader_mismatch": "加载器不匹配",
    "missing_dep": "缺少前置",
    "breaks": "互不兼容",
}


class ConflictScanDialog(MessageBoxBase):
    """模组冲突扫描结果（对标 HMCL 模组警告；核心与 AI 工具共用）。"""

    def __init__(self, result: dict, parent=None):
        super().__init__(parent)
        result = result or {}
        issues = list(result.get("issues") or [])

        self.viewLayout.addWidget(SubtitleLabel(tr("模组冲突扫描"), self))
        bits = [f"{tr('模组')} {result.get('enabled', 0)}/{result.get('mod_count', 0)}"]
        if result.get("loader"):
            bits.append(str(result["loader"]))
        if result.get("mc_version"):
            bits.append(f"MC {result['mc_version']}")
        summary = BodyLabel("  ·  ".join(bits), self)
        summary.setStyleSheet(f"color: {Theme.muted}; background: transparent;")
        self.viewLayout.addWidget(summary)

        if not issues:
            ok = BodyLabel(tr("未发现重复安装、缺失前置或不兼容声明。"), self)
            ok.setStyleSheet("color: #2E9E5B; font-weight: 600; background: transparent;")
            self.viewLayout.addWidget(ok)
        else:
            scroll = ScrollArea(self)
            scroll.setWidgetResizable(True)
            scroll.setStyleSheet("ScrollArea { background: transparent; border: none; }")
            host = QWidget()
            lay = QVBoxLayout(host)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(4)
            for issue in issues:
                lay.addWidget(self._issue_row(issue))
            lay.addStretch(1)
            scroll.setWidget(host)
            scroll.setMinimumSize(560, min(320, 60 * len(issues) + 16))
            self.viewLayout.addWidget(scroll, 1)

        self.yesButton.setText(tr("我知道了"))
        self.cancelButton.hide()
        self.widget.setMinimumWidth(620)

    def _issue_row(self, issue: dict) -> QFrame:
        row = QFrame()
        row.setObjectName("conflictRow")
        row.setStyleSheet(row_qss("conflictRow"))
        row.setMinimumHeight(52)
        h = QHBoxLayout(row)
        h.setContentsMargins(12, 8, 12, 8)
        h.setSpacing(10)
        sev = str(issue.get("severity") or "warn")
        color = "#D95568" if sev == "error" else "#D9A441"
        pill = QLabel(tr("错误") if sev == "error" else tr("警告"))
        pill.setStyleSheet(
            f"color: white; background: {color}; border-radius: 9px;"
            " padding: 2px 10px; font-size: 11px; font-weight: 600;")
        h.addWidget(pill)
        col = QVBoxLayout()
        col.setSpacing(1)
        msg = QLabel(str(issue.get("message") or "?"))
        msg.setWordWrap(True)
        msg.setStyleSheet(
            f"color: {Theme.title}; font-size: 13px; background: transparent;")
        col.addWidget(msg)
        detail_bits = [tr(_ISSUE_LABELS.get(str(issue.get("type")), str(issue.get("type") or "")))]
        files = issue.get("files") or ([issue.get("file")] if issue.get("file") else [])
        if files:
            detail_bits.append(", ".join(str(f) for f in files[:4]))
        cap = QLabel("  ·  ".join(b for b in detail_bits if b))
        cap.setStyleSheet(f"color: {Theme.muted}; font-size: 11px; background: transparent;")
        col.addWidget(cap)
        h.addLayout(col, 1)
        return row


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
        self.search.setPlaceholderText(tr("按名称 / 文件名筛选…"))
        self.search.setFixedWidth(200)
        bar.addWidget(self.instance_box)
        bar.addWidget(self.target_box)
        bar.addWidget(self.search)
        bar.addStretch(1)
        self.folder_btn = TransparentPushButton(FIF.FOLDER, tr("打开 mods 文件夹"))
        self.import_btn = TransparentPushButton(FIF.ADD, tr("导入 jar"))
        self.update_btn = TransparentPushButton(FIF.SYNC, tr("检查更新"))
        self.scan_btn = TransparentPushButton(FIF.SEARCH, tr("扫描冲突"))
        for b in (self.folder_btn, self.import_btn, self.update_btn, self.scan_btn):
            b.setFixedHeight(32)
            bar.addWidget(b)
        cv.addLayout(bar)

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
        self.scan_btn.clicked.connect(self._scan_conflicts)
        self.setAcceptDrops(True)

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
        self.reload_list()

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

    def _apply_subtitle(self):
        total = len(self._entries)
        on = sum(1 for r in self._entries if r.get("enabled"))
        off = total - on
        size = sum(int(r.get("bytes") or 0) for r in self._entries)
        self.count_pill.setText(f"{on}/{total}")
        self.subtitle.setText(
            f"{tr('启用')} {on} · {tr('禁用')} {off} · {_fmt_size(size)} · "
            f"{self._current_instance()}{(' / ' + ver_label) if (ver_label := self._current_version()) else ''}")

    def _refill(self, *_):
        text = (self.search.text() or "").strip().lower()
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        def _match(r: dict) -> bool:
            if not text:
                return True
            hay = " ".join(str(r.get(k) or "") for k in
                           ("filename", "mod_name", "name_cn", "id")).lower()
            return text in hay

        rows = [r for r in self._entries if _match(r)]
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
        box = MessageBox(tr("删除确认"), f"将删除模组文件「{filename}」，不可恢复。", self)
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
        try:
            self.backend.start_mod_updates(self._current_instance())
        except Exception as e:
            InfoBar.error(tr("检查更新失败"), str(e), parent=self,
                          position=InfoBarPosition.TOP, duration=4000)

    def _scan_conflicts(self):
        inst = self._current_instance()
        ver = self._current_version()
        self.scan_btn.setEnabled(False)
        self.scan_btn.setText(tr("扫描中…"))

        def restore():
            import shiboken6
            if shiboken6.isValid(self.scan_btn):
                self.scan_btn.setEnabled(True)
                self.scan_btn.setText(tr("扫描冲突"))

        def ok(result):
            restore()
            ConflictScanDialog(result or {}, self.window()).exec()

        def err(message):
            restore()
            InfoBar.error(tr("扫描失败"), str(message), parent=self,
                          position=InfoBarPosition.TOP, duration=4000)

        self.backend.call_async(
            lambda: self.backend.scan_mod_conflicts(inst, ver), ok, err)

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
