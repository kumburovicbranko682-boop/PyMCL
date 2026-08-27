# -*- coding: utf-8 -*-
"""实例页：实例卡片网格 + 新建实例入口卡。"""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    CaptionLabel, FluentIcon as FIF, InfoBar, InfoBarPosition, MessageBox,
    ScrollArea, SimpleCardWidget,
    StrongBodyLabel, SubtitleLabel, TransparentToolButton,
)

from mclauncher.config import CONFIG
from ..widgets import ComboDialog, IconTile, InputDialog, Pill, anchor_grid, grid_columns
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
        top.addWidget(IconTile(info["name"], size=40, image=info.get("icon") or None))
        name_box = QVBoxLayout()
        name_box.setSpacing(2)
        name_box.addWidget(StrongBodyLabel(info["name"]))
        name_box.addWidget(CaptionLabel(tr("{0} 个版本").format(info["versions"])))
        top.addLayout(name_box, 1)
        if info.get("pack"):
            update_btn = TransparentToolButton(getattr(FIF, "UPDATE", FIF.SYNC))
            update_btn.setToolTip(tr("检查整合包更新"))
            update_btn.clicked.connect(lambda: page.check_pack_update(info["name"]))
            top.addWidget(update_btn)
        top.addWidget(Pill(tr("默认") if info["name"] == CONFIG.get("default_instance") else tr("实例"), "#4C8BF5"))
        layout.addLayout(top)
        layout.addWidget(CaptionLabel(str(info.get("mc") or "")))
        # java_label 若是「自动选择」哨兵原文，过一遍 tr 让英文界面不漏翻
        layout.addWidget(CaptionLabel(f"Java · {tr(info.get('java_label') or '自动选择')}"))
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
        delete_btn.clicked.connect(lambda: page.delete(info["name"]))
        export_btn.clicked.connect(lambda: page.export_pack(info["name"]))
        saves_btn.clicked.connect(lambda: page.open_saves(info["name"]))
        actions.addStretch(1)
        actions.addWidget(open_btn)
        actions.addWidget(saves_btn)
        actions.addWidget(java_btn)
        actions.addWidget(rename_btn)
        if info.get("pack"):
            update_btn = TransparentToolButton(
                FIF.UPDATE if hasattr(FIF, "UPDATE") else FIF.SYNC)
            update_btn.setToolTip(tr("检查整合包更新"))
            update_btn.clicked.connect(lambda: page.check_pack_update(info["name"]))
            actions.addWidget(update_btn)
        actions.addWidget(export_btn)
        actions.addWidget(delete_btn)
        layout.addLayout(actions)

    def contextMenuEvent(self, event):
        from qfluentwidgets import Action, RoundMenu
        menu = RoundMenu(parent=self)
        dup = Action(FIF.COPY, tr("复制实例"))
        dup.triggered.connect(lambda: self.page.duplicate(self.info["name"]))
        menu.addAction(dup)
        set_icon = Action(FIF.PHOTO, tr("设置图标…"))
        set_icon.triggered.connect(lambda: self.page.set_icon(self.info["name"]))
        menu.addAction(set_icon)
        if self.info.get("icon"):
            reset_icon = Action(FIF.CANCEL, tr("恢复默认图标"))
            reset_icon.triggered.connect(lambda: self.page.reset_icon(self.info["name"]))
            menu.addAction(reset_icon)
        menu.exec(event.globalPos())


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
                    item.widget().hide()
                    item.widget().deleteLater()
            insts = self.backend.get_instances()
            cols = grid_columns(self.scroll, self, 240)
            self._cols = cols
            for i, inst in enumerate(insts):
                self.grid.addWidget(InstanceCard(inst, self), i // cols, i % cols)
            n = len(insts)
            self.grid.addWidget(NewInstanceCard(self), n // cols, n % cols)
            anchor_grid(self.grid, cols, n // cols + 1)
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

    def delete(self, name: str):
        box = MessageBox(
            tr("删除实例"),
            tr("确定删除实例「{name}」？存档与配置会一并移除（会尽量移入系统回收站，可找回）。").format(name=name),
            self)
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

    def duplicate(self, name: str):
        dlg = InputDialog(tr("复制实例"), tr("新实例名称（版本、模组、存档都会复制）"),
                          text=f"{name}-副本", parent=self)
        if dlg.exec() and dlg.value():
            try:
                self.backend.duplicate_instance(name, dlg.value())
            except Exception as e:
                MessageBox(tr("复制失败"), str(e), self).exec()

    def set_icon(self, name: str):
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(
            self, tr("选择实例图标"), "",
            tr("图片文件") + " (*.png *.jpg *.jpeg *.gif *.webp *.bmp)")
        if not path:
            return
        try:
            self.backend.set_instance_icon(name, path)
        except Exception as e:
            MessageBox(tr("设置图标失败"), str(e), self).exec()
        self.reload()

    def reset_icon(self, name: str):
        try:
            self.backend.clear_instance_icon(name)
        except Exception as e:
            MessageBox(tr("设置图标失败"), str(e), self).exec()
        self.reload()

    def check_pack_update(self, name: str):
        def done(info):
            info = info or {}
            if not info.get("update"):
                MessageBox(
                    tr("整合包更新"),
                    tr("「{name}」已是最新版本（{v}）").format(
                        name=info.get("name") or name, v=info.get("current") or "?"),
                    self.window(),
                ).exec()
                return
            box = MessageBox(
                tr("发现整合包新版本"),
                tr("{name}：{a} → {b}\n\n更新会重新安装整合包文件并清理旧版本残留的模组；"
                   "存档、截图与手动添加的模组不受影响。是否更新？").format(
                    name=info.get("name") or name,
                    a=info.get("current") or "?", b=info.get("latest") or "?"),
                self.window(),
            )
            if box.exec():
                self.backend.update_modpack(name)

        def failed(msg):
            MessageBox(tr("检查整合包更新失败"), str(msg or tr("未知错误")), self.window()).exec()

        call_async = getattr(self.backend, "call_async", None)
        if callable(call_async):
            call_async(lambda: self.backend.check_modpack_update(name), done, failed)
            return
        try:
            done(self.backend.check_modpack_update(name))
        except Exception as e:
            failed(e)

    def export_pack(self, name: str):
        from .export_dialog import ExportPackDialog

        def open_dlg(info):
            dlg = ExportPackDialog(info, parent=self.window())
            if not dlg.exec():
                return
            p = dlg.payload()
            self.backend.export_modpack(name, fmt=p["fmt"],
                                        include=p["include"], meta=p["meta"])

        def failed(msg):
            MessageBox(tr("导出整合包"), str(msg or tr("未知错误")), self.window()).exec()

        call_async = getattr(self.backend, "call_async", None)
        if callable(call_async):
            call_async(lambda: self.backend.export_pack_info(name), open_dlg, failed)
            return
        try:
            open_dlg(self.backend.export_pack_info(name))
        except Exception as e:
            failed(e)

    def check_pack_update(self, name: str):
        def ok(info):
            if info.get("has_update"):
                box = MessageBox(
                    tr("发现整合包新版本"),
                    tr("{n}\n当前 {a} → 最新 {b}\n\n更新会按新版本重装整合包：存档保留，config 等会被新包覆盖。继续？").format(
                        n=info.get("name"), a=info.get("current"),
                        b=info.get("latest")),
                    self)
                box.yesButton.setText(tr("更新"))
                if box.exec():
                    self.backend.start_modpack_update(name)
                    InfoBar.success(
                        tr("已开始更新"), tr("进度见下载任务。"), parent=self,
                        position=InfoBarPosition.TOP, duration=4000)
            else:
                InfoBar.success(
                    tr("已是最新"),
                    f"{info.get('name')} {info.get('current')}", parent=self,
                    position=InfoBarPosition.TOP, duration=4000)

        def fail(message):
            MessageBox(tr("无法检查整合包更新"), str(message), self).exec()

        self.backend.call_async(
            lambda: self.backend.check_modpack_update(name), ok, fail)

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
                tr("实例「{0}」启动时使用的 Java。自动选择会按游戏版本匹配（1.19+ 用 17，远古版用 8）。").format(name),
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

    def showEvent(self, event):
        """懒构造时页面只有 100px 宽，列数算成 1、实例卡挤成居中一列；
        且 resizeEvent 里的 isVisible 守卫会吞掉显示前的那次修正。
        首次显示后补一次列数校验。"""
        super().showEvent(event)
        QTimer.singleShot(0, self._recheck_cols)

    def _recheck_cols(self):
        if grid_columns(self.scroll, self, 240) != self._cols:
            self.reload()
