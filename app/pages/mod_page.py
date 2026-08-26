# -*- coding: utf-8 -*-
"""模组管理页：查看已安装模组、启用/禁用、删除、导入、检查更新。

侧边栏一级入口。目录选择与安装目标一致：实例共享 mods + 开了版本隔离的版本。
"""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QFileDialog, QFrame, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget,
)
from qfluentwidgets import (
    CaptionLabel, ComboBox, FluentIcon as FIF, InfoBar, InfoBarPosition, LineEdit,
    MessageBox, PushButton, SubtitleLabel, SwitchButton, TransparentPushButton,
    TransparentToolButton,
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
    """单个已安装模组：图标 + 文件名 + 大小 + 启用开关 + 删除。"""

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

        info = QVBoxLayout()
        info.setSpacing(1)
        title = QLabel(name)
        title.setStyleSheet(
            f"color: {Theme.title}; font-size: 13px; font-weight: 600; background: transparent;")
        info.addWidget(title)
        meta = CaptionLabel(
            f"{_fmt_size(entry.get('bytes'))}"
            + (f"  ·  {tr('已禁用')}" if not entry.get("enabled") else ""))
        meta.setStyleSheet(f"color: {Theme.muted}; font-size: 11px; background: transparent;")
        info.addWidget(meta)
        lay.addLayout(info, 1)

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
        self.search.setPlaceholderText(tr("按文件名筛选…"))
        self.search.setFixedWidth(200)
        bar.addWidget(self.instance_box)
        bar.addWidget(self.target_box)
        bar.addWidget(self.search)
        bar.addStretch(1)
        self.folder_btn = TransparentPushButton(FIF.FOLDER, tr("打开 mods 文件夹"))
        self.import_btn = TransparentPushButton(FIF.ADD, tr("导入 jar"))
        # 后端任务会把查到的更新直接装上（_mod_update_impl），按钮名要说实话
        self.update_btn = TransparentPushButton(FIF.SYNC, tr("检查并更新"))
        for b in (self.folder_btn, self.import_btn, self.update_btn):
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
        rows = [r for r in self._entries
                if not text or text in str(r.get("filename") or "").lower()]
        if not rows:
            if self._entries:
                self.list_layout.addWidget(EmptyState(FIF.SEARCH, tr("没有匹配的模组")))
            else:
                self.list_layout.addWidget(
                    EmptyState(FIF.TAG, tr("还没有安装模组，可点右上角「导入 jar」或到「下载 → Mod」安装")))
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
                         tr("将删除模组文件「{0}」，不可恢复。").format(filename), self)
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
            return
        win = self.window()
        if hasattr(win, "fly_to_tasks"):
            win.fly_to_tasks(self.update_btn, tr("更新"))
        InfoBar.success(
            tr("已开始检查并更新"),
            tr("进度见「下载任务」；查到的更新会直接装进 mods 文件夹"),
            parent=self, position=InfoBarPosition.TOP, duration=4000,
        )

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
            InfoBar.info(tr("识别到剪贴板链接"), tr("到「下载 → Mod」搜索框粘贴即可安装"), parent=self,
                         position=InfoBarPosition.TOP, duration=3000)
