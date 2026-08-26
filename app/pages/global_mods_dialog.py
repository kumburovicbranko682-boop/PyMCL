# -*- coding: utf-8 -*-
"""全局 Mod 列表与启禁。"""
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel, InfoBar, InfoBarPosition, MessageBoxBase, SubtitleLabel,
    SwitchButton, TransparentPushButton, FluentIcon as FIF,
)
from mclauncher.i18n import tr


class GlobalModsDialog(MessageBoxBase):
    def __init__(self, backend, parent=None):
        super().__init__(parent)
        self.backend = backend
        self.viewLayout.addWidget(SubtitleLabel(tr("全局 Mod"), self))
        self.viewLayout.addWidget(BodyLabel(tr("启用的 jar 会在每次启动前链到当前版本的 mods。"), self))
        self.host = QVBoxLayout()
        wrap = QWidget(self)
        wrap.setLayout(self.host)
        self.viewLayout.addWidget(wrap)
        btns = QHBoxLayout()
        open_btn = TransparentPushButton(FIF.FOLDER, tr("打开文件夹"))
        open_btn.clicked.connect(backend.open_global_mods)
        # 空状态让人去文件夹放 jar，但对话框自己不会察觉文件变化——
        # 没有刷新手段的话，照指示做完回来界面还说「还没有全局模组」，
        # 只能关掉重开。
        refresh_btn = TransparentPushButton(FIF.SYNC, tr("刷新"))
        refresh_btn.clicked.connect(self.reload)
        btns.addWidget(open_btn)
        btns.addWidget(refresh_btn)
        btns.addStretch(1)
        btns_wrap = QWidget(self)
        btns_wrap.setLayout(btns)
        self.viewLayout.addWidget(btns_wrap)
        self.yesButton.setText(tr("关闭"))
        self.cancelButton.hide()
        self.widget.setMinimumWidth(480)
        self.reload()

    def reload(self):
        while self.host.count():
            it = self.host.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        rows = self.backend.list_global_mods() or []
        if not rows:
            self.host.addWidget(BodyLabel(tr("还没有全局模组——点「打开文件夹」放入 jar，回来点「刷新」。")))
            return
        for row in rows:
            bar = QHBoxLayout()
            name = row.get("filename") or "?"
            bar.addWidget(QLabel(name), 1)
            sw = SwitchButton()
            sw.setChecked(bool(row.get("enabled")))
            # `sw` 也必须用默认参数绑住。以前它是自由变量，循环结束后所有 lambda 拿到的
            # 都是最后一个 SwitchButton：某个 mod 启禁失败时回滚的是列表最后那个开关，
            # 用户看到的是「点 A 失败，B 自己弹回去了」。
            sw.checkedChanged.connect(lambda on, n=name, w=sw: self._toggle(n, on, w))
            wrap = QWidget()
            wrap.setLayout(bar)
            bar.addWidget(sw)
            self.host.addWidget(wrap)

    def _toggle(self, filename, enabled, switch=None):
        try:
            self.backend.set_global_mod_enabled(filename, enabled)
        except Exception as e:
            if switch is not None:
                switch.blockSignals(True)
                switch.setChecked(not enabled)
                switch.blockSignals(False)
            InfoBar.error(tr("切换失败"), str(e), parent=self,
                          position=InfoBarPosition.TOP, duration=4000)
