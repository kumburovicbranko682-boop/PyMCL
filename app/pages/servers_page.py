# -*- coding: utf-8 -*-
"""服务器列表管理页。"""
from __future__ import annotations

import re
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog, QFrame, QHBoxLayout, QHeaderView, QStackedWidget, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)
from qfluentwidgets import (
    BodyLabel, ComboBox, FluentIcon as FIF, InfoBar, LineEdit, MessageBox,
    MessageBoxBase, PushButton, StrongBodyLabel, SubtitleLabel,
    TransparentPushButton,
)

from mclauncher.config import CONFIG
from ..pcl_chrome import Theme
from ..widgets import EmptyState
from mclauncher.i18n import tr

# 整条链都得用 getattr：当前 qfluentwidgets 已经没有 FIF.WORLD，
# 写成裸属性的话一旦 GLOBE 也被改名，就是 import 期 AttributeError，整个启动器起不来。
_GLOBE_ICON = (getattr(FIF, "GLOBE", None) or getattr(FIF, "WORLD", None)
               or FIF.CLOUD_DOWNLOAD)

_PORT_RE = re.compile(r"^\d{1,5}$")


def parse_server_address(text: str) -> tuple[str, int]:
    """"host" 或 "host:port" → (host, port)。端口缺省/非法时用 25565。"""
    text = (text or "").strip()
    host, port = text, 25565
    if ":" in text:
        head, tail = text.rsplit(":", 1)
        if _PORT_RE.match(tail) and 0 < int(tail) <= 65535:
            host, port = head.strip(), int(tail)
    return host, port


class _ServerDialog(MessageBoxBase):
    """一个框填完：名称（可选）+ 地址（可带 :端口）。

    以前添加要连过 3 个模态框（名称 → 地址 → 端口），端口几乎总是默认
    25565 也必须单独点一次；编辑则要连过 2 个还会把端口丢回默认。
    """

    def __init__(self, title: str, name: str = "", address: str = "", parent=None):
        super().__init__(parent)
        self.viewLayout.addWidget(SubtitleLabel(title, self))
        self.name_edit = LineEdit(self)
        self.name_edit.setPlaceholderText(tr("名称（可选）"))
        self.name_edit.setText(name)
        self.addr_edit = LineEdit(self)
        self.addr_edit.setPlaceholderText(tr("example.com 或 IP，可带 :端口"))
        self.addr_edit.setText(address)
        self.viewLayout.addWidget(BodyLabel(tr("名称"), self))
        self.viewLayout.addWidget(self.name_edit)
        self.viewLayout.addWidget(BodyLabel(tr("地址"), self))
        self.viewLayout.addWidget(self.addr_edit)
        self.yesButton.setText(tr("保存"))
        self.cancelButton.setText(tr("取消"))
        self.widget.setMinimumWidth(420)

    def values(self) -> tuple[str, str, int]:
        host, port = parse_server_address(self.addr_edit.text())
        return self.name_edit.text().strip(), host, port


class ServerPage(QWidget):
    """服务器管理页面。"""

    def __init__(self, backend, parent=None):
        super().__init__(parent)
        self.backend = backend
        self._instance = ""
        self._servers = []

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 顶栏
        self.topBar = QFrame()
        self.topBar.setObjectName("topBar")
        self.topBar.setStyleSheet(f"#topBar {{ background: {Theme.card}; border-bottom: 1px solid {Theme.line}; }}")
        self.topBar.setFixedHeight(56)
        tl = QHBoxLayout(self.topBar)
        tl.setContentsMargins(24, 0, 24, 0)
        self._title_lab = StrongBodyLabel(tr("服务器列表"))
        self._title_lab.setStyleSheet(f"color: {Theme.text}; font-size: 18px;")
        tl.addWidget(self._title_lab)
        tl.addSpacing(16)
        tl.addWidget(BodyLabel(tr("实例")))
        self.instance_box = ComboBox()
        self.instance_box.setFixedWidth(160)
        self.instance_box.currentTextChanged.connect(self._on_instance_changed)
        tl.addWidget(self.instance_box)
        tl.addStretch(1)
        add_btn = PushButton(tr("添加服务器"))
        add_btn.setIcon(FIF.ADD)
        add_btn.clicked.connect(self._on_add)
        tl.addWidget(add_btn)
        import_btn = PushButton(tr("导入"))
        import_btn.clicked.connect(self._on_import)
        tl.addWidget(import_btn)
        export_btn = PushButton(tr("导出"))
        export_btn.clicked.connect(self._on_export)
        tl.addWidget(export_btn)
        root.addWidget(self.topBar)

        # 表格
        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels([tr("名称"), tr("地址"), tr("端口"), tr("描述"), tr("操作")])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setStyleSheet(
            f"QTableWidget {{ background: {Theme.card}; border: none; gridline-color: {Theme.line}; }}"
            f"QTableWidget::item {{ padding: 8px 12px; }}"
            f"QHeaderView::section {{ background: {Theme.card}; color: {Theme.muted};"
            " border: none; border-bottom: 1px solid "
            f"{Theme.line}; font-weight: 600; padding: 8px; }}"
        )
        self.empty = EmptyState(_GLOBE_ICON, tr("没有可用的服务器\n点击「添加服务器」开始添加"))
        # 表格和空状态占同一格：以前空状态是单独一行且 stretch=0，
        # 表格隐藏后它只能挤在页面底部一条，不居中。
        self._body = QStackedWidget()
        self._body.addWidget(self.table)
        self._body.addWidget(self.empty)
        root.addWidget(self._body, 1)

    def _fill_instances(self, prefer: str = ""):
        cur = self.instance_box.currentText()
        self.instance_box.blockSignals(True)
        self.instance_box.clear()
        names = [i["name"] for i in self.backend.get_instances()]
        self.instance_box.addItems(names)
        pick = prefer or cur or CONFIG.get("default_instance") or (names[0] if names else "")
        if pick in names:
            self.instance_box.setCurrentText(pick)
        self.instance_box.blockSignals(False)

    def _on_instance_changed(self, name: str):
        self.reload(name)

    def reload(self, instance: str = ""):
        self._fill_instances(instance)
        self._instance = self.instance_box.currentText() or instance or ""
        try:
            self._servers = self.backend.list_servers(self._instance)
        except Exception:
            self._servers = []
        self._render()

    def restyle(self):
        """主题切换时刷新一次性样式。"""
        self.topBar.setStyleSheet(f"#topBar {{ background: {Theme.card}; border-bottom: 1px solid {Theme.line}; }}")
        self._title_lab.setStyleSheet(f"color: {Theme.text}; font-size: 18px;")
        self.table.setStyleSheet(
            f"QTableWidget {{ background: {Theme.card}; border: none; gridline-color: {Theme.line}; }}"
            f"QTableWidget::item {{ padding: 8px 12px; }}"
            f"QHeaderView::section {{ background: {Theme.card}; color: {Theme.muted};"
            " border: none; border-bottom: 1px solid "
            f"{Theme.line}; font-weight: 600; padding: 8px; }}"
        )
        if hasattr(self.empty, "restyle") and self.empty.isVisible():
            self.empty.restyle()

    def _render(self):
        self.table.setRowCount(0)
        if not self._servers:
            self._body.setCurrentWidget(self.empty)
            return
        self._body.setCurrentWidget(self.table)
        self.table.setRowCount(len(self._servers))
        for i, s in enumerate(self._servers):
            self.table.setItem(i, 0, QTableWidgetItem(s.get("name", "?")))
            self.table.setItem(i, 1, QTableWidgetItem(s.get("ip", "")))
            self.table.setItem(i, 2, QTableWidgetItem(str(s.get("port", 25565))))
            desc = (s.get("description", "") or "")[:40]
            self.table.setItem(i, 3, QTableWidgetItem(desc))
            btn_w = QWidget()
            btn_l = QHBoxLayout(btn_w)
            btn_l.setContentsMargins(4, 2, 4, 2)
            # 不再 setFixedWidth(40)：两个汉字加内边距根本放不下，会被省略号截成「编…」，
            # 切英文后 Edit/Delete 更放不下。让按钮按自身 sizeHint 走，最后一列本来就 stretch。
            edit_b = TransparentPushButton(tr("编辑"))
            edit_b.clicked.connect(lambda checked, idx=i: self._on_edit(idx))
            del_b = TransparentPushButton(tr("删除"))
            del_b.clicked.connect(lambda checked, idx=i: self._on_delete(idx))
            btn_l.addWidget(edit_b)
            btn_l.addWidget(del_b)
            self.table.setCellWidget(i, 4, btn_w)

    def _on_add(self):
        dlg = _ServerDialog(tr("添加服务器"), parent=self)
        if not dlg.exec():
            return
        name, ip, port = dlg.values()
        if not ip:
            InfoBar.error(tr("缺少地址"), tr("请填写服务器地址"), duration=3000, parent=self)
            return
        try:
            self.backend.add_server(self._instance, name, ip, port)
            InfoBar.success(tr("已添加"), tr("服务器 {0} 已添加").format(name or ip),
                            duration=2000, parent=self)
            self.reload(self._instance)
        except Exception as e:
            InfoBar.error(tr("添加失败"), str(e), duration=3000, parent=self)

    def _on_edit(self, index: int):
        s = self._servers[index]
        port0 = int(s.get("port", 25565) or 25565)
        addr = s.get("ip", "") if port0 == 25565 else f"{s.get('ip', '')}:{port0}"
        dlg = _ServerDialog(tr("编辑服务器"), name=s.get("name", ""), address=addr, parent=self)
        if not dlg.exec():
            return
        name, ip, port = dlg.values()
        if not ip:
            InfoBar.error(tr("缺少地址"), tr("请填写服务器地址"), duration=3000, parent=self)
            return
        try:
            self.backend.update_server(self._instance, index, name=name, ip=ip, port=port)
            InfoBar.success(tr("已更新"), tr("服务器已更新"), duration=2000, parent=self)
            self.reload(self._instance)
        except Exception as e:
            InfoBar.error(tr("更新失败"), str(e), duration=3000, parent=self)

    def _on_delete(self, index: int):
        s = self._servers[index]
        box = MessageBox(tr("确认删除"), tr("删除服务器 {0}？").format(s.get("name", "?")), self)
        if box.exec():
            try:
                self.backend.delete_server(self._instance, index)
                InfoBar.success(tr("已删除"), "", duration=2000, parent=self)
                self.reload(self._instance)
            except Exception as e:
                InfoBar.error(tr("删除失败"), str(e), duration=3000, parent=self)

    def _on_import(self):
        text, ok = QFileDialog.getOpenFileName(self, tr("选择导入文件"), "", tr("文本文件 (*.txt);;JSON (*.json)"))
        if not ok or not text:
            return
        try:
            with open(text, "r", encoding="utf-8") as f:
                data = f.read()
            n = self.backend.import_servers(self._instance, data)
            InfoBar.success(tr("导入完成"), tr("已导入 {0} 个服务器").format(n), duration=2000, parent=self)
            self.reload(self._instance)
        except Exception as e:
            InfoBar.error(tr("导入失败"), str(e), duration=3000, parent=self)

    def _on_export(self):
        try:
            text = self.backend.export_servers(self._instance)
        except Exception as e:
            InfoBar.error(tr("导出失败"), str(e), duration=3000, parent=self)
            return
        path, _ = QFileDialog.getSaveFileName(self, tr("导出服务器"), "servers.txt", tr("文本文件 (*.txt)"))
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            InfoBar.success(tr("已导出"), tr("已保存到 {0}").format(path), duration=2000, parent=self)
        except Exception as e:
            InfoBar.error(tr("导出失败"), str(e), duration=3000, parent=self)