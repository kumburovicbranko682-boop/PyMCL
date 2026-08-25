# -*- coding: utf-8 -*-
"""服务器列表管理页。"""
from __future__ import annotations

import base64
import re
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QPixmap
from PySide6.QtWidgets import (
    QFileDialog, QFrame, QHBoxLayout, QHeaderView, QStackedWidget, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)
from qfluentwidgets import (
    BodyLabel, CaptionLabel, ComboBox, FluentIcon as FIF, InfoBar, MessageBox,
    MessageBoxBase, PushButton, StrongBodyLabel, SubtitleLabel,
    TransparentPushButton,
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


def _server_icon(b64: str):
    """servers.dat icon 字段（纯 base64 PNG）→ QIcon；坏数据返回 None。"""
    if not b64:
        return None
    try:
        raw = base64.b64decode(str(b64), validate=True)
    except Exception:
        return None
    pix = QPixmap()
    if not pix.loadFromData(raw) or pix.isNull():
        return None
    return QIcon(pix.scaled(24, 24, Qt.KeepAspectRatio, Qt.SmoothTransformation))


class _LanWorldsDialog(MessageBoxBase):
    """局域网扫描结果：复制地址发朋友，或填到启动页「直连服务器」。"""

    def __init__(self, worlds: list, parent=None):
        super().__init__(parent)
        self.viewLayout.addWidget(SubtitleLabel(tr("局域网世界"), self))
        if not worlds:
            self.viewLayout.addWidget(BodyLabel(tr("没有发现开放的局域网世界。"), self))
            hint = CaptionLabel(
                tr("请让房主进入世界后按 Esc →「对局域网开放」，并确认双方连着同一"
                   "路由器、防火墙放行了 Java。"), self)
            hint.setWordWrap(True)
            hint.setStyleSheet(f"color: {Theme.muted};")
            self.viewLayout.addWidget(hint)
        else:
            tip = CaptionLabel(
                tr("把地址发给同一局域网的朋友，或复制到启动页的「直连服务器」。"), self)
            tip.setWordWrap(True)
            tip.setStyleSheet(f"color: {Theme.muted};")
            self.viewLayout.addWidget(tip)
            for w in worlds:
                row = QHBoxLayout()
                label = BodyLabel(f"{w.get('motd') or '?'}  ·  {w.get('address') or ''}", self)
                label.setWordWrap(True)
                copy_b = TransparentPushButton(FIF.COPY, tr("复制地址"))
                copy_b.clicked.connect(
                    lambda _, a=w.get("address", ""), b=None: self._copy(a))
                row.addWidget(label, 1)
                row.addWidget(copy_b)
                self.viewLayout.addLayout(row)
        self.yesButton.setText(tr("知道了"))
        self.cancelButton.hide()
        self.widget.setMinimumWidth(460)

    def _copy(self, addr: str):
        QGuiApplication.clipboard().setText(addr)
        btn = self.sender()
        if btn is not None:
            btn.setText(tr("已复制"))


class ServerPage(QWidget):
    """服务器管理页面。"""

    def __init__(self, backend, parent=None):
        super().__init__(parent)
        self.backend = backend
        self._instance = ""
        self._servers = []
        # 状态刷新令牌：reload 后旧的 ping 回调直接作废，避免写错行
        self._status_token = 0
        self._status_pending = 0

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
        self.status_btn = PushButton(tr("刷新状态"))
        self.status_btn.setIcon(FIF.SYNC)
        self.status_btn.setToolTip(tr("查询每个服务器的在线状态、延迟与在线人数"))
        self.status_btn.clicked.connect(self._refresh_status)
        tl.addWidget(self.status_btn)
        self.lan_btn = PushButton(tr("发现局域网"))
        self.lan_btn.setIcon(FIF.WIFI)
        self.lan_btn.setToolTip(tr("扫描本局域网里「对局域网开放」的世界"))
        self.lan_btn.clicked.connect(self._discover_lan)
        tl.addWidget(self.lan_btn)
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
            [tr("名称"), tr("地址"), tr("端口"), tr("状态"), tr("描述"), tr("操作")])
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
        # 表格重建后，旧的状态查询回调不能再往单元格里写
        self._status_token += 1
        self._status_pending = 0
        self.status_btn.setEnabled(bool(self._servers))
        self.status_btn.setText(tr("刷新状态"))
        self.table.setRowCount(0)
        if not self._servers:
            self._body.setCurrentWidget(self.empty)
            return
        self._body.setCurrentWidget(self.table)
        self.table.setRowCount(len(self._servers))
        for i, s in enumerate(self._servers):
            name_item = QTableWidgetItem(s.get("name", "?"))
            icon = _server_icon(s.get("icon", ""))
            if icon:
                name_item.setIcon(icon)
            self.table.setItem(i, 0, name_item)
            self.table.setItem(i, 1, QTableWidgetItem(s.get("ip", "")))
            self.table.setItem(i, 2, QTableWidgetItem(str(s.get("port", 25565))))
            self.table.setItem(i, 3, QTableWidgetItem("—"))
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

    # ------------------------------------------------------------ 局域网发现

    def _discover_lan(self):
        if getattr(self, "_lan_scanning", False):
            return
        self._lan_scanning = True
        self.lan_btn.setEnabled(False)
        self.lan_btn.setText(tr("扫描中…"))

        def _reset():
            self._lan_scanning = False
            self.lan_btn.setEnabled(True)
            self.lan_btn.setText(tr("发现局域网"))

        def done(worlds):
            _reset()
            _LanWorldsDialog(worlds or [], self.window()).exec()

        def err(msg):
            _reset()
            InfoBar.error(tr("扫描失败"), str(msg), duration=3000, parent=self)

        self.backend.call_async(
            lambda: self.backend.discover_lan_worlds(3.0), done, err)

    # ------------------------------------------------------------ 状态查询

    def _refresh_status(self):
        """并发查询所有服务器的在线状态（Server List Ping）。"""
        if not self._servers or self._status_pending:
            return
        self._status_token += 1
        token = self._status_token
        self._status_pending = len(self._servers)
        self.status_btn.setEnabled(False)
        self.status_btn.setText(tr("查询中…"))
        inst = self._instance
        for i, s in enumerate(self._servers):
            self._set_status_cell(i, QTableWidgetItem(tr("查询中…")))
            # ping_listed_server 会顺手把 favicon 写回 servers.dat 的 icon 字段，
            # 游戏多人列表首开就有图标
            self.backend.call_async(
                lambda idx=i: self.backend.ping_listed_server(inst, idx),
                lambda result, row=i: self._on_status(token, row, result),
                lambda err, row=i: self._on_status(token, row, {"online": False, "error": str(err)}),
            )

    def _set_status_cell(self, row: int, item: QTableWidgetItem):
        if 0 <= row < self.table.rowCount():
            self.table.setItem(row, 3, item)

    def _on_status(self, token: int, row: int, result: dict):
        if token != self._status_token:
            return
        self._status_pending -= 1
        if self._status_pending <= 0:
            self._status_pending = 0
            self.status_btn.setEnabled(True)
            self.status_btn.setText(tr("刷新状态"))
        result = result or {}
        if result.get("online"):
            text = f"{result.get('latency_ms', 0)}ms · {result.get('players_online', 0)}/{result.get('players_max', 0)}"
            item = QTableWidgetItem(text)
            item.setForeground(QColor("#2FA36B"))
            tip_parts = []
            if result.get("motd"):
                tip_parts.append(str(result["motd"]))
            if result.get("version"):
                tip_parts.append(tr("版本: {v}").format(v=result["version"]))
            names = [p.get("name", "") for p in result.get("players_sample") or [] if p.get("name")]
            if names:
                tip_parts.append(tr("在线玩家: {names}").format(names=", ".join(names[:12])))
            if tip_parts:
                item.setToolTip("\n".join(tip_parts))
        else:
            item = QTableWidgetItem(tr("离线"))
            item.setForeground(QColor("#D84A4A"))
            if result.get("error"):
                item.setToolTip(str(result["error"]))
        self._set_status_cell(row, item)
        # 拿到 favicon 就地更新名称列的图标（后端已写回 servers.dat）
        icon = _server_icon(result.get("icon") or "")
        if icon and 0 <= row < self.table.rowCount():
            name_item = self.table.item(row, 0)
            if name_item is not None:
                name_item.setIcon(icon)

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