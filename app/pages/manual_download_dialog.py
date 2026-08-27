# -*- coding: utf-8 -*-
"""整合包被禁 Mod 手动下载引导（对标 PCL2 / HMCL）。

CurseForge 上作者可以禁止第三方启动器分发文件（API 无 downloadUrl、
CDN 403）。这里列出这些 Mod 的官网文件页链接，玩家浏览器下载后
放进实例 mods 文件夹即可。
"""

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel, FluentIcon as FIF, InfoBar, InfoBarPosition, MessageBoxBase,
    PushButton, ScrollArea, SubtitleLabel, TransparentToolButton,
)

from mclauncher.i18n import tr
from ..pcl_chrome import Theme, ghost_btn_qss, row_qss


class ManualDownloadDialog(MessageBoxBase):
    def __init__(self, backend, data: dict, parent=None):
        super().__init__(parent)
        self.backend = backend
        self.instance = str((data or {}).get("instance") or "")
        items = list((data or {}).get("items") or [])

        self.viewLayout.addWidget(SubtitleLabel(tr("部分 Mod 需要手动下载"), self))
        hint = BodyLabel(
            tr("以下 Mod 的作者禁止第三方启动器下载。请打开链接用浏览器下载 jar 文件，"
               "放进实例 mods 文件夹后即可正常启动。清单也写在 mods/需要手动下载的Mod.txt。"),
            self)
        hint.setWordWrap(True)
        self.viewLayout.addWidget(hint)

        scroll = ScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("ScrollArea { background: transparent; border: none; }")
        host = QWidget()
        lay = QVBoxLayout(host)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        for m in items:
            lay.addWidget(self._row(m))
        lay.addStretch(1)
        scroll.setWidget(host)
        scroll.setMinimumSize(560, min(320, 64 * max(1, len(items)) + 16))
        self.viewLayout.addWidget(scroll, 1)

        self.yesButton.setText(tr("打开 mods 文件夹"))
        self.cancelButton.setText(tr("我知道了"))
        self.yesButton.clicked.connect(self._open_mods)
        self.widget.setMinimumWidth(620)

    def _row(self, m: dict) -> QFrame:
        row = QFrame()
        row.setObjectName("manualRow")
        row.setStyleSheet(row_qss("manualRow"))
        row.setFixedHeight(58)
        h = QHBoxLayout(row)
        h.setContentsMargins(12, 6, 12, 6)
        h.setSpacing(10)
        col = QVBoxLayout()
        col.setSpacing(2)
        name = QLabel(str(m.get("project") or m.get("filename") or "?"))
        name.setStyleSheet(
            f"color: {Theme.title}; font-size: 13px; font-weight: 600; background: transparent;")
        col.addWidget(name)
        fn = QLabel(str(m.get("filename") or ""))
        fn.setStyleSheet(f"color: {Theme.muted}; font-size: 11px; background: transparent;")
        col.addWidget(fn)
        h.addLayout(col, 1)
        url = str(m.get("url") or "")
        copy_btn = TransparentToolButton(FIF.COPY)
        copy_btn.setToolTip(tr("复制链接"))
        copy_btn.clicked.connect(lambda _=False, u=url: self._copy(u))
        h.addWidget(copy_btn)
        open_btn = PushButton(tr("打开链接"))
        open_btn.setFixedSize(88, 30)
        open_btn.setStyleSheet(ghost_btn_qss())
        open_btn.clicked.connect(lambda _=False, u=url: QDesktopServices.openUrl(QUrl(u)))
        h.addWidget(open_btn)
        return row

    def _copy(self, url: str):
        from PySide6.QtGui import QGuiApplication
        QGuiApplication.clipboard().setText(url)
        InfoBar.success(tr("已复制"), url[:80], parent=self,
                        position=InfoBarPosition.TOP, duration=1800)

    def _open_mods(self):
        try:
            self.backend.open_version_folder(self.instance, "", "mods")
        except Exception as e:
            InfoBar.error(tr("无法打开"), str(e), parent=self,
                          position=InfoBarPosition.TOP, duration=3500)
