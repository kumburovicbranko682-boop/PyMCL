# -*- coding: utf-8 -*-
"""全局 Mod 列表与启禁。"""
from PySide6.QtCore import QTimer
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
        self._sig = None
        self.viewLayout.addWidget(SubtitleLabel(tr("全局 Mod"), self))
        self.viewLayout.addWidget(BodyLabel(tr("启用的 jar 会在每次启动前链到当前版本的 mods。"), self))
        self.host = QVBoxLayout()
        wrap = QWidget(self)
        wrap.setLayout(self.host)
        self.viewLayout.addWidget(wrap)
        open_btn = TransparentPushButton(FIF.FOLDER, tr("打开文件夹"))
        open_btn.clicked.connect(backend.open_global_mods)
        self.viewLayout.addWidget(open_btn)
        self.yesButton.setText(tr("关闭"))
        self.cancelButton.hide()
        self.widget.setMinimumWidth(480)
        self.reload()
        # 空状态让用户「打开文件夹放入 jar」，但放完回来列表不会动，
        # 像坏了一样，必须关掉重开。对话框开着就轮询目录，变了才重建。
        self._watch = QTimer(self)
        self._watch.setInterval(1500)
        self._watch.timeout.connect(self.reload)
        self._watch.start()

    def reload(self):
        rows = self.backend.list_global_mods() or []
        sig = tuple((r.get("filename"), bool(r.get("enabled"))) for r in rows)
        # 内容没变就不重建：每 1.5s 重造一遍 SwitchButton 会吃掉用户正要点的开关
        if sig == self._sig:
            return
        self._sig = sig
        while self.host.count():
            it = self.host.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        if not rows:
            self.host.addWidget(BodyLabel(tr("还没有全局模组，点「打开文件夹」放入 jar。")))
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
            # 立刻记住新状态，别让下一次轮询以为「变了」而重建列表
            if self._sig:
                self._sig = tuple(
                    (n, bool(enabled) if n == filename else e)
                    for n, e in self._sig)
        except Exception as e:
            if switch is not None:
                switch.blockSignals(True)
                switch.setChecked(not enabled)
                switch.blockSignals(False)
            InfoBar.error(tr("切换失败"), str(e), parent=self,
                          position=InfoBarPosition.TOP, duration=4000)
