# -*- coding: utf-8 -*-
"""整合包导出向导（HMCL 同款）：格式 + 名称/版本/作者 + 文件勾选。"""
from PySide6.QtWidgets import QFormLayout, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel, CaptionLabel, CheckBox, ComboBox, LineEdit, MessageBoxBase,
    ScrollArea, SubtitleLabel,
)

from mclauncher.i18n import tr
from ..pcl_chrome import form_label, paint_theme_surfaces

_FORMATS = (
    ("mrpack", "Modrinth 整合包 (.mrpack)"),
    ("curseforge", "CurseForge 整合包 (.zip)"),
    ("multimc", "MultiMC / Prism 实例 (.zip)"),
)


def _fmt_size(n) -> str:
    n = int(n or 0)
    if n >= 1 << 30:
        return f"{n / (1 << 30):.1f} GB"
    if n >= 1 << 20:
        return f"{n / (1 << 20):.1f} MB"
    if n >= 1 << 10:
        return f"{n / (1 << 10):.0f} KB"
    return f"{n} B"


class ExportPackDialog(MessageBoxBase):
    """info 来自 backend.export_pack_info：{meta, items}。"""

    def __init__(self, info: dict, parent=None):
        super().__init__(parent)
        info = info or {}
        meta = info.get("meta") or {}
        items = info.get("items") or []

        self.viewLayout.addWidget(SubtitleLabel(tr("导出整合包"), self))
        hint = BodyLabel(tr("选择格式、填写信息并勾选要打包的文件。"), self)
        hint.setWordWrap(True)
        self.viewLayout.addWidget(hint)

        form_host = QWidget(self)
        form = QFormLayout(form_host)
        form.setContentsMargins(0, 8, 0, 0)

        self.fmt = ComboBox()
        for _key, label in _FORMATS:
            self.fmt.addItem(tr(label))
        form.addRow(form_label(tr("格式")), self.fmt)

        self.name = LineEdit()
        self.name.setText(str(meta.get("name") or ""))
        form.addRow(form_label(tr("名称")), self.name)
        self.version = LineEdit()
        self.version.setText(str(meta.get("version") or "1.0.0"))
        form.addRow(form_label(tr("版本号")), self.version)
        self.author = LineEdit()
        self.author.setPlaceholderText(tr("可选"))
        self.author.setText(str(meta.get("author") or ""))
        form.addRow(form_label(tr("作者")), self.author)
        self.viewLayout.addWidget(form_host)

        self.viewLayout.addWidget(CaptionLabel(tr("要打包的文件")))
        scroll = ScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFixedHeight(220)
        host = QWidget(scroll)
        lay = QVBoxLayout(host)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)
        self.boxes: list[tuple[str, CheckBox]] = []
        for item in items:
            path = str(item.get("path") or "")
            if not path:
                continue
            n = int(item.get("files") or 0)
            size = _fmt_size(item.get("bytes"))
            suffix = (tr("{n} 个文件 · {size}").format(n=n, size=size)
                      if item.get("dir") else size)
            row = QHBoxLayout()
            box = CheckBox(path)
            box.setChecked(bool(item.get("default")))
            row.addWidget(box, 1)
            row.addWidget(CaptionLabel(suffix))
            lay.addLayout(row)
            self.boxes.append((path, box))
        if not self.boxes:
            lay.addWidget(CaptionLabel(tr("实例目录里没有可打包的文件")))
        lay.addStretch(1)
        scroll.setWidget(host)
        self.viewLayout.addWidget(scroll)

        self.yesButton.setText(tr("导出"))
        self.cancelButton.setText(tr("取消"))
        self.widget.setMinimumWidth(560)
        paint_theme_surfaces(form_host, allow_transparent=False)

    def payload(self) -> dict:
        fmt = _FORMATS[max(0, self.fmt.currentIndex())][0]
        include = [path for path, box in self.boxes if box.isChecked()]
        return {
            "fmt": fmt,
            "include": include,
            "meta": {
                "name": self.name.text().strip(),
                "version": self.version.text().strip(),
                "author": self.author.text().strip(),
            },
        }
