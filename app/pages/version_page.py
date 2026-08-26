# -*- coding: utf-8 -*-
"""版本页：版本卡片网格 + 加载器安装 + 已安装管理。"""

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    Action, BodyLabel, CaptionLabel, ComboBox, FluentIcon as FIF, InfoBar, InfoBarPosition,
    MessageBox, MessageBoxBase, Pivot, PushButton, CheckBox, RoundMenu, ScrollArea,
    SearchLineEdit, SimpleCardWidget, StrongBodyLabel, SubtitleLabel, TextEdit,
    TransparentToolButton,
)

from mclauncher.config import CONFIG
from ..widgets import EmptyState, Pill, grid_columns
from mclauncher.i18n import tr


class VersionCard(SimpleCardWidget):
    def __init__(self, info: dict, on_install, parent=None, on_notes=None):
        super().__init__(parent)
        self.info = info
        self.setFixedSize(216, 132)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(6)

        top = QHBoxLayout()
        top.addWidget(StrongBodyLabel(info["version"]), 1)
        vtype = info["type"]
        labels = {"release": tr("正式版"), "snapshot": tr("快照"), "old_alpha": tr("远古"), "old_beta": tr("远古")}
        colors = {"release": "#2FA36B", "snapshot": "#E8862E", "old_alpha": "#7C5CD6", "old_beta": "#7C5CD6"}
        top.addWidget(Pill(labels.get(vtype, vtype), colors.get(vtype, "#E8862E")))
        layout.addLayout(top)
        layout.addWidget(CaptionLabel(f'发布于 {info["date"]}'))
        layout.addStretch(1)

        row = QHBoxLayout()
        if on_notes is not None:
            notes_btn = TransparentToolButton(FIF.INFO)
            notes_btn.setToolTip(tr("查看官方更新内容"))
            notes_btn.clicked.connect(lambda: on_notes(info))
            row.addWidget(notes_btn)
        row.addStretch(1)
        install_btn = PushButton(FIF.DOWNLOAD, tr("安装"))
        install_btn.setFixedHeight(30)
        install_btn.clicked.connect(lambda: on_install(info, self))
        row.addWidget(install_btn)
        layout.addLayout(row)


class PatchNotesDialog(MessageBoxBase):
    """某个 MC 版本的官方更新说明（Mojang patch notes 转纯文本）。"""

    def __init__(self, version: str, parent=None):
        super().__init__(parent)
        self.title_label = SubtitleLabel(tr("{v} 更新内容").format(v=version), self)
        self.viewLayout.addWidget(self.title_label)
        self.meta_label = CaptionLabel(tr("加载中…"), self)
        self.viewLayout.addWidget(self.meta_label)
        self.body = TextEdit(self)
        self.body.setReadOnly(True)
        self.body.setMinimumHeight(320)
        self.viewLayout.addWidget(self.body)
        self.yesButton.setText(tr("关闭"))
        self.cancelButton.hide()
        self.widget.setMinimumWidth(600)

    def set_note(self, note: dict):
        title = str(note.get("title") or "").strip()
        if title:
            self.title_label.setText(title)
        bits = [b for b in (note.get("date") or "", note.get("type") or "") if b]
        self.meta_label.setText(" · ".join(bits))
        body = str(note.get("body") or "").strip()
        self.body.setPlainText(body or tr("这个版本没有官方更新说明。"))

    def set_error(self, message: str):
        self.meta_label.setText("")
        self.body.setPlainText(tr("加载失败：{err}").format(err=message))


class VersionPage(QWidget):
    def __init__(self, backend, parent=None):
        super().__init__(parent)
        self.setObjectName("versionPage")
        self.backend = backend
        self._all_versions: list[dict] = []
        self._fetched = False
        self._cols = 0
        # 首屏只建一屏卡片：每张 Fluent 卡 ~7ms，一次建 80 张光构造就
        # 500ms+，还占内存。翻页用「加载更多」补，步长 80。
        self._limit = 24
        self._show_hidden = bool(CONFIG.get("show_hidden_versions"))
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.timeout.connect(self._refill)
        # 搜索 / 筛选防抖：每个键入都全量重建网格是输入卡顿的直接来源。
        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(150)
        self._filter_timer.timeout.connect(self._refill)

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 20, 28, 20)
        root.setSpacing(14)
        root.addWidget(SubtitleLabel(tr("版本")))

        bar = QHBoxLayout()
        bar.setSpacing(12)
        self.search = SearchLineEdit()
        self.search.setPlaceholderText(tr("搜索版本号…"))
        self.search.setFixedWidth(260)
        self.pivot = Pivot(self)
        self.pivot.addItem("all", tr("全部"))
        self.pivot.addItem("release", tr("正式版"))
        self.pivot.addItem("snapshot", tr("快照"))
        self.pivot.addItem("old_alpha", tr("远古"))
        self.pivot.setCurrentItem("all")
        self.instance_box = ComboBox()
        self.instance_box.setFixedWidth(140)
        self.launch_after = CheckBox(tr("完成后启动"))
        self.launch_after.setChecked(True)
        self.hidden_box = CheckBox(tr("显示隐藏"))
        self.hidden_box.setChecked(self._show_hidden)
        self.json_btn = PushButton(FIF.CODE, tr("版本 JSON"))
        self.json_btn.setToolTip(tr("从本地版本 JSON 文件安装版本（HMCL 同款）"))
        bar.addWidget(self.search)
        bar.addWidget(self.pivot)
        bar.addStretch(1)
        bar.addWidget(self.json_btn)
        bar.addWidget(BodyLabel(tr("实例")))
        bar.addWidget(self.instance_box)
        bar.addWidget(self.hidden_box)
        bar.addWidget(self.launch_after)
        root.addLayout(bar)

        self.scroll = ScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.grid_host = QWidget()
        self.grid = QGridLayout(self.grid_host)
        self.grid.setContentsMargins(0, 0, 8, 0)
        self.grid.setSpacing(12)
        self.scroll.setWidget(self.grid_host)
        root.addWidget(self.scroll, 3)

        installed_card = SimpleCardWidget(self)
        ic_layout = QVBoxLayout(installed_card)
        ic_layout.setContentsMargins(20, 14, 20, 14)
        ic_layout.setSpacing(10)
        head = QHBoxLayout()
        head.addWidget(StrongBodyLabel(tr("已安装版本")))
        head.addStretch(1)
        self.uninstall_btn = TransparentToolButton(FIF.DELETE)
        self.uninstall_btn.setToolTip(tr("卸载选中版本"))
        self.repair_btn = TransparentToolButton(FIF.SYNC)
        self.repair_btn.setToolTip(tr("修复选中版本（补全缺失文件）"))
        head.addWidget(self.repair_btn)
        head.addWidget(self.uninstall_btn)
        ic_layout.addLayout(head)
        self.installed_area = QVBoxLayout()
        self.installed_area.setSpacing(6)
        ic_layout.addLayout(self.installed_area)
        root.addWidget(installed_card, 1)

        self.search.textChanged.connect(self._on_filter_changed)
        self.pivot.currentItemChanged.connect(self._on_filter_changed)
        self.uninstall_btn.clicked.connect(self._uninstall_selected)
        self.repair_btn.clicked.connect(self._repair_selected)
        self.instance_box.currentTextChanged.connect(self._reload_installed)
        self.hidden_box.toggled.connect(self._toggle_hidden)
        self.json_btn.clicked.connect(self._install_from_json)

        self.reload()

    def reload(self):
        cur = self.instance_box.currentText()
        self.instance_box.blockSignals(True)
        self.instance_box.clear()
        names = [i["name"] for i in self.backend.get_instances()]
        self.instance_box.addItems(names)
        if cur in names:
            self.instance_box.setCurrentText(cur)
        elif CONFIG.get("default_instance") in names:
            self.instance_box.setCurrentText(CONFIG.get("default_instance"))
        self.instance_box.blockSignals(False)
        self._all_versions = self.backend.get_version_list()
        self._refill()
        self._reload_installed()
        if self._fetched:
            return
        self._fetched = True
        self.backend.call_async(
            self.backend.fetch_version_list,
            self._on_versions_fetched,
            self._on_versions_err,
        )

    def _on_versions_fetched(self, rows):
        self._all_versions = rows or []
        self._refill()

    def _on_versions_err(self, err):
        self._fetched = False
        msg = str(err or tr("未知错误"))
        InfoBar.error(
            tr("版本列表加载失败"),
            msg,
            parent=self,
            position=InfoBarPosition.TOP,
            duration=4000,
        )
        if not self._all_versions:
            while self.grid.count():
                item = self.grid.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            self.grid.addWidget(
                EmptyState(FIF.INFO, tr("版本列表加载失败")), 0, 0)
            self._cols = 1

    def reload_installed_only(self):
        cur = self.instance_box.currentText()
        names = [i["name"] for i in self.backend.get_instances()]
        self.instance_box.blockSignals(True)
        self.instance_box.clear()
        self.instance_box.addItems(names)
        if cur in names:
            self.instance_box.setCurrentText(cur)
        self.instance_box.blockSignals(False)
        self._reload_installed()

    def _on_filter_changed(self, *_a):
        """文本 / 透视筛选变化：防抖后再重建网格。"""
        self._filter_timer.start()

    def _refill(self):
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        text = self.search.text().strip().lower()
        vtype = self.pivot.currentRouteKey()
        rows = []
        for v in self._all_versions:
            if text and text not in v["version"].lower():
                continue
            if vtype == "all":
                rows.append(v)
            elif vtype == "old_alpha" and v["type"] in ("old_alpha", "old_beta"):
                rows.append(v)
            elif v["type"] == vtype:
                rows.append(v)

        if not rows:
            self.grid.addWidget(EmptyState(FIF.SEARCH, tr("没有匹配的版本")), 0, 0)
            self._cols = 1
            return
        cols = grid_columns(self.scroll, self, 240)
        self._cols = cols
        shown = rows[: self._limit]
        for i, v in enumerate(shown):
            self.grid.addWidget(VersionCard(v, self._install, on_notes=self._show_notes),
                                i // cols, i % cols)
        if len(rows) > self._limit:
            more = PushButton(f"加载更多（还有 {len(rows) - self._limit}）")
            more.clicked.connect(self._more)
            self.grid.addWidget(more, (len(shown) + cols - 1) // cols, 0, 1, cols)

    def _reload_installed(self):
        while self.installed_area.count():
            item = self.installed_area.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                while item.layout().count():
                    sub = item.layout().takeAt(0)
                    if sub.widget():
                        sub.widget().deleteLater()

        self._installed_checks = []
        instance = self.instance_box.currentText() or "default"
        try:
            stats = self.backend.get_version_stats(instance) or {}
        except Exception:
            stats = {}
        ids = self.backend.get_installed_versions(instance, include_hidden=self._show_hidden)
        # HMCL 游戏列表同款：最近玩过的排前面；没玩过的保持原（字母）顺序垫底
        ids = sorted(ids, key=lambda vid: -(stats.get(vid, {}).get("last") or 0))
        for v in ids:
            row = QHBoxLayout()
            # 版本自定义图标（PCL2/HMCL 版本图标同款）：设置过就摆在最前
            try:
                icon_file = self.backend.get_version_icon(instance, v)
            except Exception:
                icon_file = ""
            if icon_file:
                from PySide6.QtCore import Qt as _Qt
                from PySide6.QtGui import QPixmap
                from PySide6.QtWidgets import QLabel
                pix = QPixmap(icon_file)
                if not pix.isNull():
                    icon_label = QLabel()
                    icon_label.setPixmap(pix.scaled(
                        22, 22, _Qt.KeepAspectRatio, _Qt.SmoothTransformation))
                    icon_label.setFixedSize(24, 24)
                    icon_label.setStyleSheet("background: transparent;")
                    row.addWidget(icon_label)
            cb = CheckBox(v)
            low = v.lower()
            color = "#7C5CD6" if "fabric" in low else (
                "#E8862E" if "forge" in low and "neo" not in low else (
                    "#2E9B6B" if "optifine" in low else (
                        "#4C8BF5" if "liteloader" in low else "#4C8BF5")))
            label = "Fabric" if "fabric" in low else (
                "Forge" if "forge" in low and "neo" not in low else (
                    "Quilt" if "quilt" in low else (
                        "NeoForge" if "neoforge" in low or "neo" in low else (
                            "OptiFine" if "optifine" in low else (
                                "LiteLoader" if "liteloader" in low else tr("原版"))))))
            row.addWidget(cb, 1)
            st = stats.get(v) or {}
            if st.get("last"):
                played = CaptionLabel(f"{st.get('last_text', '')} · {st.get('seconds_text', '')}")
                played.setStyleSheet("color: rgba(128,128,128,0.85); background: transparent;")
                row.addWidget(played)
            row.addWidget(Pill(label, color))
            setup = TransparentToolButton(FIF.SETTING)
            setup.setToolTip(tr("版本设置"))
            setup.clicked.connect(lambda _, vid=v, inst=instance: self._setup(inst, vid))
            more = TransparentToolButton(getattr(FIF, "MORE", FIF.VIEW))
            more.setToolTip(tr("更多"))
            more.clicked.connect(lambda _, vid=v, inst=instance, btn=more: self._version_menu(inst, vid, btn))
            row.addWidget(setup)
            row.addWidget(more)
            self.installed_area.addLayout(row)
            self._installed_checks.append((cb, f"{instance} / {v}"))

    def _more(self):
        self._limit += 80
        self._refill()

    def _toggle_hidden(self, on):
        self._show_hidden = bool(on)
        from mclauncher.config import CONFIG
        CONFIG.set("show_hidden_versions", self._show_hidden)
        CONFIG.save()
        self._reload_installed()

    def _show_notes(self, info: dict):
        """官方版本更新说明（HMCL 版本公告同款）：异步拉取后弹窗。"""
        version = str(info.get("version") or "")
        dlg = PatchNotesDialog(version, parent=self.window())

        def done(note):
            try:
                dlg.set_note(note or {})
            except RuntimeError:
                pass

        def failed(err):
            try:
                dlg.set_error(str(err or tr("未知错误")))
            except RuntimeError:
                pass

        self.backend.call_async(
            lambda: self.backend.game_patch_note(version), done, failed)
        dlg.exec()

    def _install(self, info: dict, source=None):
        instance = self.instance_box.currentText() or "default"
        from .install_wizard import InstallWizardDialog
        dlg = InstallWizardDialog(self.backend, info["version"], instance, self)
        if not dlg.exec():
            return
        payload = dlg.payload()
        loader = payload.get("loader") or tr("无")
        win = self.window()
        if source is not None and hasattr(win, "fly_to_tasks"):
            win.fly_to_tasks(source, info["version"], "#2FA36B")
        tid = self.backend.install_game(
            info["version"], loader, payload.get("loader_version") or "",
            instance=instance, extra=payload.get("extra") or {},
        )
        if self.launch_after.isChecked() and hasattr(win, "queue_launch_after"):
            win.queue_launch_after(tid, instance, info["version"], loader)

    def _version_menu(self, instance, version, btn):
        menu = RoundMenu(parent=self)

        def add(text, fn):
            act = Action(text)
            act.triggered.connect(fn)
            menu.addAction(act)

        add(tr("打开游戏文件夹"), lambda: self._open_folder(instance, version, "game"))
        add(tr("打开 mods"), lambda: self._open_folder(instance, version, "mods"))
        add(tr("打开 saves"), lambda: self._open_folder(instance, version, "saves"))
        add(tr("打开截图"), lambda: self._open_folder(instance, version, "screenshots"))
        add(tr("存档管理…"), lambda: self._saves(instance, version))
        add(tr("原理图管理…"), lambda: self._schematics(instance, version))
        add(tr("重命名"), lambda: self._rename(instance, version))
        add(tr("复制"), lambda: self._copy(instance, version))
        add(tr("设置版本图标…"), lambda: self._set_icon(instance, version))
        if self.backend.get_version_icon(instance, version):
            add(tr("清除版本图标"), lambda: self._clear_icon(instance, version))
        add(tr("隐藏 / 取消隐藏"), lambda: self._hide(instance, version))
        add(tr("创建桌面快捷方式"), lambda: self._shortcut(instance, version))
        add(tr("导出启动脚本"), lambda: self.backend.export_launch_script(instance, version))
        menu.exec(btn.mapToGlobal(btn.rect().bottomLeft()))

    def _shortcut(self, instance, version):
        try:
            path = self.backend.create_desktop_shortcut(instance, version)
        except Exception as e:
            MessageBox(tr("创建失败"), str(e), self).exec()
            return
        MessageBox(tr("已创建"), f"桌面快捷方式：\n{path}\n\n双击即可直接启动该版本。", self).exec()

    def _open_folder(self, instance, version, which):
        try:
            self.backend.open_version_folder(instance, version, which)
        except Exception as e:
            MessageBox(tr("无法打开"), str(e), self).exec()

    def _install_from_json(self):
        """从本地版本 JSON 安装（HMCL「通过版本 JSON 安装」同款入口）。"""
        from PySide6.QtWidgets import QFileDialog
        from ..widgets import InputDialog
        path, _ = QFileDialog.getOpenFileName(
            self, tr("选择版本 JSON 文件"), "",
            tr("版本 JSON (*.json)") + ";;" + tr("所有文件 (*)"))
        if not path:
            return
        info = self.backend.classify_import(path)
        if info.get("kind") != "version_json":
            MessageBox(tr("无法识别"),
                       tr("该文件不是 Minecraft 版本 JSON（缺少 id 与 mainClass / inheritsFrom）。"),
                       self).exec()
            return
        from pathlib import Path as _P
        default_id = info.get("version_id") or _P(path).stem
        dlg = InputDialog(tr("通过版本 JSON 安装"), tr("版本名称"), text=default_id, parent=self)
        if not dlg.exec() or not dlg.value():
            return
        instance = self.instance_box.currentText() or ""
        try:
            self.backend.install_version_json(path, dlg.value(), instance)
        except Exception as e:
            MessageBox(tr("安装失败"), str(e), self).exec()
            return
        InfoBar.success(tr("已开始安装"), tr("进度见「下载任务」"), parent=self,
                        position=InfoBarPosition.TOP_RIGHT, duration=3500)

    def _saves(self, instance, version):
        from .saves_dialog import SavesDialog
        SavesDialog(self.backend, instance, version, self).exec()

    def _schematics(self, instance, version):
        from .schematics_dialog import SchematicsDialog
        SchematicsDialog(self.backend, instance, version, self).exec()

    def _set_icon(self, instance, version):
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(
            self, tr("选择版本图标"), "",
            tr("图片 (*.png *.jpg *.jpeg *.gif *.webp *.bmp)") + ";;" + tr("所有文件 (*)"))
        if not path:
            return
        try:
            self.backend.set_version_icon(instance, version, path)
        except Exception as e:
            MessageBox(tr("设置失败"), str(e), self).exec()
            return
        self._reload_installed()

    def _clear_icon(self, instance, version):
        try:
            self.backend.clear_version_icon(instance, version)
        except Exception as e:
            MessageBox(tr("操作失败"), str(e), self).exec()
            return
        self._reload_installed()

    def _rename(self, instance, version):
        from ..widgets import InputDialog
        dlg = InputDialog(tr("重命名版本"), tr("新版本 ID"), text=version, parent=self)
        if dlg.exec() and dlg.value():
            try:
                self.backend.rename_version(instance, version, dlg.value())
            except Exception as e:
                MessageBox(tr("重命名失败"), str(e), self).exec()
            self._reload_installed()

    def _copy(self, instance, version):
        from ..widgets import InputDialog
        dlg = InputDialog(tr("复制版本"), tr("新版本 ID"), text=version + "-copy", parent=self)
        if dlg.exec() and dlg.value():
            try:
                self.backend.copy_version(instance, version, dlg.value())
            except Exception as e:
                MessageBox(tr("复制失败"), str(e), self).exec()
            self._reload_installed()

    def _hide(self, instance, version):
        try:
            data = self.backend.get_version_settings(instance, version)
            self.backend.hide_version(instance, version, not bool(data.get("hidden")))
        except Exception as e:
            MessageBox(tr("操作失败"), str(e), self).exec()
        self._reload_installed()

    def _uninstall_selected(self):
        selected = [v for rb, v in getattr(self, "_installed_checks", []) if rb.isChecked()]
        if not selected:
            box = MessageBox(tr("未选择"), tr("请先勾选要卸载的版本"), self)
            box.exec()
            return
        box = MessageBox(
            tr("确认卸载"),
            f"将卸载 {len(selected)} 个版本：\n" + "\n".join(selected)
            + "\n" + tr("（会尽量移入系统回收站，可找回）"), self)
        if box.exec():
            for spec in selected:
                try:
                    self.backend.uninstall_version(spec)
                except Exception as e:
                    MessageBox(tr("卸载失败"), str(e), self).exec()
            self._reload_installed()

    def _setup(self, instance, version):
        from .version_setup import VersionSetupDialog
        dlg = VersionSetupDialog(self.backend, instance, version, self)
        if dlg.exec():
            dlg.save()

    def _repair_selected(self):
        selected = [v for rb, v in getattr(self, "_installed_checks", []) if rb.isChecked()]
        if not selected:
            MessageBox(tr("未选择"), tr("请先勾选要修复的版本"), self).exec()
            return
        for spec in selected:
            inst, vid = spec.split(" / ", 1) if " / " in spec else (
                self.instance_box.currentText() or "default", spec)
            self.backend.repair_version(inst, vid)
        InfoBar.success(tr("已开始修复"), f"{len(selected)} 个版本", parent=self,
                        position=InfoBarPosition.TOP, duration=2500)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not self._all_versions:
            return
        cols = grid_columns(self.scroll, self, 240)
        if cols == self._cols:
            return
        self._resize_timer.start(120)
