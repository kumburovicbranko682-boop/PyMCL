# -*- coding: utf-8 -*-
"""安装向导：原版 + 主加载器 + OptiFine / LiteLoader，可选加载器版本。"""
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel, CheckBox, ComboBox, LineEdit, MessageBoxBase, SubtitleLabel,
)

from mclauncher.config import CONFIG
from ..ui_alive import guard
from mclauncher.i18n import tr


class InstallWizardDialog(MessageBoxBase):
    def __init__(self, backend, mc_version: str, instance: str, parent=None):
        super().__init__(parent)
        self.backend = backend
        self.mc_version = mc_version
        self.instance = instance
        self._dismissed = False
        self.viewLayout.addWidget(SubtitleLabel(tr("安装 {0}").format(mc_version), self))
        hint = BodyLabel(tr("主加载器只能选一个。Forge 可同时勾选 OptiFine（放入 mods）。"), self)
        hint.setWordWrap(True)
        self.viewLayout.addWidget(hint)

        form = QWidget(self)
        lay = QVBoxLayout(form)
        lay.setContentsMargins(0, 8, 0, 0)
        # 版本名称（HMCL/PCL2 安装新游戏同款）：留空用默认自动名
        self.name_edit = LineEdit()
        self.name_edit.setPlaceholderText(tr("留空使用默认名（如 {mc}）").format(mc=mc_version))
        row0 = QHBoxLayout()
        row0.addWidget(BodyLabel(tr("版本名称")))
        row0.addWidget(self.name_edit, 1)
        lay.addLayout(row0)
        self.primary = ComboBox()
        loaders = [tr("无（原版）"), "Fabric", "Forge", "Quilt", "NeoForge"]
        if mc_version == "1.12.2":
            # Cleanroom：1.12.2 专属的现代化 Forge 分支（HMCL 3.7 同款）
            loaders.append("Cleanroom")
        self.primary.addItems(loaders)
        self.loader_ver = ComboBox()
        self.loader_ver.setMinimumWidth(280)
        self.loader_ver.addItem(tr("最新"))
        row = QHBoxLayout()
        row.addWidget(BodyLabel(tr("主加载器")))
        row.addWidget(self.primary, 1)
        lay.addLayout(row)
        row2 = QHBoxLayout()
        row2.addWidget(BodyLabel(tr("加载器版本")))
        row2.addWidget(self.loader_ver, 1)
        lay.addLayout(row2)

        # Fabric API / QSL 随装（HMCL 安装页同款可选组件）
        self.fabric_api = CheckBox(tr("同时安装 Fabric API（多数 Fabric 模组的必备前置）"))
        self.fabric_api.setChecked(True)
        lay.addWidget(self.fabric_api)
        self.optifine = CheckBox(tr("同时安装 OptiFine（Forge / 原版）"))
        self.liteloader = CheckBox(tr("同时安装 LiteLoader（1.7–1.12）"))
        self.skip_assets = CheckBox(tr("跳过资源文件校验（加快重装）"))
        self.skip_assets.setChecked(bool(CONFIG.get("skip_assets")))
        self.of_ver = ComboBox()
        self.of_ver.addItem(tr("最新"))
        lay.addWidget(self.optifine)
        row3 = QHBoxLayout()
        row3.addWidget(BodyLabel("OptiFine"))
        row3.addWidget(self.of_ver, 1)
        lay.addLayout(row3)
        lay.addWidget(self.liteloader)
        lay.addWidget(self.skip_assets)
        self.viewLayout.addWidget(form)

        self.yesButton.setText(tr("开始安装"))
        self.cancelButton.setText(tr("取消"))
        self.widget.setMinimumWidth(520)
        self.primary.currentTextChanged.connect(self._reload_loaders)
        self.optifine.toggled.connect(self._sync)
        self._reload_loaders()

    def reject(self):
        self._dismissed = True
        super().reject()

    def accept(self):
        self._dismissed = True
        super().accept()

    def _sync(self):
        primary = self.primary.currentText()
        of_ok = primary.startswith(tr("无")) or primary == "Forge"
        self.optifine.setEnabled(of_ok)
        if not of_ok:
            self.optifine.setChecked(False)
        fab_ok = primary in ("Fabric", "Quilt")
        self.fabric_api.setEnabled(fab_ok)
        self.fabric_api.setText(
            tr("同时安装 QSL / Quilted Fabric API（多数 Quilt 模组的必备前置）")
            if primary == "Quilt"
            else tr("同时安装 Fabric API（多数 Fabric 模组的必备前置）"))

    def _reload_loaders(self):
        self._sync()
        name = self.primary.currentText()
        loader = tr("无") if name.startswith(tr("无")) else name
        self.loader_ver.clear()
        self.loader_ver.addItem(tr("最新"))
        call_async = getattr(self.backend, "call_async", None)
        if loader != tr("无") and callable(call_async):
            call_async(
                lambda: self.backend.list_loader_versions(self.mc_version, loader),
                guard(self, self._fill_loader),
                lambda _e: None,
            )
        of_ok = name.startswith(tr("无")) or name == "Forge"
        if of_ok and callable(call_async):
            call_async(
                lambda: self.backend.list_loader_versions(self.mc_version, "OptiFine"),
                guard(self, self._fill_opti),
                lambda _e: None,
            )
        elif not of_ok:
            self.of_ver.clear()
            self.of_ver.addItem(tr("最新"))

    @staticmethod
    def _decorate(row: dict) -> str:
        """给版本行加「推荐 / 测试版」标注（PCL2/HMCL 同款），只影响显示文本。"""
        base = str(row.get("label") or row.get("id") or "")
        marks = []
        if row.get("recommended"):
            marks.append(tr("推荐"))
        if not row.get("stable", True):
            marks.append(tr("测试版"))
        return f"{base}（{'，'.join(marks)}）" if marks else base

    def _fill_loader(self, rows):
        prev = self.loader_ver.currentData()
        self.loader_ver.blockSignals(True)
        self.loader_ver.clear()
        self.loader_ver.addItem(tr("最新"))
        for r in rows or []:
            rid = str(r.get("id") or r.get("label") or "")
            self.loader_ver.addItem(self._decorate(r), userData=rid)
        if prev:
            idx = self.loader_ver.findData(prev)
            if idx >= 0:
                self.loader_ver.setCurrentIndex(idx)
        self.loader_ver.blockSignals(False)

    def _fill_opti(self, rows):
        prev = self.of_ver.currentData()
        self.of_ver.blockSignals(True)
        self.of_ver.clear()
        self.of_ver.addItem(tr("最新"))
        for r in rows or []:
            rid = str(r.get("id") or r.get("label") or "")
            self.of_ver.addItem(self._decorate(r), userData=rid)
        if prev:
            idx = self.of_ver.findData(prev)
            if idx >= 0:
                self.of_ver.setCurrentIndex(idx)
        self.of_ver.blockSignals(False)

    def payload(self) -> dict:
        primary = self.primary.currentText()
        loader = tr("无") if primary.startswith(tr("无")) else primary
        # 版本号从 userData 取（显示文本带「推荐/测试版」标注，不能直接当版本号用）
        lv = self.loader_ver.currentData()
        if lv is None:
            txt = self.loader_ver.currentText()
            lv = "" if txt == tr("最新") else txt
        extra = {
            "optifine": self.optifine.isChecked(),
            "liteloader": self.liteloader.isChecked(),
            "skip_assets": self.skip_assets.isChecked(),
            "fabric_api": self.fabric_api.isEnabled() and self.fabric_api.isChecked(),
        }
        if lv:
            extra["loader_version"] = str(lv)
        of = self.of_ver.currentData()
        if of is None:
            txt = self.of_ver.currentText()
            of = "" if txt == tr("最新") else txt
        if of:
            extra["optifine_version"] = str(of)
        name = self.name_edit.text().strip()
        if name:
            extra["custom_name"] = name
        return {"loader": loader, "loader_version": extra.get("loader_version") or "", "extra": extra}
