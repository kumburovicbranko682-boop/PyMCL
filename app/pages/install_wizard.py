# -*- coding: utf-8 -*-
"""安装向导：原版 + 主加载器 + OptiFine / LiteLoader，可选加载器版本。"""
import re

from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel, CheckBox, ComboBox, MessageBoxBase, SubtitleLabel,
)

from mclauncher.config import CONFIG
from ..ui_alive import guard
from mclauncher.i18n import tr


def liteloader_supported(mc_version: str) -> bool:
    """LiteLoader 只有 1.7–1.12 的构建，别的版本勾了也装不上。"""
    m = re.match(r"^1\.(\d+)", (mc_version or "").strip())
    return bool(m and 7 <= int(m.group(1)) <= 12)


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
        self.primary = ComboBox()
        self.primary.addItems([tr("无（原版）"), "Fabric", "Forge", "Quilt", "NeoForge"])
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

        self.optifine = CheckBox(tr("同时安装 OptiFine（Forge / 原版）"))
        self.liteloader = CheckBox(tr("同时安装 LiteLoader（1.7–1.12）"))
        # 标签写着 1.7–1.12，但以前任何版本都能勾，勾了也装不上
        if not liteloader_supported(mc_version):
            self.liteloader.setEnabled(False)
            self.liteloader.setToolTip(
                tr("LiteLoader 没有 {0} 的构建，只支持 1.7–1.12").format(mc_version))
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

    def _fill_loader(self, rows):
        cur = self.loader_ver.currentText()
        self.loader_ver.blockSignals(True)
        self.loader_ver.clear()
        self.loader_ver.addItem(tr("最新"))
        for r in rows or []:
            self.loader_ver.addItem(r.get("label") or r.get("id") or "")
        if cur and cur != tr("最新"):
            self.loader_ver.setCurrentText(cur)
        self.loader_ver.blockSignals(False)

    def _fill_opti(self, rows):
        cur = self.of_ver.currentText()
        self.of_ver.blockSignals(True)
        self.of_ver.clear()
        self.of_ver.addItem(tr("最新"))
        for r in rows or []:
            self.of_ver.addItem(r.get("label") or r.get("id") or "")
        if cur:
            self.of_ver.setCurrentText(cur)
        self.of_ver.blockSignals(False)

    def payload(self) -> dict:
        primary = self.primary.currentText()
        loader = tr("无") if primary.startswith(tr("无")) else primary
        lv = self.loader_ver.currentText()
        extra = {
            "optifine": self.optifine.isChecked(),
            "liteloader": self.liteloader.isChecked(),
            "skip_assets": self.skip_assets.isChecked(),
        }
        if lv and lv != tr("最新"):
            extra["loader_version"] = lv
        of = self.of_ver.currentText()
        if of and of != tr("最新"):
            extra["optifine_version"] = of
        return {"loader": loader, "loader_version": extra.get("loader_version") or "", "extra": extra}
