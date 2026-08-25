# -*- coding: utf-8 -*-
"""实例页：实例卡片网格 + 新建实例入口卡。"""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    CaptionLabel, FluentIcon as FIF, MessageBox, PushButton, ScrollArea,
    SimpleCardWidget, StrongBodyLabel, SubtitleLabel, TransparentToolButton,
)

from mclauncher.config import CONFIG
from ..widgets import ComboDialog, IconTile, InputDialog, Pill, grid_columns
from mclauncher.i18n import tr


class InstanceCard(SimpleCardWidget):
    def __init__(self, info: dict, page, parent=None):
        super().__init__(parent)
        self.info = info
        self.page = page
        self.setFixedSize(240, 138)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(6)

        top = QHBoxLayout()
        top.addWidget(IconTile(info["name"], size=40))
        name_box = QVBoxLayout()
        name_box.setSpacing(2)
        name_box.addWidget(StrongBodyLabel(info["name"]))
        name_box.addWidget(CaptionLabel(f'{info["versions"]} 个版本'))
        top.addLayout(name_box, 1)
        if info["name"] == CONFIG.get("default_instance"):
            top.addWidget(Pill(tr("默认"), "#4C8BF5"))
        elif info.get("external"):
            top.addWidget(Pill(tr("外部目录"), "#9C6ADE"))
        else:
            top.addWidget(Pill(tr("实例"), "#4C8BF5"))
        if info.get("external") and info.get("path"):
            self.setToolTip(str(info["path"]))
        layout.addLayout(top)
        layout.addWidget(CaptionLabel(str(info.get("mc") or "")))
        layout.addWidget(CaptionLabel(f"Java · {info.get('java_label') or '自动选择'}"))
        layout.addStretch(1)

        actions = QHBoxLayout()
        actions.setSpacing(4)
        open_btn = TransparentToolButton(FIF.FOLDER)
        open_btn.setToolTip(tr("打开实例文件夹"))
        java_btn = TransparentToolButton(FIF.CODE)
        java_btn.setToolTip(tr("选择此实例使用的 Java"))
        rename_btn = TransparentToolButton(FIF.EDIT)
        rename_btn.setToolTip(tr("重命名"))
        delete_btn = TransparentToolButton(FIF.DELETE)
        delete_btn.setToolTip(tr("删除实例"))
        export_btn = TransparentToolButton(FIF.SHARE if hasattr(FIF, "SHARE") else FIF.DOWNLOAD)
        export_btn.setToolTip(tr("导出为 .mrpack"))
        saves_btn = TransparentToolButton(FIF.PHOTO)
        saves_btn.setToolTip(tr("存档 / 截图"))
        open_btn.clicked.connect(lambda: page.open_folder(info["name"]))
        java_btn.clicked.connect(lambda: page.pick_java(info["name"]))
        rename_btn.clicked.connect(lambda: page.rename(info["name"]))
        delete_btn.clicked.connect(
            lambda: page.delete(info["name"], bool(info.get("external"))))
        if info.get("external"):
            delete_btn.setToolTip(tr("移除外部目录（不删除文件）"))
        export_btn.clicked.connect(lambda: page.export_pack(info["name"]))
        saves_btn.clicked.connect(lambda: page.open_saves(info["name"]))
        actions.addStretch(1)
        actions.addWidget(open_btn)
        actions.addWidget(saves_btn)
        actions.addWidget(java_btn)
        actions.addWidget(rename_btn)
        actions.addWidget(export_btn)
        if info.get("pack"):
            update_btn = TransparentToolButton(
                FIF.UPDATE if hasattr(FIF, "UPDATE") else FIF.SYNC)
            update_btn.setToolTip(tr("检查整合包更新"))
            update_btn.clicked.connect(lambda: page.check_pack_update(info["name"]))
            actions.addWidget(update_btn)
        actions.addWidget(delete_btn)
        layout.addLayout(actions)


class NewInstanceCard(SimpleCardWidget):
    def __init__(self, page, parent=None):
        super().__init__(parent)
        self.page = page
        self.setFixedSize(240, 138)
        self.setCursor(Qt.PointingHandCursor)
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)
        label = StrongBodyLabel(tr("＋ 新建实例"))
        label.setAlignment(Qt.AlignCenter)
        sub = CaptionLabel(tr("隔离的版本、模组与存档"))
        sub.setAlignment(Qt.AlignCenter)
        layout.addWidget(label)
        layout.addWidget(sub)

    def mouseReleaseEvent(self, event):
        # 不判断按键的话，右键这张卡也会弹出新建实例对话框
        if event.button() != Qt.LeftButton:
            return
        self.page.create()


class InstancePage(QWidget):
    def __init__(self, backend, parent=None):
        super().__init__(parent)
        self.setObjectName("instancePage")
        self.backend = backend
        self._reloading = False
        self._cols = 0
        self._picking_java = False
        self._checking_pack = False
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(120)
        self._resize_timer.timeout.connect(self.reload)

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 20, 28, 20)
        root.setSpacing(14)

        head = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.addWidget(SubtitleLabel(tr("实例")))
        title_box.addWidget(CaptionLabel(tr("每个实例相互隔离，放心折腾")))
        head.addLayout(title_box, 1)
        self.link_btn = PushButton(
            getattr(FIF, "FOLDER_ADD", None) or FIF.FOLDER, tr("添加已有游戏目录"))
        self.link_btn.clicked.connect(self.link_external)
        head.addWidget(self.link_btn)
        root.addLayout(head)

        self.scroll = ScrollArea(self)
        self.scroll.setWidgetResizable(True)
        host = QWidget()
        self.grid = QGridLayout(host)
        self.grid.setContentsMargins(0, 0, 8, 0)
        self.grid.setSpacing(12)
        self.scroll.setWidget(host)
        root.addWidget(self.scroll, 1)

        self.reload()

    def reload(self):
        if self._reloading:
            return
        self._reloading = True
        try:
            while self.grid.count():
                item = self.grid.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            insts = self.backend.get_instances()
            cols = grid_columns(self.scroll, self, 240)
            self._cols = cols
            for i, inst in enumerate(insts):
                self.grid.addWidget(InstanceCard(inst, self), i // cols, i % cols)
            n = len(insts)
            self.grid.addWidget(NewInstanceCard(self), n // cols, n % cols)
            # reload 会重建卡片宿主，深色底要再刷一次
            from ..pcl_chrome import paint_theme_surfaces
            paint_theme_surfaces(self)
        finally:
            self._reloading = False

    def create(self):
        dlg = InputDialog(tr("新建实例"), tr("实例名称"), placeholder=tr("例如：模组生存"), parent=self)
        if dlg.exec() and dlg.value():
            try:
                self.backend.create_instance(dlg.value())
            except Exception as e:
                MessageBox(tr("创建失败"), str(e), self).exec()
            self.reload()

    def link_external(self):
        from pathlib import Path
        from PySide6.QtWidgets import QFileDialog
        folder = QFileDialog.getExistingDirectory(
            self, tr("选择游戏目录（如官方启动器的 .minecraft）"))
        if not folder:
            return
        default = Path(folder).name or tr("外部目录")
        dlg = InputDialog(
            tr("添加已有游戏目录"),
            tr("原地使用这个文件夹，不复制任何文件。给它起个实例名："),
            text=default, parent=self)
        if not dlg.exec():
            return
        try:
            self.backend.link_external_instance(dlg.value() or default, folder)
        except Exception as e:
            MessageBox(tr("添加失败"), str(e), self).exec()
            return
        self.reload()

    def delete(self, name: str, external: bool = False):
        if external:
            box = MessageBox(
                tr("移除外部目录"),
                tr("移除外部目录「{name}」？只解除引用，文件夹和其中的存档不会被删除。").format(name=name),
                self)
        else:
            box = MessageBox(tr("删除实例"), f"确定删除实例「{name}」？其中的存档与配置将一并移除。", self)
        if box.exec():
            try:
                self.backend.delete_instance(name)
            except Exception as e:
                MessageBox(tr("删除失败"), str(e), self).exec()
            self.reload()

    def rename(self, name: str):
        dlg = InputDialog(tr("重命名实例"), tr("新名称"), text=name, parent=self)
        if dlg.exec() and dlg.value():
            try:
                self.backend.rename_instance(name, dlg.value())
            except Exception as e:
                MessageBox(tr("重命名失败"), str(e), self).exec()
            self.reload()

    def export_pack(self, name: str):
        self.backend.export_modpack(name)

    def check_pack_update(self, name: str):
        if self._checking_pack:
            return
        self._checking_pack = True

        def fetch():
            state = self.backend.get_modpack_state(name)
            if not state.get("can_update"):
                return {"state": state}
            return {"state": state, "check": self.backend.check_modpack_update(name)}

        def done(result):
            self._checking_pack = False
            state = result.get("state") or {}
            if not state.get("can_update"):
                MessageBox(tr("无法检查更新"),
                           state.get("reason") or tr("该实例不是整合包安装"), self).exec()
                return
            check = result.get("check") or {}
            if not check.get("has_update"):
                from qfluentwidgets import InfoBar, InfoBarPosition
                InfoBar.success(
                    tr("已是最新"),
                    tr("整合包 {name} 当前 {version} 已是最新版本").format(
                        name=state.get("name") or name,
                        version=check.get("current") or state.get("version") or "?"),
                    parent=self, position=InfoBarPosition.TOP, duration=3500)
                return
            lines = [
                tr("整合包「{name}」有新版本：").format(name=state.get("name") or name),
                f"{check.get('current') or '?'}  →  {check.get('latest') or '?'}"
                + (f"（{check.get('date')}）" if check.get("date") else ""),
                "",
                tr("更新会替换整合包管理的模组与配置；你修改过的配置会先备份到实例的 backups 目录。"),
            ]
            if check.get("changelog"):
                lines += ["", tr("更新日志："), check["changelog"][:400]]
            box = MessageBox(tr("发现整合包更新"), "\n".join(lines), self)
            box.yesButton.setText(tr("立即更新"))
            box.cancelButton.setText(tr("取消"))
            if box.exec():
                self.backend.update_modpack(name, check.get("latest_id") or "")
                from qfluentwidgets import InfoBar, InfoBarPosition
                InfoBar.info(tr("整合包更新中"),
                             tr("进度见「任务」页，完成后实例会自动刷新"),
                             parent=self, position=InfoBarPosition.TOP, duration=3500)

        def failed(msg):
            self._checking_pack = False
            MessageBox(tr("检查整合包更新失败"), str(msg or tr("未知错误")), self).exec()

        self.backend.call_async(fetch, done, failed)

    def pick_java(self, name: str):
        if self._picking_java:
            return
        self._picking_java = True

        def open_dlg(opts):
            self._picking_java = False
            opts = list(opts or [])
            labels = [o["label"] for o in opts]
            current = self.backend.java_combo_label_for(name, opts)
            dlg = ComboDialog(
                tr("选择 Java"),
                f"实例「{name}」启动时使用的 Java。自动选择会按游戏版本匹配（1.19+ 用 17，远古版用 8）。",
                labels, current, self,
            )
            if dlg.exec():
                chosen = dlg.value()
                value = tr("自动选择")
                for o in opts:
                    if o["label"] == chosen:
                        value = o["value"]
                        break
                try:
                    self.backend.set_instance_java(name, value)
                except Exception as e:
                    MessageBox(tr("保存失败"), str(e), self).exec()
                self.reload()
                win = self.window()
                lp = getattr(win, "launch_page", None)
                if lp is not None:
                    lp._reload_java_box()

        def failed(msg):
            self._picking_java = False
            MessageBox(tr("扫描 Java 失败"), str(msg or tr("未知错误")), self).exec()

        call_async = getattr(self.backend, "call_async", None)
        if callable(call_async):
            call_async(lambda: self.backend.java_combo_options(name, True), open_dlg, failed)
            return
        try:
            open_dlg(self.backend.java_combo_options(name, True))
        except Exception as e:
            failed(e)

    def open_folder(self, name: str):
        try:
            self.backend.open_instance_folder(name)
        except Exception as e:
            MessageBox(tr("无法打开"), str(e), self).exec()

    def open_saves(self, name: str):
        from .saves_dialog import SavesDialog
        SavesDialog(self.backend, name, "", self).exec()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not self.isVisible():
            return
        cols = grid_columns(self.scroll, self, 240)
        if cols == self._cols:
            return
        self._resize_timer.start()
