# -*- coding: utf-8 -*-
"""服务器列表管理页：增删改查 + 实时状态（延迟 / 人数 / MOTD / 图标）。"""
from __future__ import annotations

import base64
import re
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (
    QFileDialog, QFrame, QHBoxLayout, QHeaderView, QStackedWidget, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)
from qfluentwidgets import (
    BodyLabel, ComboBox, FluentIcon as FIF, InfoBar, MessageBox, PushButton,
    StrongBodyLabel, TransparentPushButton,
)

from mclauncher.config import CONFIG
from ..pcl_chrome import Theme
from ..widgets import InputDialog, EmptyState
from mclauncher.i18n import tr

# 整条链都得用 getattr：当前 qfluentwidgets 已经没有 FIF.WORLD，
# 写成裸属性的话一旦 GLOBE 也被改名，就是 import 期 AttributeError，整个启动器起不来。
_GLOBE_ICON = (getattr(FIF, "GLOBE", None) or getattr(FIF, "WORLD", None)
               or FIF.CLOUD_DOWNLOAD)

_PORT_RE = re.compile(r"^\d{1,5}$")


class ServerPage(QWidget):
    """服务器管理页面。"""

    def __init__(self, backend, parent=None):
        super().__init__(parent)
        self.backend = backend
        self._instance = ""
        self._servers = []
        self._ping_token = 0
        self._ping_pending = 0

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
        self.ping_btn = PushButton(tr("刷新状态"))
        self.ping_btn.setIcon(FIF.SYNC)
        self.ping_btn.clicked.connect(self._ping_all)
        tl.addWidget(self.ping_btn)
        import_btn = PushButton(tr("导入"))
        import_btn.clicked.connect(self._on_import)
        tl.addWidget(import_btn)
        export_btn = PushButton(tr("导出"))
        export_btn.clicked.connect(self._on_export)
        tl.addWidget(export_btn)
        root.addWidget(self.topBar)

        # 表格
        self.table = QTableWidget()
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels(
            [tr("名称"), tr("状态"), tr("地址"), tr("端口"), tr("描述"), tr("操作")])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
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
            self.table.setItem(i, 1, QTableWidgetItem(tr("查询中…")))
            self.table.setItem(i, 2, QTableWidgetItem(s.get("ip", "")))
            self.table.setItem(i, 3, QTableWidgetItem(str(s.get("port", 25565))))
            desc = (s.get("description", "") or "")[:40]
            self.table.setItem(i, 4, QTableWidgetItem(desc))
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
            self.table.setCellWidget(i, 5, btn_w)
        self._ping_all()

    # ---- 服务器状态查询

    def _ping_all(self):
        if not self._servers:
            return
        self._ping_token += 1
        token = self._ping_token
        self._ping_pending = len(self._servers)
        self.ping_btn.setEnabled(False)
        self.ping_btn.setText(tr("查询中…"))
        for i, s in enumerate(self._servers):
            host, port = s.get("ip", ""), s.get("port", 25565)
            self.backend.call_async(
                lambda h=host, p=port: self.backend.ping_server(h, p),
                lambda status, row=i, tok=token: self._on_ping(tok, row, status),
                lambda msg, row=i, tok=token: self._on_ping(
                    tok, row, {"online": False, "error": str(msg)}),
            )

    def _ping_done_one(self):
        self._ping_pending -= 1
        if self._ping_pending <= 0:
            self.ping_btn.setEnabled(True)
            self.ping_btn.setText(tr("刷新状态"))

    def _on_ping(self, token: int, row: int, status: dict):
        if token != self._ping_token:
            return
        self._ping_done_one()
        if row >= self.table.rowCount():
            return
        item = self.table.item(row, 1)
        if item is None:
            return
        if status.get("online"):
            online = status.get("players_online", 0)
            maxp = status.get("players_max", 0)
            item.setText(f"● {status.get('latency_ms', 0)}ms  {online}/{maxp}")
            item.setForeground(QColor("#2E9B6B"))
            tip_lines = []
            if status.get("motd"):
                tip_lines.append(status["motd"])
            if status.get("version"):
                tip_lines.append(tr("版本: {v}").format(v=status["version"]))
            if status.get("sample"):
                tip_lines.append(tr("在线: {names}").format(
                    names=", ".join(status["sample"][:8])))
            item.setToolTip("\n".join(tip_lines))
            self._set_favicon(row, status.get("favicon") or "")
        else:
            item.setText(tr("离线"))
            item.setForeground(QColor("#D64545"))
            item.setToolTip(status.get("error") or "")

    def _set_favicon(self, row: int, favicon: str):
        """favicon 是 data:image/png;base64,… 的 data URI。"""
        if not favicon.startswith("data:image") or "," not in favicon:
            return
        try:
            raw = base64.b64decode(favicon.split(",", 1)[1])
        except (ValueError, TypeError):
            return
        pix = QPixmap()
        if pix.loadFromData(raw):
            name_item = self.table.item(row, 0)
            if name_item is not None:
                name_item.setIcon(QIcon(pix))

    def _on_add(self):
        dlg = InputDialog(tr("添加服务器"), tr("服务器名称"), placeholder=tr("可选"))
        if not dlg.exec():
            return
        name = dlg.value()
        ip_dlg = InputDialog(tr("添加服务器"), tr("服务器地址"), placeholder=tr("example.com 或 IP"))
        if not ip_dlg.exec():
            return
        ip = ip_dlg.value()
        port_dlg = InputDialog(tr("添加服务器"), tr("端口"), text="25565", placeholder=tr("默认 25565"))
        if not port_dlg.exec():
            return
        port_text = port_dlg.value()
        port = int(port_text) if _PORT_RE.match(port_text) else 25565
        try:
            self.backend.add_server(self._instance, name, ip, port)
            InfoBar.success(tr("已添加"), f"服务器 {name or ip} 已添加", duration=2000, parent=self)
            self.reload(self._instance)
        except Exception as e:
            InfoBar.error(tr("添加失败"), str(e), duration=3000, parent=self)

    def _on_edit(self, index: int):
        s = self._servers[index]
        dlg = InputDialog(tr("编辑服务器"), tr("服务器名称"), text=s.get("name", ""))
        if not dlg.exec():
            return
        name = dlg.value()
        ip_dlg = InputDialog(tr("编辑服务器"), tr("服务器地址"), text=s.get("ip", ""))
        if not ip_dlg.exec():
            return
        ip = ip_dlg.value()
        try:
            self.backend.update_server(self._instance, index, name=name, ip=ip,
                                       port=s.get("port", 25565))
            InfoBar.success(tr("已更新"), f"服务器已更新", duration=2000, parent=self)
            self.reload(self._instance)
        except Exception as e:
            InfoBar.error(tr("更新失败"), str(e), duration=3000, parent=self)

    def _on_delete(self, index: int):
        s = self._servers[index]
        box = MessageBox(tr("确认删除"), f"删除服务器 {s.get('name', '?')}？", self)
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
            InfoBar.success(tr("导入完成"), f"已导入 {n} 个服务器", duration=2000, parent=self)
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
            InfoBar.success(tr("已导出"), f"已保存到 {path}", duration=2000, parent=self)
        except Exception as e:
            InfoBar.error(tr("导出失败"), str(e), duration=3000, parent=self)