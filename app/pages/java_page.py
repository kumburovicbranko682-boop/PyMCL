# -*- coding: utf-8 -*-
"""Java 页：环境卡片 + 发行版选择 + 版本下载磁贴。"""

from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    CaptionLabel, ComboBox, FluentIcon as FIF, InfoBar, InfoBarPosition,
    PushButton, SimpleCardWidget, StrongBodyLabel, SubtitleLabel,
    TransparentPushButton,
)

from ..widgets import EmptyState, IconTile, Pill
from mclauncher.i18n import tr


class JavaCard(SimpleCardWidget):
    def __init__(self, info: dict, parent=None):
        super().__init__(parent)
        self.setFixedHeight(76)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(14)

        layout.addWidget(IconTile("J", "#E8862E", size=46))
        info_box = QVBoxLayout()
        info_box.setSpacing(2)
        title_row = QHBoxLayout()
        title_row.addWidget(StrongBodyLabel(f'Java {info["major"]}'))
        title_row.addWidget(Pill(tr("可用"), "#2FA36B"))
        title_row.addStretch(1)
        info_box.addLayout(title_row)
        info_box.addWidget(CaptionLabel(info.get("path") or info.get("name") or ""))
        layout.addLayout(info_box, 1)


class JavaDownloadTile(SimpleCardWidget):
    def __init__(self, major: str, note: str, on_download, parent=None):
        super().__init__(parent)
        # 定宽 150 装不下英文说明（会被硬裁）；改成最小宽，按文字自适应
        self.setMinimumWidth(150)
        self.setFixedHeight(128)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(4)
        title = StrongBodyLabel(f"Java {major}")
        title.setStyleSheet("font-size: 16px;")
        layout.addWidget(title)
        layout.addWidget(CaptionLabel(note))
        layout.addStretch(1)
        btn = PushButton(FIF.DOWNLOAD, tr("下载"))
        btn.setFixedHeight(30)
        btn.clicked.connect(lambda: on_download(major, self))
        layout.addWidget(btn)


class JavaPage(QWidget):
    # 存原文、展示时才 tr：类属性里的 tr() 在 import 那一刻就定死语言
    NOTES = {
        "8": "1.16 及以下旧版本",
        "11": "部分旧模组环境",
        "17": "1.18 – 1.20.4 推荐",
        "21": "1.20.5 – 1.21.11",
        "25": "26.1+ 需要 Java 25",
    }

    def __init__(self, backend, parent=None):
        super().__init__(parent)
        self.setObjectName("javaPage")
        self.backend = backend
        self._vendors = []

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 20, 28, 20)
        root.setSpacing(14)

        head = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.addWidget(SubtitleLabel("Java"))
        title_box.addWidget(CaptionLabel(tr("Minecraft 所需 Java 会在启动时自动匹配下载；也可在实例页为每个实例单独指定")))
        head.addLayout(title_box, 1)
        self.refresh_btn = TransparentPushButton(FIF.SYNC, tr("重新检测"))
        head.addWidget(self.refresh_btn, 0)
        root.addLayout(head)

        root.addWidget(StrongBodyLabel(tr("本机环境")))
        self.env_layout = QVBoxLayout()
        self.env_layout.setSpacing(10)
        root.addLayout(self.env_layout)

        root.addSpacing(6)
        dl_head = QHBoxLayout()
        dl_head.addWidget(StrongBodyLabel(tr("下载新运行时")))
        dl_head.addStretch(1)
        dl_head.addWidget(CaptionLabel(tr("发行版")))
        self.vendor_box = ComboBox()
        self.vendor_box.setMinimumWidth(160)
        self._reload_vendors()
        dl_head.addWidget(self.vendor_box)
        root.addLayout(dl_head)

        tiles = QHBoxLayout()
        tiles.setSpacing(12)
        for major in ("8", "11", "17", "21", "25"):
            tiles.addWidget(JavaDownloadTile(major, tr(self.NOTES[major]), self._download))
        tiles.addStretch(1)
        root.addLayout(tiles)
        root.addStretch(1)

        self.refresh_btn.clicked.connect(lambda: self.reload(scan_system=True))
        self.reload(scan_system=False)

    def _reload_vendors(self):
        self.vendor_box.blockSignals(True)
        self.vendor_box.clear()
        self._vendors = []
        getter = getattr(self.backend, "java_vendor_list", None)
        labeler = getattr(self.backend, "java_vendor_label", None)
        vendors = list(getter() if callable(getter) else ["adoptium", "zulu", "microsoft"])
        if not vendors:
            vendors = ["adoptium"]
        for v in vendors:
            label = labeler(v) if callable(labeler) else v
            self._vendors.append(v)
            self.vendor_box.addItem(str(label or v))
        # 默认 Adoptium
        if "adoptium" in self._vendors:
            self.vendor_box.setCurrentIndex(self._vendors.index("adoptium"))
        elif self._vendors:
            self.vendor_box.setCurrentIndex(0)
        self.vendor_box.blockSignals(False)

    def _selected_vendor(self) -> str:
        idx = self.vendor_box.currentIndex()
        if 0 <= idx < len(self._vendors):
            return self._vendors[idx]
        return "adoptium"

    def reload(self, scan_system: bool = False):
        local = self.backend.get_java_list(scan_system=False)
        self._fill(local)
        if not scan_system:
            return
        call_async = getattr(self.backend, "call_async", None)
        if callable(call_async):
            call_async(
                lambda: self.backend.get_java_list(True),
                self._fill,
                self._on_scan_err,
            )
            return
        try:
            self._fill(self.backend.get_java_list(scan_system=True))
        except Exception as exc:
            self._on_scan_err(exc)

    def _on_scan_err(self, err):
        InfoBar.error(
            tr("扫描 Java 失败"),
            str(err or tr("未知错误")),
            parent=self,
            position=InfoBarPosition.TOP,
            duration=4000,
        )

    def _fill(self, javas):
        while self.env_layout.count():
            item = self.env_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        javas = list(javas or [])
        if not javas:
            self.env_layout.addWidget(EmptyState(FIF.CODE, tr("未检测到 Java，请从下方下载")))
            return
        for j in javas:
            self.env_layout.addWidget(JavaCard(j))

    def _download(self, major: str, source=None):
        win = self.window()
        if source is not None and hasattr(win, "fly_to_tasks"):
            win.fly_to_tasks(source, "J", "#E8862E")
        vendor = self._selected_vendor()
        self.backend.download_java(major, vendor=vendor)
