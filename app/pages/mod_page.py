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
    CaptionLabel, ComboBox, FluentIcon as FIF, InfoBar, InfoBarPosition, LineEdit,
    MessageBox, MessageBoxBase, PushButton, SubtitleLabel, SwitchButton,
    TransparentPushButton, TransparentToolButton,
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
        self.conflict_btn = TransparentPushButton(FIF.SEARCH, tr("冲突扫描"))
        for b in (self.folder_btn, self.import_btn, self.update_btn, self.conflict_btn):
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
        self.conflict_btn.clicked.connect(self._scan_conflicts)
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

    def _refill(self, *_):
        text = (self.search.text() or "").strip().lower()
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        rows = [r for r in self._entries
                if not text or text in str(r.get("filename") or "").lower()
                or text in str(r.get("name") or "").lower()]
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
        try:
            self.backend.start_mod_updates(self._current_instance())
        except Exception as e:
            InfoBar.error(tr("检查更新失败"), str(e), parent=self,
                          position=InfoBarPosition.TOP, duration=4000)

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
