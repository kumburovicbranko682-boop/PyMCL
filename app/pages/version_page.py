# -*- coding: utf-8 -*-
"""版本页：版本卡片网格 + 加载器安装 + 已安装管理。"""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QGridLayout, QHBoxLayout, QListWidget, QListWidgetItem, QVBoxLayout, QWidget,
)
from qfluentwidgets import (
    Action, BodyLabel, CaptionLabel, ComboBox, FluentIcon as FIF, InfoBar, InfoBarPosition,
    LineEdit, MessageBox, MessageBoxBase, Pivot, PushButton, CheckBox, RoundMenu,
    ScrollArea, SearchLineEdit,
    SimpleCardWidget, StrongBodyLabel, SubtitleLabel, TransparentToolButton,
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
        # 官方更新日志只覆盖正式版与近代快照，远古版本不显示入口
        if on_notes is not None and vtype in ("release", "snapshot"):
            notes_btn = TransparentToolButton(FIF.INFO)
            notes_btn.setToolTip(tr("更新日志"))
            notes_btn.clicked.connect(lambda: on_notes(info, notes_btn))
            row.addWidget(notes_btn)
        row.addStretch(1)
        install_btn = PushButton(FIF.DOWNLOAD, tr("安装"))
        install_btn.setFixedHeight(30)
        install_btn.clicked.connect(lambda: on_install(info, self))
        row.addWidget(install_btn)
        layout.addLayout(row)


class PatchNoteDialog(MessageBoxBase):
    """Minecraft 官方版本更新日志（对标 HMCL 下载页 patch notes）。"""

    def __init__(self, note: dict, parent=None):
        super().__init__(parent)
        note = note or {}
        head = QHBoxLayout()
        head.addWidget(SubtitleLabel(note.get("title") or "?", self), 1)
        vtype = str(note.get("type") or "")
        if vtype:
            label = {"release": tr("正式版"), "snapshot": tr("快照")}.get(vtype, vtype)
            color = "#2FA36B" if vtype == "release" else "#E8862E"
            head.addWidget(Pill(label, color))
        self.viewLayout.addLayout(head)

        from PySide6.QtWidgets import QTextBrowser
        body = QTextBrowser(self)
        body.setOpenExternalLinks(True)
        body.setHtml(note.get("body_html") or tr("（官方正文为空）"))
        body.setMinimumSize(620, 380)
        self.viewLayout.addWidget(body, 1)

        self.yesButton.setText(tr("关闭"))
        self.cancelButton.hide()
        self.widget.setMinimumWidth(680)


class ExportPackDialog(MessageBoxBase):
    """导出整合包（.mrpack）：名称 / 版本号 / 可选目录。"""

    def __init__(self, version: str, parent=None):
        super().__init__(parent)
        self.viewLayout.addWidget(SubtitleLabel(tr("导出整合包"), self))
        hint = BodyLabel(tr("打包为 Modrinth 整合包（.mrpack）。能在 Modrinth 反查到的模组只记下载地址，其余模组和 config 等进 overrides。"), self)
        hint.setWordWrap(True)
        self.viewLayout.addWidget(hint)
        self.name = LineEdit(self)
        self.name.setPlaceholderText(tr("整合包名称"))
        self.name.setText(version)
        self.viewLayout.addWidget(self.name)
        self.ver = LineEdit(self)
        self.ver.setPlaceholderText(tr("整合包版本号"))
        self.ver.setText("1.0.0")
        self.viewLayout.addWidget(self.ver)
        self.rp = CheckBox(tr("包含资源包"), self)
        self.sp = CheckBox(tr("包含光影包"), self)
        self.viewLayout.addWidget(self.rp)
        self.viewLayout.addWidget(self.sp)
        self.yesButton.setText(tr("导出"))
        self.cancelButton.setText(tr("取消"))
        self.widget.setMinimumWidth(420)


class LoaderSwitchDialog(MessageBoxBase):
    """更换 / 移除加载器（对标 HMCL 版本设置的「自动安装」）。"""

    LOADERS = [
        ("", "原版（移除加载器）"), ("fabric", "Fabric"), ("quilt", "Quilt"),
        ("forge", "Forge"), ("neoforge", "NeoForge"),
        ("optifine", "OptiFine"), ("liteloader", "LiteLoader"),
    ]

    def __init__(self, backend, comps: dict, parent=None):
        super().__init__(parent)
        self.backend = backend
        self._rows: list[dict] = []
        self._gen = 0
        mc = comps.get("mc") or ""
        self._mc = mc
        cur = comps.get("loader") or ""
        cur_lv = comps.get("loader_version") or ""
        self._cur_lv = cur_lv

        self.viewLayout.addWidget(SubtitleLabel(tr("更换 / 移除加载器"), self))
        from mclauncher.version_components import LOADER_LABELS
        cur_label = LOADER_LABELS.get(cur, cur) if cur else tr("原版")
        cur_text = f"{cur_label} {cur_lv}".strip()
        self.viewLayout.addWidget(BodyLabel(
            f"Minecraft {mc} · " + tr("当前: {l}").format(l=cur_text), self))
        in_place = comps.get("version") != mc
        hint = CaptionLabel(
            tr("原地更换，mods、设置与存档保留；不选版本号则装最新稳定版。") if in_place
            else tr("原版规范目录不能原地加装，会生成新版本并保留原版。"), self)
        hint.setWordWrap(True)
        self.viewLayout.addWidget(hint)

        self.loader_box = ComboBox(self)
        for key, label in self.LOADERS:
            self.loader_box.addItem(tr(label) if not key else label)
        keys = [k for k, _ in self.LOADERS]
        self.loader_box.setCurrentIndex(keys.index(cur) if cur in keys else 0)
        self.viewLayout.addWidget(self.loader_box)

        self.ver_box = ComboBox(self)
        self.viewLayout.addWidget(self.ver_box)
        self.loader_box.currentIndexChanged.connect(self._reload_versions)
        self._reload_versions()

        self.yesButton.setText(tr("更换"))
        self.cancelButton.setText(tr("取消"))
        self.widget.setMinimumWidth(460)

    def _loader_key(self) -> str:
        return self.LOADERS[self.loader_box.currentIndex()][0]

    def _reload_versions(self, *_a):
        key = self._loader_key()
        self._rows = []
        self.ver_box.clear()
        if key in ("", "liteloader"):
            self.ver_box.setEnabled(False)
            return
        self._gen += 1
        gen = self._gen
        self.ver_box.setEnabled(False)
        self.ver_box.addItem(tr("加载版本列表…"))

        def ok(rows):
            import shiboken6
            if not shiboken6.isValid(self.ver_box) or gen != self._gen:
                return
            self.ver_box.clear()
            self._rows = list(rows or [])
            for r in self._rows:
                label = str(r.get("label") or r.get("id") or "")
                if not r.get("stable", True):
                    label += " " + tr("(测试版)")
                self.ver_box.addItem(label)
            self.ver_box.setEnabled(bool(self._rows))
            if not self._rows:
                self.ver_box.addItem(tr("该 MC 版本没有可用构建"))
            for i, r in enumerate(self._rows):
                if self._cur_lv and str(r.get("id")) == self._cur_lv:
                    self.ver_box.setCurrentIndex(i)
                    break

        def err(message):
            import shiboken6
            if not shiboken6.isValid(self.ver_box) or gen != self._gen:
                return
            self.ver_box.clear()
            self.ver_box.addItem(tr("版本列表获取失败，确认后装最新稳定版"))
            self.ver_box.setEnabled(False)

        self.backend.call_async(
            lambda: self.backend.list_loader_versions(self._mc, key), ok, err)

    def payload(self) -> tuple[str, str]:
        key = self._loader_key()
        idx = self.ver_box.currentIndex()
        if key and 0 <= idx < len(self._rows):
            return key, str(self._rows[idx].get("id") or "")
        return key, ""


class AssetExtractDialog(MessageBoxBase):
    """提取游戏资源（对标 PCL2 百宝箱）：音乐 / 音效 / 语言文件按真实文件名导出。"""

    def __init__(self, backend, instance: str, version: str, parent=None):
        super().__init__(parent)
        self.backend = backend
        self.instance = instance
        self.version = version
        self.extracted_names: list[str] = []
        self.viewLayout.addWidget(SubtitleLabel(tr("提取游戏资源"), self))
        hint = BodyLabel(tr("游戏资源按哈希存放，这里还原成真实文件名导出到 exports 目录。"), self)
        hint.setWordWrap(True)
        self.viewLayout.addWidget(hint)
        row = QHBoxLayout()
        self.kind = ComboBox(self)
        self._cats = backend.asset_categories()
        for c in self._cats:
            self.kind.addItem(c["label"])
        self.kind.currentIndexChanged.connect(self._refill)
        self.search = SearchLineEdit(self)
        self.search.setPlaceholderText(tr("搜索文件名…"))
        self.search.textChanged.connect(self._refill)
        row.addWidget(self.kind)
        row.addWidget(self.search, 1)
        self.viewLayout.addLayout(row)
        self.all_box = CheckBox(tr("全选"), self)
        self.all_box.toggled.connect(self._toggle_all)
        self.viewLayout.addWidget(self.all_box)
        self.listw = QListWidget(self)
        self.listw.setMinimumHeight(280)
        self.viewLayout.addWidget(self.listw)
        self.count_label = CaptionLabel("", self)
        self.viewLayout.addWidget(self.count_label)
        self.yesButton.setText(tr("提取选中"))
        self.cancelButton.setText(tr("取消"))
        self.widget.setMinimumWidth(560)
        self.yesButton.clicked.connect(self._collect)
        self._refill()

    def _refill(self, *_a):
        key = self._cats[self.kind.currentIndex()]["key"] if self._cats else "all"
        try:
            rows = self.backend.list_game_assets(
                self.instance, self.version, category=key,
                query=self.search.text().strip())
        except Exception as e:
            self.listw.clear()
            self.count_label.setText(str(e))
            return
        self.listw.clear()
        shown = rows[:800]
        for r in shown:
            size_kb = max(1, int(r.get("size") or 0) // 1024)
            suffix = "" if r.get("present") else tr("（本地缺失）")
            item = QListWidgetItem(f"{r['name']}  ({size_kb} KB){suffix}")
            item.setData(Qt.UserRole, r["name"])
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked)
            self.listw.addItem(item)
        more = len(rows) - len(shown)
        self.count_label.setText(
            tr("{n} 个文件").format(n=len(rows))
            + (tr("，仅显示前 {m} 个，可用搜索缩小范围").format(m=len(shown)) if more > 0 else ""))

    def _toggle_all(self, on):
        state = Qt.Checked if on else Qt.Unchecked
        for i in range(self.listw.count()):
            self.listw.item(i).setCheckState(state)

    def _collect(self):
        self.extracted_names = [
            self.listw.item(i).data(Qt.UserRole)
            for i in range(self.listw.count())
            if self.listw.item(i).checkState() == Qt.Checked
        ]


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
            self.grid.addWidget(
                VersionCard(v, self._install, on_notes=self._show_patch_note),
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

    def _show_patch_note(self, info: dict, btn=None):
        """取官方更新日志并弹窗（对标 HMCL 下载页 patch notes）。"""
        version = str(info.get("version") or "")
        if not version:
            return
        if btn is not None:
            btn.setEnabled(False)

        def restore():
            import shiboken6
            if btn is not None and shiboken6.isValid(btn):
                btn.setEnabled(True)

        def ok(note):
            restore()
            PatchNoteDialog(note, self.window()).exec()

        def err(message):
            restore()
            InfoBar.warning(tr("暂无更新日志"), str(message), parent=self,
                            position=InfoBarPosition.TOP, duration=4000)

        self.backend.call_async(
            lambda: self.backend.get_version_patch_note(version), ok, err)

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
        add(tr("更换 / 移除加载器…"), lambda: self._switch_loader(instance, version))
        add(tr("隐藏 / 取消隐藏"), lambda: self._hide(instance, version))
        add(tr("创建桌面快捷方式"), lambda: self._shortcut(instance, version))
        add(tr("导出启动脚本"), lambda: self.backend.export_launch_script(instance, version))
        add(tr("导出整合包…"), lambda: self._export_pack(instance, version))
        add(tr("提取游戏资源…"), lambda: self._extract_assets(instance, version))
        menu.exec(btn.mapToGlobal(btn.rect().bottomLeft()))

    def _switch_loader(self, instance, version):
        try:
            comps = self.backend.get_version_components(instance, version)
        except Exception as e:
            MessageBox(tr("无法识别版本组件"), str(e), self.window()).exec()
            return
        if not comps.get("mc"):
            MessageBox(tr("无法识别版本组件"),
                       tr("无法确定该版本对应的 Minecraft 版本"), self.window()).exec()
            return
        dlg = LoaderSwitchDialog(self.backend, comps, self.window())
        if not dlg.exec():
            return
        loader, lv = dlg.payload()
        self.backend.switch_loader(instance, version, loader, lv)
        InfoBar.success(tr("已开始更换加载器"), tr("进度见下载任务，完成后版本列表自动刷新。"),
                        parent=self, position=InfoBarPosition.TOP, duration=4000)

    def _extract_assets(self, instance, version):
        try:
            dlg = AssetExtractDialog(self.backend, instance, version, self.window())
        except Exception as e:
            MessageBox(tr("无法读取资源索引"), str(e), self).exec()
            return
        if dlg.exec() and dlg.extracted_names:
            self.backend.extract_game_assets(instance, version, dlg.extracted_names)
            InfoBar.success(
                tr("已开始提取"), tr("完成后文件在 exports 目录，进度见下载任务。"),
                parent=self, position=InfoBarPosition.TOP, duration=4000)

    def _export_pack(self, instance, version):
        dlg = ExportPackDialog(version, self.window())
        if not dlg.exec():
            return
        try:
            self.backend.export_modpack(
                instance, version,
                name=dlg.name.text().strip() or version,
                pack_version=dlg.ver.text().strip() or "1.0.0",
                include_resourcepacks=dlg.rp.isChecked(),
                include_shaderpacks=dlg.sp.isChecked(),
            )
        except Exception as e:
            MessageBox(tr("导出失败"), str(e), self).exec()
            return
        InfoBar.success(
            tr("已开始导出"), tr("完成后文件在 exports 目录，进度见下载任务。"),
            parent=self, position=InfoBarPosition.TOP, duration=4000)

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
