# -*- coding: utf-8 -*-
"""首次运行向导：只确认游戏目录和下载源。

内存、隔离等有可靠默认值的选项不在这里问——第一次打开的人还没有
任何版本，无法回答「新版本默认隔离」这种问题；这些都在 设置 页可改。
"""
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QWidget
from qfluentwidgets import BodyLabel, ComboBox, LineEdit, MessageBoxBase, SubtitleLabel

from mclauncher.config import CONFIG
from mclauncher.i18n import tr


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

        self.yesButton.setText(tr("开始使用"))
        self.cancelButton.setText(tr("以后再说"))
        self.widget.setMinimumWidth(480)

    def _browse(self):
        path = QFileDialog.getExistingDirectory(self, tr("选择游戏目录"), self.game_dir.text())
        if path:
            self.game_dir.setText(path)

    def apply(self):
        src = {tr("自动（官方慢则 BMCLAPI）"): "auto", tr("仅官方"): "official", tr("仅 BMCLAPI"): "bmclapi"}
        data = self.backend.get_settings()
        data.update({
            "download_source": src.get(self.src.currentText(), "auto"),
            "first_run": False,
        })
        self.backend.save_settings(data)
        path = self.game_dir.text().strip()
        if path:
            try:
                self.backend.set_game_dir(path)
            except Exception:
                pass
