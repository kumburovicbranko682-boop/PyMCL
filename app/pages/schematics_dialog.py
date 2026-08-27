# -*- coding: utf-8 -*-
"""原理图管理对话框（HMCL 原理图管理界面同款）。

列出游戏目录 schematics/ 里的 Litematica / WorldEdit / 结构方块文件，
展示能解析出的元数据（名称/作者/尺寸/方块数），支持导入、删除、打开文件夹。
"""
from PySide6.QtWidgets import (
    QFileDialog, QFrame, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget,
)
from qfluentwidgets import (
    CaptionLabel, FluentIcon as FIF, InfoBar, InfoBarPosition, MessageBox,
    MessageBoxBase, PushButton, SubtitleLabel, TransparentToolButton,
)

from mclauncher.i18n import tr
from ..pcl_chrome import Theme, row_qss
from ..widgets import EmptyState, Pill


def _fmt_size(n) -> str:
    try:
        n = float(n or 0)
    except (TypeError, ValueError):
        return "—"
    if n >= 1024 ** 2:
        return f"{n / 1024 ** 2:.1f} MB"
    if n >= 1024:
        return f"{n / 1024:.0f} KB"
    return f"{int(n)} B"


class SchematicsDialog(MessageBoxBase):
    def __init__(self, backend, instance: str, version: str = "", parent=None):
        super().__init__(parent)
        self.backend = backend
        self.instance = instance
        self.version = version or ""

        self.viewLayout.addWidget(SubtitleLabel(tr("原理图管理"), self))
        self.head = CaptionLabel("", self)
        self.head.setStyleSheet(f"color: {Theme.muted}; background: transparent;")
        self.head.setWordWrap(True)
        self.viewLayout.addWidget(self.head)

        bar = QHBoxLayout()
        self.import_btn = PushButton(tr("导入原理图"))
        self.folder_btn = PushButton(tr("打开文件夹"))
        bar.addWidget(self.import_btn)
        bar.addWidget(self.folder_btn)
        bar.addStretch(1)
        host = QWidget(self)
        host.setLayout(bar)
        self.viewLayout.addWidget(host)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        inner = QWidget(self)
        self.list_lay = QVBoxLayout(inner)
        self.list_lay.setContentsMargins(0, 0, 0, 0)
        self.list_lay.setSpacing(6)
        scroll.setWidget(inner)
        scroll.setMinimumHeight(260)
        self.viewLayout.addWidget(scroll)

        self.yesButton.setText(tr("关闭"))
        self.cancelButton.hide()
        self.widget.setMinimumWidth(620)

        self.import_btn.clicked.connect(self._import)
        self.folder_btn.clicked.connect(self._open_folder)
        self.reload()

    # ------------------------------------------------------------------
    def reload(self):
        while self.list_lay.count():
            item = self.list_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        try:
            rows = self.backend.list_schematics(self.instance, self.version) or []
        except Exception as e:
            rows = []
            self.head.setText(tr("读取失败：{err}").format(err=e))
        target = self.instance + (f" / {self.version}" if self.version else "")
        self.head.setText(tr("{target} · 共 {n} 个原理图").format(target=target, n=len(rows)))
        if not rows:
            self.list_lay.addWidget(EmptyState(
                FIF.LAYOUT, tr("schematics 文件夹还是空的，可点「导入原理图」添加")))
            self.list_lay.addStretch(1)
            return
        for row in rows:
            self.list_lay.addWidget(self._row(row))
        self.list_lay.addStretch(1)

    def _row(self, row: dict) -> QWidget:
        frame = QFrame(self)
        frame.setObjectName("schemRow")
        frame.setStyleSheet(row_qss("schemRow"))
        lay = QHBoxLayout(frame)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(10)

        box = QVBoxLayout()
        box.setSpacing(1)
        display = str(row.get("title") or "").strip() or row.get("name") or "?"
        title = QLabel(display)
        title.setStyleSheet(
            f"color: {Theme.title}; font-size: 13px; font-weight: 600; background: transparent;")
        box.addWidget(title)
        bits = []
        if row.get("size"):
            bits.append(str(row["size"]))
        if row.get("blocks"):
            bits.append(tr("{n} 个方块").format(n=row["blocks"]))
        if row.get("author"):
            bits.append(str(row["author"]))
        bits.append(_fmt_size(row.get("bytes")))
        if display != row.get("name"):
            bits.append(str(row.get("name") or ""))
        cap = CaptionLabel("  ·  ".join(b for b in bits if b))
        cap.setStyleSheet(f"color: {Theme.muted}; font-size: 11px; background: transparent;")
        box.addWidget(cap)
        lay.addLayout(box, 1)

        lay.addWidget(Pill(str(row.get("format") or "?"), "#4C8BF5"))
        rm = TransparentToolButton(FIF.DELETE)
        rm.setToolTip(tr("删除（尽量移入回收站）"))
        rm.clicked.connect(lambda _=False, n=row.get("name"): self._delete(n))
        lay.addWidget(rm)
        return frame

    # ------------------------------------------------------------------
    def _import(self):
        pats = tr("原理图 (*.litematic *.schem *.schematic *.nbt)")
        paths, _ = QFileDialog.getOpenFileNames(self, tr("选择原理图文件"), "", pats)
        if not paths:
            return
        try:
            added = self.backend.import_schematics(self.instance, paths, self.version)
        except Exception as e:
            InfoBar.error(tr("导入失败"), str(e), parent=self,
                          position=InfoBarPosition.TOP, duration=4000)
            return
        InfoBar.success(tr("已导入"), "、".join(added)[:200], parent=self,
                        position=InfoBarPosition.TOP, duration=3000)
        self.reload()

    def _delete(self, name: str):
        box = MessageBox(tr("删除确认"),
                         tr("将删除原理图「{name}」（会尽量移入系统回收站，可找回）。").format(name=name),
                         self)
        box.yesButton.setText(tr("删除"))
        box.cancelButton.setText(tr("取消"))
        if not box.exec():
            return
        try:
            self.backend.delete_schematic(self.instance, name, self.version)
        except Exception as e:
            InfoBar.error(tr("删除失败"), str(e), parent=self,
                          position=InfoBarPosition.TOP, duration=4000)
            return
        self.reload()

    def _open_folder(self):
        try:
            self.backend.open_schematics_folder(self.instance, self.version)
        except Exception as e:
            InfoBar.error(tr("打开失败"), str(e), parent=self,
                          position=InfoBarPosition.TOP, duration=4000)
