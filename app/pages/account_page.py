# -*- coding: utf-8 -*-
"""账号页：微软 / 离线 / 皮肤站，带皮肤预览。"""
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel, CaptionLabel, ComboBox, FluentIcon as FIF, InfoBar, InfoBarPosition,
    LineEdit, PasswordLineEdit, PrimaryPushButton, PushButton, SimpleCardWidget,
    StrongBodyLabel, SubtitleLabel, TransparentPushButton,
)

from ..widgets import DeviceCodeDialog, IconTile, Pill, ThumbnailTile
from ..pcl_chrome import Theme
from mclauncher.i18n import tr


class AccountPage(QWidget):
    def __init__(self, backend, parent=None):
        super().__init__(parent)
        self.setObjectName("accountPage")
        self.backend = backend
        self._login_dlg = None
        self._login_task = None
        self._pix_token = 0
        self._auth_busy = False

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 20, 28, 20)
        root.setSpacing(14)
        root.addWidget(SubtitleLabel(tr("账号")))
        root.addWidget(CaptionLabel(tr("微软正版、离线、Little Skin、统一通行证 / 自建 Yggdrasil")))

        top = QHBoxLayout()
        skin_card = SimpleCardWidget(self)
        sl = QVBoxLayout(skin_card)
        sl.setContentsMargins(16, 14, 16, 14)
        self.skin = BodyLabel(tr("皮肤"))
        self.skin.setFixedSize(140, 260)
        self.skin.setAlignment(Qt.AlignCenter)
        self.skin.setStyleSheet(f"background: {Theme.hover}; border-radius: 8px;")
        sl.addWidget(self.skin, 0, Qt.AlignHCenter)
        self.skin_name = StrongBodyLabel(tr("未登录"))
        self.skin_name.setAlignment(Qt.AlignCenter)
        sl.addWidget(self.skin_name)
        top.addWidget(skin_card)

        list_card = SimpleCardWidget(self)
        ll = QVBoxLayout(list_card)
        ll.setContentsMargins(16, 14, 16, 14)
        ll.addWidget(StrongBodyLabel(tr("已保存账号")))
        self.list_box = QVBoxLayout()
        ll.addLayout(self.list_box)
        ll.addStretch(1)
        top.addWidget(list_card, 1)
        root.addLayout(top)

        ms = SimpleCardWidget(self)
        ms_l = QHBoxLayout(ms)
        ms_l.setContentsMargins(16, 12, 16, 12)
        ms_l.addWidget(StrongBodyLabel(tr("微软账号")), 1)
        btn = PrimaryPushButton(FIF.PEOPLE, tr("设备码 / 浏览器登录"))
        btn.clicked.connect(self._ms)
        ms_l.addWidget(btn)
        root.addWidget(ms)

        yg = SimpleCardWidget(self)
        yl = QVBoxLayout(yg)
        yl.setContentsMargins(16, 12, 16, 12)
        yl.addWidget(StrongBodyLabel(tr("皮肤站（authlib-injector）")))
        row = QHBoxLayout()
        self.preset = ComboBox()
        self.preset.setFixedWidth(180)
        for item in backend.authlib_presets():
            self.preset.addItem(item["name"])
        self.api = LineEdit()
        self.api.setPlaceholderText("https://littleskin.cn/api/yggdrasil")
        self.user = LineEdit()
        self.user.setPlaceholderText(tr("邮箱 / 用户名"))
        self.pw = PasswordLineEdit()
        self.pw.setPlaceholderText(tr("密码"))
        self.yg_btn = PrimaryPushButton(tr("登录皮肤站"))
        self.yg_btn.clicked.connect(self._ygg)
        row.addWidget(self.preset)
        row.addWidget(self.api, 1)
        yl.addLayout(row)
        row2 = QHBoxLayout()
        row2.addWidget(self.user)
        row2.addWidget(self.pw)
        row2.addWidget(self.yg_btn)
        yl.addLayout(row2)
        self.preset.currentTextChanged.connect(self._fill_preset)
        self._fill_preset()
        root.addWidget(yg)

        n8 = SimpleCardWidget(self)
        n8l = QVBoxLayout(n8)
        n8l.setContentsMargins(16, 12, 16, 12)
        n8l.addWidget(StrongBodyLabel(tr("统一通行证（Nide8）")))
        n8l.addWidget(CaptionLabel(tr("填 32 位服务器 ID，或把含该 ID 的链接贴进来")))
        n8row = QHBoxLayout()
        self.nide8_id = LineEdit()
        self.nide8_id.setPlaceholderText(tr("服务器 ID / 链接"))
        self.nide8_user = LineEdit()
        self.nide8_user.setPlaceholderText(tr("用户名"))
        self.nide8_pw = PasswordLineEdit()
        self.nide8_pw.setPlaceholderText(tr("密码"))
        self.n8_btn = PrimaryPushButton(tr("登录通行证"))
        self.n8_btn.clicked.connect(self._nide8)
        n8row.addWidget(self.nide8_id, 1)
        n8l.addLayout(n8row)
        n8row2 = QHBoxLayout()
        n8row2.addWidget(self.nide8_user)
        n8row2.addWidget(self.nide8_pw)
        n8row2.addWidget(self.n8_btn)
        n8l.addLayout(n8row2)
        root.addWidget(n8)

        off = SimpleCardWidget(self)
        ol = QHBoxLayout(off)
        ol.setContentsMargins(16, 12, 16, 12)
        self.offline = LineEdit()
        self.offline.setPlaceholderText(tr("离线角色名"))
        self.skin_box = ComboBox()
        self.skin_box.addItems([tr("默认"), "Steve", "Alex"])
        self.skin_box.setFixedWidth(90)
        off_btn = PushButton(tr("保存离线账号"))
        off_btn.clicked.connect(self._offline)
        ol.addWidget(StrongBodyLabel(tr("离线")), 0)
        ol.addWidget(self.offline, 1)
        ol.addWidget(self.skin_box)
        ol.addWidget(off_btn)
        root.addWidget(off)
        root.addStretch(1)

        backend.finished.connect(self._on_finished)
        backend.login_code.connect(self._on_login_code)
        backend.login_status.connect(self._on_login_status)
        self.reload()

    def _fill_preset(self, _t=""):
        name = self.preset.currentText()
        for item in self.backend.authlib_presets():
            if item["name"] == name and item.get("api"):
                self.api.setText(item["api"])

    def reload(self):
        while self.list_box.count():
            item = self.list_box.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        rows = self.backend.get_account_rows()
        if not rows:
            self.list_box.addWidget(CaptionLabel(tr("还没有正版或皮肤站账号")))
        for row in rows:
            card = QWidget()
            card.setObjectName("accCard")
            card.setStyleSheet(
                f"#accCard {{ background: transparent; border: 1px solid {Theme.line};"
                " border-radius: 8px; padding: 6px; }"
                f"#accCard:hover {{ background: {Theme.hover}; }}"
            )
            bar = QHBoxLayout(card)
            bar.setContentsMargins(8, 4, 8, 4)
            # 头像预览
            body_url = row.get("body", "")
            if body_url:
                face_url = body_url.replace("/body", "/face") if "/body" in body_url else body_url
                thumb = ThumbnailTile(row["name"], face_url, size=36)
                bar.addWidget(thumb)
            else:
                bar.addWidget(IconTile(row["name"], size=36))
            bar.addWidget(StrongBodyLabel(row["name"]))
            kind = {
                "microsoft": tr("微软"),
                "authlib": tr("皮肤站"),
                "nide8": tr("统一通行证"),
                "offline": tr("离线"),
            }.get(row["type"], row["type"])
            color = "#2E9B6B" if row["type"] == "microsoft" else (
                "#E8862E" if row["type"] == "nide8" else "#7C5CD6")
            bar.addWidget(Pill(kind, color))
            if row.get("active"):
                bar.addWidget(Pill(tr("当前"), "#4C8BF5"))
            use_btn = TransparentPushButton(tr("使用"))
            use_btn.clicked.connect(lambda _, n=row["name"]: self._use(n))
            del_btn = TransparentPushButton(FIF.DELETE, tr("删除"))
            del_btn.clicked.connect(lambda _, n=row["name"]: self._delete(n))
            bar.addStretch(1)
            bar.addWidget(use_btn)
            bar.addWidget(del_btn)
            self.list_box.addWidget(card)
        active = next((r for r in rows if r.get("active")), None) or (rows[0] if rows else None)
        self.skin_name.setText(active["name"] if active else "Steve")
        self._load_skin(active["body"] if active else "")

    def restyle(self):
        self.skin.setStyleSheet(f"background: {Theme.hover}; border-radius: 8px;")
        self.reload()

    def _load_skin(self, url: str):
        if not url:
            return
        self._pix_token += 1
        token = self._pix_token

        def fetch():
            import requests
            resp = requests.get(url, timeout=12)
            resp.raise_for_status()
            return resp.content

        def ok(data):
            if token != self._pix_token:
                return
            pix = QPixmap()
            if pix.loadFromData(data):
                self.skin.setPixmap(pix.scaled(140, 260, Qt.KeepAspectRatio, Qt.SmoothTransformation))

        self.backend.call_async(fetch, ok, lambda *_: None)

    def _delete(self, name):
        from qfluentwidgets import MessageBox
        box = MessageBox(
            tr("删除账号"),
            tr("将删除账号「{name}」。若为微软账号，刷新令牌也会一并丢失，需重新走设备码 / 浏览器登录。").format(
                name=name),
            self,
        )
        box.yesButton.setText(tr("删除"))
        box.cancelButton.setText(tr("取消"))
        if not box.exec():
            return
        self.backend.remove_account(name)
        self.reload()

    def _use(self, name):
        self.backend.set_active_account(name)
        self.reload()

    def _set_auth_busy(self, busy: bool):
        self._auth_busy = busy
        self.yg_btn.setEnabled(not busy)
        self.n8_btn.setEnabled(not busy)
        self.yg_btn.setText(tr("登录中…") if busy else tr("登录皮肤站"))
        self.n8_btn.setText(tr("登录中…") if busy else tr("登录通行证"))

    def _offline(self):
        name = self.offline.text().strip()
        if not name:
            InfoBar.error(tr("缺少名字"), tr("请填写离线角色名"), parent=self,
                          position=InfoBarPosition.TOP, duration=2500)
            return
        self.backend.add_offline_account(
            name, {"Steve": "steve", "Alex": "alex"}.get(self.skin_box.currentText(), "default"))
        self.reload()

    def _ms(self):
        if self._login_dlg:
            return
        self._login_dlg = DeviceCodeDialog(self.window())
        self._login_task = self.backend.start_microsoft_login()
        accepted = self._login_dlg.exec()
        self._login_dlg = None
        # 关掉设备码框 = 放弃登录：不取消的话轮询会一直问微软要令牌，
        # 直到超时凭空弹一条「登录失败」（启动页早修了，这里是同一个坑）。
        if not accepted and self._login_task:
            cancel = getattr(self.backend, "cancel_task", None)
            if callable(cancel):
                try:
                    cancel(self._login_task)
                except Exception:
                    pass
            self._login_task = None
        self.reload()

    def _ygg(self):
        if self._auth_busy:
            return
        api = self.api.text().strip()
        if not api:
            InfoBar.error(tr("缺少地址"), tr("请填写 Yggdrasil API"), parent=self,
                          position=InfoBarPosition.TOP, duration=3000)
            return
        # 空凭据不必发给服务器换一句英文报错，本地就说清楚
        if not self.user.text().strip() or not self.pw.text():
            InfoBar.error(tr("缺少账号信息"), tr("请填写邮箱 / 用户名和密码"), parent=self,
                          position=InfoBarPosition.TOP, duration=3000)
            return
        self._set_auth_busy(True)
        self._login_task = self.backend.start_authlib_login(
            api, self.user.text().strip(), self.pw.text())

    def _nide8(self):
        if self._auth_busy:
            return
        sid = self.nide8_id.text().strip()
        if not sid:
            InfoBar.error(tr("缺少服务器 ID"), tr("请填写统一通行证服务器 ID"), parent=self,
                          position=InfoBarPosition.TOP, duration=3000)
            return
        if not self.nide8_user.text().strip() or not self.nide8_pw.text():
            InfoBar.error(tr("缺少账号信息"), tr("请填写用户名和密码"), parent=self,
                          position=InfoBarPosition.TOP, duration=3000)
            return
        self._set_auth_busy(True)
        self._login_task = self.backend.start_nide8_login(
            sid, self.nide8_user.text().strip(), self.nide8_pw.text())

    def _on_login_code(self, code, uri):
        if self._login_dlg:
            self._login_dlg.show_code(code, uri)

    def _on_login_status(self, text):
        if self._login_dlg:
            self._login_dlg.show_status(text)

    def _on_finished(self, task_id, success, message):
        if task_id != self._login_task:
            return
        if self._auth_busy:
            self._set_auth_busy(False)
        if self._login_dlg and success:
            self._login_dlg.accept()
        if success:
            InfoBar.success(tr("登录成功"), message, parent=self,
                            position=InfoBarPosition.TOP, duration=2500)
            self.reload()
        elif message != tr("已取消"):
            InfoBar.error(tr("登录失败"), message, parent=self,
                          position=InfoBarPosition.TOP, duration=4000)
