# -*- coding: utf-8 -*-
"""版本页：版本卡片网格 + 加载器安装 + 已安装管理。"""

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    Action, BodyLabel, CaptionLabel, ComboBox, FluentIcon as FIF, InfoBar, InfoBarPosition,
    MessageBox, Pivot, PushButton, CheckBox, RoundMenu, ScrollArea, SearchLineEdit,
    SimpleCardWidget, StrongBodyLabel, SubtitleLabel, TransparentToolButton,
)

from mclauncher.config import CONFIG
from ..widgets import EmptyState, Pill, grid_columns
from mclauncher.i18n import tr


class VersionCard(SimpleCardWidget):
    def __init__(self, info: dict, on_install, parent=None):
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
        row.addStretch(1)
        install_btn = PushButton(FIF.DOWNLOAD, tr("安装"))
        install_btn.setFixedHeight(30)
        install_btn.clicked.connect(lambda: on_install(info, self))
        row.addWidget(install_btn)
        layout.addLayout(row)


class VersionPage(QWidget):
    def __init__(self, backend, parent=None):
        super().__init__(parent)
        self.setObjectName("versionPage")
        self.backend = backend
        self._all_versions: list[dict] = []
        self._fetched = False
        self._fetching = False
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
        bar.addWidget(self.search)
        bar.addWidget(self.pivot)
        bar.addStretch(1)
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
        self._start_fetch()
        self._refill()
        self._reload_installed()

    def _start_fetch(self):
        if self._fetched:
            return
        self._fetched = True
        self._fetching = True
        self.backend.call_async(
            self.backend.fetch_version_list,
            self._on_versions_fetched,
            self._on_versions_err,
        )

    def _on_versions_fetched(self, rows):
        self._fetching = False
        self._all_versions = rows or []
        self._refill()

    def _on_versions_err(self, err):
        self._fetched = False
        self._fetching = False
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
                EmptyState(FIF.INFO, tr("版本列表加载失败，多为网络波动"),
                           action_text=tr("重试"),
                           on_action=self._retry_fetch), 0, 0)
            self._cols = 1

    def _retry_fetch(self):
        self._start_fetch()
        self._refill()

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
            if not self._all_versions and self._fetching:
                # 清单还在后台拉取：别谎报「没有匹配的版本」
                self.grid.addWidget(
                    EmptyState(FIF.SYNC, tr("正在获取版本列表…")), 0, 0)
            else:
                self.grid.addWidget(EmptyState(FIF.SEARCH, tr("没有匹配的版本")), 0, 0)
            self._cols = 1
            return
        cols = grid_columns(self.scroll, self, 240)
        self._cols = cols
        shown = rows[: self._limit]
        for i, v in enumerate(shown):
            self.grid.addWidget(VersionCard(v, self._install), i // cols, i % cols)
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
        for v in self.backend.get_installed_versions(instance, include_hidden=self._show_hidden):
            row = QHBoxLayout()
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
        add(tr("重命名"), lambda: self._rename(instance, version))
        add(tr("复制"), lambda: self._copy(instance, version))
        add(tr("隐藏 / 取消隐藏"), lambda: self._hide(instance, version))
        add(tr("创建桌面快捷方式"), lambda: self._shortcut(instance, version))
        add(tr("导出启动脚本"), lambda: self._export_script(instance, version))
        menu.exec(btn.mapToGlobal(btn.rect().bottomLeft()))

    def _export_script(self, instance, version):
        try:
            self.backend.export_launch_script(instance, version)
        except Exception as e:
            MessageBox(tr("导出失败"), str(e), self).exec()
            return
        InfoBar.success(
            tr("已开始导出启动脚本"),
            tr("进度见「下载任务」，完成后文件在数据目录 exports 文件夹"),
            parent=self, position=InfoBarPosition.TOP, duration=4000,
        )

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

    def _saves(self, instance, version):
        from .saves_dialog import SavesDialog
        SavesDialog(self.backend, instance, version, self).exec()

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
        box = MessageBox(tr("确认卸载"), f"将卸载 {len(selected)} 个版本：\n" + "\n".join(selected), self)
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
