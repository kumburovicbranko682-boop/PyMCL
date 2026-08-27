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


def _save_details(row: dict) -> str:
    """level.dat 摘要行：模式 · 版本 · 难度 · 上次游玩。"""
    parts = []
    mode = row.get("game_mode") or ""
    if mode:
        if row.get("hardcore"):
            mode = tr("极限")
        parts.append(tr(mode) if mode in ("生存", "创造", "冒险", "旁观") else mode)
    if row.get("mc_version"):
        parts.append(str(row["mc_version"]))
    if row.get("difficulty"):
        d = str(row["difficulty"])
        parts.append(tr(d) if d in ("和平", "简单", "普通", "困难") else d)
    if row.get("cheats"):
        parts.append(tr("作弊已开"))
    last = row.get("last_played") or 0
    if last:
        from datetime import datetime
        try:
            parts.append(tr("上次游玩 {t}").format(
                t=datetime.fromtimestamp(int(last)).strftime("%Y-%m-%d %H:%M")))
        except (ValueError, OSError, OverflowError):
            pass
    return " · ".join(parts)


class WorldDatapacksDialog(MessageBoxBase):
    """世界内数据包管理（对标 HMCL 世界管理的数据包页）：查看 / 删除。

    启用状态从 level.dat 的 DataPacks 段读出（file/<名> 条目）；
    老存档没有这段就不显示状态。
    """

    def __init__(self, backend, instance: str, save_name: str, version: str = "",
                 parent=None):
        super().__init__(parent)
        self.backend = backend
        self.instance = instance
        self.save_name = save_name
        self.version = version
        self.viewLayout.addWidget(
            SubtitleLabel(tr("数据包 · {name}").format(name=save_name), self))
        self.list = ListWidget()
        self.list.setMinimumHeight(240)
        self.list.setSpacing(4)
        self.viewLayout.addWidget(self.list)
        host = QWidget(self)
        row = QHBoxLayout(host)
        row.setContentsMargins(0, 0, 0, 0)
        self.del_btn = PushButton(tr("删除所选数据包"))
        self.del_btn.clicked.connect(self._delete)
        row.addWidget(self.del_btn)
        self.viewLayout.addWidget(host)
        self.yesButton.setText(tr("关闭"))
        self.cancelButton.hide()
        self.widget.setMinimumWidth(560)
        self.reload()

    def reload(self):
        self.list.clear()
        try:
            rows = self.backend.list_world_datapacks(
                self.instance, self.save_name, self.version) or []
        except Exception as e:
            MessageBox(tr("读取失败"), str(e), self).exec()
            rows = []
        self._rows = rows
        for r in rows:
            state = r.get("enabled")
            bits = [format_size(r.get("bytes") or 0)]
            if state is True:
                bits.append(tr("已启用"))
            elif state is False:
                bits.append(tr("已禁用"))
            text = f"{r['name']}  ({' · '.join(bits)})"
            desc = (r.get("description") or "").strip()
            if desc:
                text += f"\n{desc}"
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, r["name"])
            self.list.addItem(item)
        self.del_btn.setEnabled(bool(rows))
        if not rows:
            self.list.addItem(tr("这个世界还没有数据包。可在存档列表用「把数据包装进所选存档」安装。"))

    def _selected(self) -> str:
        item = self.list.currentItem()
        return str(item.data(Qt.UserRole) or "") if item else ""

    def _delete(self):
        name = self._selected()
        if not name:
            return
        box = MessageBox(
            tr("删除数据包"),
            tr("确定删除「{name}」？（会尽量移入系统回收站，可找回）").format(name=name), self)
        if not box.exec():
            return
        try:
            self.backend.delete_world_datapack(
                self.instance, self.save_name, name, self.version)
        except Exception as e:
            MessageBox(tr("删除失败"), str(e), self).exec()
            return
        self.reload()


class WorldEditDialog(MessageBoxBase):
    """世界信息编辑（对标 HMCL 世界信息页）：名字 / 作弊 / 难度 / 难度锁 / 模式。"""

    MODES = ("生存", "创造", "冒险", "旁观", "极限")
    DIFFS = ("和平", "简单", "普通", "困难")

    def __init__(self, row: dict, parent=None):
        super().__init__(parent)
        from PySide6.QtWidgets import QFormLayout
        from qfluentwidgets import LineEdit, SwitchButton
        self.viewLayout.addWidget(SubtitleLabel(
            tr("编辑世界 · {name}").format(name=row.get("name") or "")))
        host = QWidget(self)
        form = QFormLayout(host)
        form.setContentsMargins(0, 8, 0, 0)
        self.name_edit = LineEdit()
        self.name_edit.setText(str(row.get("level_name") or row.get("name") or ""))
        form.addRow(tr("世界名"), self.name_edit)
        self.mode_box = ComboBox()
        self.mode_box.addItems([tr(m) for m in self.MODES])
        idx = row.get("game_mode_code")
        if row.get("hardcore"):
            idx = 4
        self.mode_box.setCurrentIndex(idx if isinstance(idx, int) and 0 <= idx <= 4 else 0)
        form.addRow(tr("游戏模式"), self.mode_box)
        self.diff_box = ComboBox()
        self.diff_box.addItems([tr(d) for d in self.DIFFS])
        didx = row.get("difficulty_code")
        self.diff_box.setCurrentIndex(didx if isinstance(didx, int) and 0 <= didx <= 3 else 2)
        form.addRow(tr("难度"), self.diff_box)
        self.lock_sw = SwitchButton()
        self.lock_sw.setChecked(bool(row.get("difficulty_locked")))
        form.addRow(tr("锁定难度"), self.lock_sw)
        self.cheat_sw = SwitchButton()
        self.cheat_sw.setChecked(bool(row.get("cheats")))
        form.addRow(tr("允许作弊"), self.cheat_sw)
        self.viewLayout.addWidget(host)
        self.yesButton.setText(tr("保存"))
        self.cancelButton.setText(tr("取消"))
        self.widget.setMinimumWidth(420)

    def changes(self) -> dict:
        mode_idx = self.mode_box.currentIndex()
        return {
            "level_name": self.name_edit.text().strip(),
            "game_mode": "hardcore" if mode_idx == 4 else mode_idx,
            "difficulty": self.diff_box.currentIndex(),
            "difficulty_locked": self.lock_sw.isChecked(),
            "allow_cheats": self.cheat_sw.isChecked(),
        }


class SavesDialog(MessageBoxBase):
    def __init__(self, backend, instance: str, version: str = "", parent=None):
        super().__init__(parent)
        self.backend = backend
        self.instance = instance
        self.version = version
        self.viewLayout.addWidget(SubtitleLabel(tr("存档 · {0}").format(instance), self))
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
        self.play_btn = PushButton(tr("快速进入"))
        self.play_btn.setToolTip(tr("启动游戏并直接进入所选存档（需要 1.20+）"))
        self.open_btn = PushButton(tr("打开"))
        self.del_btn = PushButton(tr("删除存档"))
        self.dp_btn = PushButton(tr("把数据包装进所选存档"))
        self.map_btn = PushButton(tr("种子地图"))
        self.map_btn.setToolTip(tr("在浏览器打开 Chunk Base 种子地图（自动带上种子与游戏版本）"))
        row.addWidget(self.play_btn)
        row.addWidget(self.open_btn)
        row.addWidget(self.del_btn)
        row.addWidget(self.dp_btn)
        row.addWidget(self.map_btn)
        row2 = QHBoxLayout()
        self.edit_btn = PushButton(tr("编辑世界"))
        self.edit_btn.setToolTip(tr("改世界名 / 游戏模式 / 难度 / 允许作弊（写入 level.dat）"))
        self.nbt_btn = PushButton(tr("NBT 编辑"))
        self.nbt_btn.setToolTip(tr("以标签树形式直接编辑 level.dat（进阶，保存前自动备份）"))
        self.backup_btn = PushButton(tr("备份存档"))
        self.restore_btn = PushButton(tr("还原备份"))
        self.export_btn = PushButton(tr("导出为 zip"))
        self.wdp_btn = PushButton(tr("存档数据包"))
        row2.addWidget(self.edit_btn)
        row2.addWidget(self.nbt_btn)
        row2.addWidget(self.backup_btn)
        row2.addWidget(self.restore_btn)
        row2.addWidget(self.export_btn)
        row2.addWidget(self.wdp_btn)
        rows.addLayout(row)
        rows.addLayout(row2)
        self.viewLayout.addWidget(host)
        self.yesButton.setText(tr("关闭"))
        self.cancelButton.hide()
        self.widget.setMinimumWidth(640)
        self.kind.currentTextChanged.connect(self.reload)
        self.play_btn.clicked.connect(self._play)
        self.open_btn.clicked.connect(self._open)
        self.del_btn.clicked.connect(self._delete)
        self.dp_btn.clicked.connect(self._datapack)
        self.edit_btn.clicked.connect(self._edit_world)
        self.nbt_btn.clicked.connect(self._nbt_edit)
        self.backup_btn.clicked.connect(self._backup)
        self.restore_btn.clicked.connect(self._restore)
        self.export_btn.clicked.connect(self._export)
        self.wdp_btn.clicked.connect(self._world_datapacks)
        self.map_btn.clicked.connect(self._seed_map)
        self.reload()

    def _set_actions(self, kind: str):
        is_save = kind == tr("存档")
        is_backup = kind == tr("备份")
        self.play_btn.setEnabled(is_save)
        self.del_btn.setEnabled(is_save or is_backup)
        self.del_btn.setText(tr("删除备份") if is_backup else tr("删除存档"))
        self.dp_btn.setEnabled(is_save)
        self.map_btn.setEnabled(is_save)
        self.edit_btn.setEnabled(is_save)
        self.nbt_btn.setEnabled(is_save)
        self.backup_btn.setEnabled(is_save)
        self.export_btn.setEnabled(is_save)
        self.wdp_btn.setEnabled(is_save)
        self.restore_btn.setEnabled(is_backup)

    def reload(self):
        self.list.clear()
        kind = self.kind.currentText()
        self._set_actions(kind)
        if kind == tr("备份"):
            for r in self.backend.list_save_backups(self.instance, "", self.version):
                self.list.addItem(f"{r['name']}  ({format_size(r.get('bytes') or 0)})")
            return
        if kind == tr("存档"):
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
                text = f"{r['name']}  ({format_size(r.get('bytes') or 0)})"
                details = _save_details(r)
                if details:
                    text += f"\n{details}"
                item = QListWidgetItem(text)
                item.setData(Qt.UserRole, r["name"])
                seed = r.get("seed") or ""
                if seed:
                    item.setToolTip(tr("种子：{seed}").format(seed=seed))
                if pix:
                    item.setIcon(QIcon(pix))
                self.list.addItem(item)
            return
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

    def _selected_name(self) -> str:
        item = self.list.currentItem()
        if not item:
            return ""
        stored = item.data(Qt.UserRole)
        if stored:
            return str(stored)
        return item.text().split("  (")[0]

    def _play(self):
        name = self._selected_name()
        if not name:
            MessageBox(tr("快速进入"), tr("请先选择存档"), self).exec()
            return
        try:
            self.backend.join_world(self.instance, name, self.version)
        except Exception as e:
            MessageBox(tr("启动失败"), str(e), self).exec()
            return
        # 关掉对话框，让用户看到启动进度
        self.accept()

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
            if MessageBox(tr("删除备份"), tr("确定删除备份「{0}」？").format(name), self).exec():
                self.backend.delete_save_backup(self.instance, name, self.version)
                self.reload()
            return
        box = MessageBox(
            tr("删除存档"),
            tr("确定删除「{name}」？（会尽量移入系统回收站，可找回）").format(name=name), self)
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
        MessageBox(tr("已开始备份"), tr("「{0}」正在打包，可到下载任务页看进度。").format(name), self).exec()

    def _restore(self):
        name = self._selected_name()
        if not name:
            return
        box = MessageBox(
            tr("还原备份"),
            tr("从「{0}」还原存档？\n若同名存档已存在，会另存为「原名-还原」，不会覆盖。").format(name),
            self,
        )
        if not box.exec():
            return
        try:
            out = self.backend.restore_save_backup(self.instance, name, self.version)
        except Exception as e:
            MessageBox(tr("还原失败"), str(e), self).exec()
            return
        MessageBox(tr("还原完成"), tr("已还原为存档「{0}」。").format(out.get('name')), self).exec()
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
        MessageBox(tr("导出完成"), tr("已导出到：\n{0}").format(out), self).exec()

    def _world_datapacks(self):
        name = self._selected_name()
        if not name:
            MessageBox(tr("未选择"), tr("请先在列表里选一个存档。"), self).exec()
            return
        WorldDatapacksDialog(self.backend, self.instance, name, self.version, self).exec()

    def _seed_map(self):
        name = self._selected_name()
        if not name:
            MessageBox(tr("未选择"), tr("请先在列表里选一个存档。"), self).exec()
            return
        try:
            url = self.backend.world_seed_map_url(self.instance, name, self.version)
        except Exception as e:
            MessageBox(tr("无法打开种子地图"), str(e), self).exec()
            return
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl(url))

    def _edit_world(self):
        name = self._selected_name()
        if not name:
            MessageBox(tr("未选择"), tr("请先在列表里选一个存档。"), self).exec()
            return
        rows = self.backend.list_saves(self.instance, self.version)
        row = next((r for r in rows if r.get("name") == name), None)
        if row is None:
            MessageBox(tr("存档不存在"), name, self).exec()
            return
        dlg = WorldEditDialog(row, self)
        if not dlg.exec():
            return
        try:
            self.backend.edit_world(self.instance, name, dlg.changes(), self.version)
        except Exception as e:
            MessageBox(tr("编辑失败"), str(e), self).exec()
            return
        self.reload()

    def _nbt_edit(self):
        name = self._selected_name()
        if not name:
            MessageBox(tr("未选择"), tr("请先在列表里选一个存档。"), self).exec()
            return
        rows = self.backend.list_saves(self.instance, self.version)
        row = next((r for r in rows if r.get("name") == name), None)
        if row is None or not row.get("path"):
            MessageBox(tr("存档不存在"), name, self).exec()
            return
        from pathlib import Path
        level = Path(row["path"]) / "level.dat"
        from .nbt_editor_dialog import NbtEditorDialog
        try:
            dlg = NbtEditorDialog(self.backend, str(level), self)
        except Exception as e:
            MessageBox(tr("读取失败"), str(e), self).exec()
            return
        if dlg.exec():
            self.reload()

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