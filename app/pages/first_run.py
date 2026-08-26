# -*- coding: utf-8 -*-
"""首次运行向导：游戏目录 / 下载源 / 内存 / 隔离。"""
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel, CaptionLabel, ComboBox, LineEdit, MessageBoxBase, SpinBox, SubtitleLabel,
)

from mclauncher.config import CONFIG
from mclauncher.i18n import tr
from mclauncher.version_settings import ISOLATION_HINTS, ISOLATION_LABELS


class FirstRunDialog(MessageBoxBase):
    def __init__(self, backend, parent=None):
        super().__init__(parent)
        self.backend = backend
        self.viewLayout.addWidget(SubtitleLabel(tr("欢迎使用 PyMCL"), self))
        hint = BodyLabel(tr("先选好游戏目录和下载源。这些以后都能在设置里改。"), self)
        hint.setWordWrap(True)
        self.viewLayout.addWidget(hint)

        self.game_dir = LineEdit()
        self.game_dir.setText(str(backend.get_settings().get("game_dir") or CONFIG.instances_dir))
        row = QHBoxLayout()
        row.addWidget(self.game_dir, 1)
        from qfluentwidgets import PushButton
        browse = PushButton(tr("浏览"))
        browse.clicked.connect(self._browse)
        row.addWidget(browse)
        host = QWidget(self)
        host.setLayout(row)
        self.viewLayout.addWidget(BodyLabel(tr("游戏 / 实例目录"), self))
        self.viewLayout.addWidget(host)

        self.src = ComboBox()
        self.src.addItems([tr("自动（官方慢则 BMCLAPI）"), tr("仅官方"), tr("仅 BMCLAPI")])
        self.viewLayout.addWidget(BodyLabel(tr("文件下载源"), self))
        self.viewLayout.addWidget(self.src)

        self.memory = SpinBox()
        self.memory.setRange(512, 32768)
        self.memory.setValue(int(CONFIG.get("memory_mb") or 4096))
        self.viewLayout.addWidget(BodyLabel(tr("默认内存 (MB)"), self))
        self.viewLayout.addWidget(self.memory)

        self.iso = ComboBox()
        self._iso_keys = {tr(v): k for k, v in ISOLATION_LABELS.items()}
        self.iso.addItems([tr(v) for v in ISOLATION_LABELS.values()])
        self.viewLayout.addWidget(BodyLabel(tr("新版本默认隔离"), self))
        self.viewLayout.addWidget(self.iso)
        # 「隔离」对新手是纯行话：随选项实时解释这一档会发生什么
        self.iso_hint = CaptionLabel("", self)
        self.iso_hint.setWordWrap(True)
        self.iso.currentTextChanged.connect(self._update_iso_hint)
        self._update_iso_hint()
        self.viewLayout.addWidget(self.iso_hint)

        self.yesButton.setText(tr("开始使用"))
        self.cancelButton.setText(tr("以后再说"))
        self.widget.setMinimumWidth(480)

    def _browse(self):
        path = QFileDialog.getExistingDirectory(self, tr("选择游戏目录"), self.game_dir.text())
        if path:
            self.game_dir.setText(path)

    def _update_iso_hint(self, *_a):
        key = self._iso_keys.get(self.iso.currentText(), "none")
        self.iso_hint.setText(tr(ISOLATION_HINTS.get(key, "")))

    def apply(self):
        src = {tr("自动（官方慢则 BMCLAPI）"): "auto", tr("仅官方"): "official", tr("仅 BMCLAPI"): "bmclapi"}
        data = self.backend.get_settings()
        data.update({
            "download_source": src.get(self.src.currentText(), "auto"),
            "default_memory_mb": self.memory.value(),
            "default_isolation": self._iso_keys.get(self.iso.currentText(), "none"),
            "first_run": False,
        })
        self.backend.save_settings(data)
        path = self.game_dir.text().strip()
        if path:
            try:
                self.backend.set_game_dir(path)
            except Exception:
                pass
