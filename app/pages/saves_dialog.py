# -*- coding: utf-8 -*-
"""存档 / 截图 / 崩溃报告 / 日志 — 带缩略图预览。"""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QListWidgetItem, QVBoxLayout, QWidget,
)
from qfluentwidgets import (
    ComboBox, ListWidget, MessageBox, MessageBoxBase, PushButton, SubtitleLabel,
)

from mclauncher.utils import format_size
from mclauncher.i18n import tr


class SavesDialog(MessageBoxBase):
    def __init__(self, backend, instance: str, version: str = "", parent=None):
        super().__init__(parent)
        self.backend = backend
        self.instance = instance
        self.version = version
        self.viewLayout.addWidget(SubtitleLabel(f"存档 · {instance}", self))
        self.kind = ComboBox()
        self.kind.addItems([tr("存档"), tr("备份"), tr("截图"), tr("崩溃报告"), tr("日志")])
        self.list = ListWidget()
        self.list.setMinimumHeight(280)
        self.list.setIconSize(QSize(48, 48))
        self.list.setSpacing(4)
        self.viewLayout.addWidget(self.kind)
        self.viewLayout.addWidget(self.list)
        host = QWidget(self)
        rows = QVBoxLayout(host)
        rows.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.open_btn = PushButton(tr("打开"))
        self.del_btn = PushButton(tr("删除存档"))
        self.dp_btn = PushButton(tr("把数据包装进所选存档"))
        row.addWidget(self.open_btn)
        row.addWidget(self.del_btn)
        row.addWidget(self.dp_btn)
        row2 = QHBoxLayout()
        self.backup_btn = PushButton(tr("备份存档"))
        self.restore_btn = PushButton(tr("还原备份"))
        self.export_btn = PushButton(tr("导出为 zip"))
        row2.addWidget(self.backup_btn)
        row2.addWidget(self.restore_btn)
        row2.addWidget(self.export_btn)
        rows.addLayout(row)
        rows.addLayout(row2)
        self.viewLayout.addWidget(host)
        self.yesButton.setText(tr("关闭"))
        self.cancelButton.hide()
        self.widget.setMinimumWidth(640)
        self.kind.currentTextChanged.connect(self.reload)
        self.list.itemSelectionChanged.connect(self._update_buttons)
        self.open_btn.clicked.connect(self._open)
        self.del_btn.clicked.connect(self._delete)
        self.dp_btn.clicked.connect(self._datapack)
        self.backup_btn.clicked.connect(self._backup)
        self.restore_btn.clicked.connect(self._restore)
        self.export_btn.clicked.connect(self._export)
        self.reload()

    def _update_buttons(self):
        """按钮可用态 = 当前类别 + 是否选中了条目。
        以前 6 个按钮里 5 个在没选中时点了毫无反应（静默 return），
        看起来就是坏的；灰掉才是诚实的状态。"""
        kind = self.kind.currentText()
        has_sel = self.list.currentItem() is not None and bool(self._selected_name())
        is_save = kind == tr("存档")
        is_backup = kind == tr("备份")
        self.open_btn.setEnabled(has_sel)
        self.del_btn.setEnabled(has_sel and (is_save or is_backup))
        self.del_btn.setText(tr("删除备份") if is_backup else tr("删除存档"))
        self.dp_btn.setEnabled(has_sel and is_save)
        self.backup_btn.setEnabled(has_sel and is_save)
        self.export_btn.setEnabled(has_sel and is_save)
        self.restore_btn.setEnabled(has_sel and is_backup)

    def _add_empty_hint(self, kind: str):
        # 空列表不能是一片空白：占位行写清「这里为什么是空的、下一步去哪」
        hints = {
            tr("存档"): tr("还没有存档——进游戏创建一个世界后，这里就会出现"),
            tr("备份"): tr("还没有备份——切到「存档」选中一个，点「备份存档」"),
            tr("截图"): tr("还没有截图——游戏里按 F2 截图后会出现在这里"),
            tr("崩溃报告"): tr("没有崩溃报告——游戏崩溃后会出现在这里"),
            tr("日志"): tr("还没有日志——启动过游戏后会出现在这里"),
        }
        item = QListWidgetItem(hints.get(kind, tr("这里还是空的")))
        item.setFlags(Qt.NoItemFlags)
        self.list.addItem(item)

    def reload(self):
        self.list.clear()
        kind = self.kind.currentText()
        if kind == tr("备份"):
            for r in self.backend.list_save_backups(self.instance, "", self.version):
                self.list.addItem(f"{r['name']}  ({format_size(r.get('bytes') or 0)})")
        elif kind == tr("存档"):
            rows = self.backend.list_saves(self.instance, self.version)
            for r in rows:
                icon_path = r.get("icon", "")
                pix = None
                if icon_path:
                    try:
                        pix = QPixmap(icon_path)
                        if pix.isNull():
                            pix = None
                        else:
                            pix = pix.scaled(48, 48, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    except Exception:
                        pix = None
                item = QListWidgetItem(f"{r['name']}  ({format_size(r.get('bytes') or 0)})")
                if pix:
                    item.setIcon(QIcon(pix))
                self.list.addItem(item)
        else:
            mapk = {tr("截图"): "screenshots", tr("崩溃报告"): "crash-reports", tr("日志"): "logs"}
            rows = self.backend.list_media(self.instance, mapk[kind], self.version)
            for r in rows:
                path = r.get("path", "")
                if kind == tr("截图") and path:
                    try:
                        pix = QPixmap(path)
                        if not pix.isNull():
                            thumb = pix.scaled(48, 48, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                            item = QListWidgetItem(r["name"])
                            item.setIcon(QIcon(thumb))
                            self.list.addItem(item)
                            continue
                    except Exception:
                        pass
                self.list.addItem(r["name"])
        if self.list.count() == 0:
            self._add_empty_hint(kind)
        self._update_buttons()

    def _selected_name(self) -> str:
        item = self.list.currentItem()
        if not item:
            return ""
        return item.text().split("  (")[0]

    def _open(self):
        name = self._selected_name()
        kind = self.kind.currentText()
        if kind == tr("存档") and name:
            self.backend.open_save(self.instance, name, self.version)
            return
        if kind == tr("备份"):
            for r in self.backend.list_save_backups(self.instance, "", self.version):
                if r.get("name") == name:
                    try:
                        self.backend.open_media(r["path"])
                    except Exception as e:
                        MessageBox(tr("打开失败"), str(e), self).exec()
                    return
            return
        mapk = {tr("截图"): "screenshots", tr("崩溃报告"): "crash-reports", tr("日志"): "logs"}
        if kind in mapk:
            rows = self.backend.list_media(self.instance, mapk[kind], self.version)
            for r in rows:
                if r["name"] == name:
                    self.backend.open_media(r["path"])
                    return

    def _delete(self):
        name = self._selected_name()
        if not name:
            return
        if self.kind.currentText() == tr("备份"):
            if MessageBox(tr("删除备份"), f"确定删除备份「{name}」？", self).exec():
                self.backend.delete_save_backup(self.instance, name, self.version)
                self.reload()
            return
        box = MessageBox(tr("删除存档"), f"确定删除「{name}」？", self)
        if box.exec():
            self.backend.delete_save(self.instance, name, self.version)
            self.reload()

    def _backup(self):
        name = self._selected_name()
        if not name:
            MessageBox(tr("未选择"), tr("请先在列表里选一个存档。"), self).exec()
            return
        try:
            self.backend.backup_save(self.instance, name, self.version)
        except Exception as e:
            MessageBox(tr("备份失败"), str(e), self).exec()
            return
        MessageBox(tr("已开始备份"), f"「{name}」正在打包，可到下载任务页看进度。", self).exec()

    def _restore(self):
        name = self._selected_name()
        if not name:
            return
        box = MessageBox(
            tr("还原备份"),
            f"从「{name}」还原存档？\n若同名存档已存在，会另存为「原名-还原」，不会覆盖。",
            self,
        )
        if not box.exec():
            return
        try:
            out = self.backend.restore_save_backup(self.instance, name, self.version)
        except Exception as e:
            MessageBox(tr("还原失败"), str(e), self).exec()
            return
        MessageBox(tr("还原完成"), f"已还原为存档「{out.get('name')}」。", self).exec()
        self.kind.setCurrentText(tr("存档"))

    def _export(self):
        name = self._selected_name()
        if not name:
            return
        path, _ = QFileDialog.getSaveFileName(self, tr("导出存档"), f"{name}.zip", tr("压缩包 (*.zip)"))
        if not path:
            return
        try:
            out = self.backend.export_save(self.instance, name, path, self.version)
        except Exception as e:
            MessageBox(tr("导出失败"), str(e), self).exec()
            return
        MessageBox(tr("导出完成"), f"已导出到：\n{out}", self).exec()

    def _datapack(self):
        name = self._selected_name()
        if not name:
            return
        packs = self.backend.get_installed_datapacks(self.instance) or []
        if not packs:
            MessageBox(tr("没有数据包"), tr("先到下载页安装数据包。"), self).exec()
            return
        dlg = MessageBoxBase(self)
        dlg.viewLayout.addWidget(SubtitleLabel(tr("选择数据包"), dlg))
        box = ComboBox(dlg)
        box.addItems(packs)
        dlg.viewLayout.addWidget(box)
        dlg.yesButton.setText(tr("安装"))
        if dlg.exec():
            self.backend.install_datapack_into_save(self.instance, box.currentText(), name, self.version)