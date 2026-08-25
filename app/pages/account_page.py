# -*- coding: utf-8 -*-
"""账号页：微软 / 离线 / 皮肤站，带皮肤预览与皮肤 / 披风更换。"""
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel, CaptionLabel, ComboBox, FluentIcon as FIF, InfoBar, InfoBarPosition,
    LineEdit, MessageBoxBase, PasswordLineEdit, PrimaryPushButton, PushButton,
    SimpleCardWidget, StrongBodyLabel, SubtitleLabel, TransparentPushButton,
)

from ..widgets import DeviceCodeDialog, IconTile, Pill, ThumbnailTile
from ..pcl_chrome import Theme
from mclauncher.i18n import tr


class SkinVariantDialog(MessageBoxBase):
    """上传皮肤前选手臂模型（经典 / 纤细）。"""

    def __init__(self, file_name: str, current: str = "classic", parent=None):
        super().__init__(parent)
        self.viewLayout.addWidget(SubtitleLabel(tr("上传皮肤"), self))
        self.viewLayout.addWidget(BodyLabel(tr("文件: {name}").format(name=file_name), self))
        row = QHBoxLayout()
        row.addWidget(BodyLabel(tr("手臂模型"), self))
        self.variant_box = ComboBox(self)
        self.variant_box.addItem(tr("经典（Steve，粗臂）"), userData="classic")
        self.variant_box.addItem(tr("纤细（Alex，细臂）"), userData="slim")
        self.variant_box.setCurrentIndex(1 if current == "slim" else 0)
        row.addWidget(self.variant_box, 1)
        self.viewLayout.addLayout(row)
        self.viewLayout.addWidget(CaptionLabel(tr("要求 64×64（或旧版 64×32）PNG"), self))
        self.yesButton.setText(tr("上传"))
        self.cancelButton.setText(tr("取消"))
        self.widget.setMinimumWidth(360)

    def variant(self) -> str:
        return self.variant_box.currentData() or "classic"


class CapeDialog(MessageBoxBase):
    """选择要展示的披风，或隐藏披风。"""

    def __init__(self, capes: list[dict], active_cape: str, parent=None):
        super().__init__(parent)
        self.viewLayout.addWidget(SubtitleLabel(tr("更换披风"), self))
        self.cape_box = ComboBox(self)
        self.cape_box.addItem(tr("不展示披风"), userData="")
        current_idx = 0
        for i, cape in enumerate(capes):
            self.cape_box.addItem(cape.get("alias") or cape.get("id") or "?",
                                  userData=cape.get("id") or "")
            if cape.get("id") == active_cape:
                current_idx = i + 1
        self.cape_box.setCurrentIndex(current_idx)
        self.viewLayout.addWidget(self.cape_box)
        if not capes:
            self.viewLayout.addWidget(CaptionLabel(tr("这个账号还没有获得任何披风"), self))
        self.yesButton.setText(tr("应用"))
        self.cancelButton.setText(tr("取消"))
        self.widget.setMinimumWidth(320)

    def cape_id(self) -> str:
        return self.cape_box.currentData() or ""


class AccountPage(QWidget):
    def __init__(self, backend, parent=None):
        super().__init__(parent)
        self.setObjectName("accountPage")
        self.backend = backend
        self._login_dlg = None
        self._login_task = None
        self._pix_token = 0
        self._auth_busy = False
        self._skin_task = None
        self._active_name = ""

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
        self.skin_change_btn = PushButton(FIF.EDIT, tr("更换皮肤…"))
        self.skin_change_btn.clicked.connect(self._change_skin)
        sl.addWidget(self.skin_change_btn)
        skin_ops = QHBoxLayout()
        self.skin_reset_btn = TransparentPushButton(tr("重置"))
        self.skin_reset_btn.clicked.connect(self._reset_skin)
        self.cape_btn = TransparentPushButton(tr("披风…"))
        self.cape_btn.clicked.connect(self._cape)
        skin_ops.addWidget(self.skin_reset_btn)
        skin_ops.addWidget(self.cape_btn)
        sl.addLayout(skin_ops)
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
        self._load_skin(active)
        self._active_name = active["name"] if active else ""
        caps = self.backend.skin_capabilities(self._active_name) if active else {
            "can_upload": False, "can_reset": False, "can_cape": False,
            "reason": tr("请先添加账号")}
        busy = self._skin_task is not None
        self.skin_change_btn.setEnabled(bool(caps.get("can_upload")) and not busy)
        self.skin_reset_btn.setEnabled(bool(caps.get("can_reset")) and not busy)
        self.cape_btn.setEnabled(bool(caps.get("can_cape")) and not busy)
        reason = caps.get("reason") or ""
        for b in (self.skin_change_btn, self.skin_reset_btn, self.cape_btn):
            b.setToolTip("" if b.isEnabled() else reason)

    def restyle(self):
        self.skin.setStyleSheet(f"background: {Theme.hover}; border-radius: 8px;")
        self.reload()

    def _load_skin(self, row: dict | None):
        if not row:
            return
        self._pix_token += 1
        token = self._pix_token
        name = row.get("name") or ""
        kind = row.get("type") or "offline"
        body_url = row.get("body") or ""

        def fetch():
            # 正版 / 皮肤站优先本地渲染原始纹理，不依赖第三方渲染站
            if kind in ("microsoft", "authlib"):
                try:
                    return ("texture", self.backend.fetch_skin_texture(name))
                except Exception:
                    pass
            if not body_url:
                raise RuntimeError("no skin url")
            import requests
            resp = requests.get(body_url, timeout=12)
            resp.raise_for_status()
            return ("image", resp.content)

        def ok(result):
            if token != self._pix_token:
                return
            what, payload = result
            if what == "texture":
                from ..skin_render import render_front
                pix = render_front(payload.get("png") or b"",
                                   payload.get("variant") or "classic", height=256)
                if not pix.isNull():
                    self.skin.setPixmap(pix)
                    return
            else:
                pix = QPixmap()
                if pix.loadFromData(payload):
                    self.skin.setPixmap(pix.scaled(
                        140, 260, Qt.KeepAspectRatio, Qt.SmoothTransformation))

        self.backend.call_async(fetch, ok, lambda *_: None)

    # ---- 皮肤 / 披风操作

    def _start_skin_task(self, task_id: str):
        self._skin_task = task_id
        for b in (self.skin_change_btn, self.skin_reset_btn, self.cape_btn):
            b.setEnabled(False)

    def _change_skin(self):
        if self._skin_task or not self._active_name:
            return
        path, _f = QFileDialog.getOpenFileName(
            self, tr("选择皮肤文件"), "", "PNG (*.png)")
        if not path:
            return
        name = self._active_name
        row = next((r for r in self.backend.get_account_rows()
                    if r["name"] == name), None)
        if row and row.get("type") == "microsoft":
            # 后台预取当前手臂模型作为默认值；拿不到就按经典
            self.backend.call_async(
                lambda: (self.backend.get_skin_profile(name) or {}).get("variant") or "classic",
                lambda v: self._ask_variant(path, v),
                lambda *_: self._ask_variant(path, "classic"))
        else:
            self._ask_variant(path, "classic")

    def _ask_variant(self, path: str, current: str):
        if self._skin_task:
            return
        import os
        dlg = SkinVariantDialog(os.path.basename(path), current, self.window())
        if not dlg.exec():
            return
        self._start_skin_task(
            self.backend.upload_skin(self._active_name, path, dlg.variant()))

    def _reset_skin(self):
        if self._skin_task or not self._active_name:
            return
        from qfluentwidgets import MessageBox
        box = MessageBox(
            tr("重置皮肤"),
            tr("将把「{name}」恢复为默认皮肤。").format(name=self._active_name),
            self,
        )
        box.yesButton.setText(tr("重置"))
        box.cancelButton.setText(tr("取消"))
        if not box.exec():
            return
        self._start_skin_task(self.backend.reset_skin(self._active_name))

    def _cape(self):
        if self._skin_task or not self._active_name:
            return
        name = self._active_name
        self.cape_btn.setEnabled(False)
        self.cape_btn.setText(tr("加载中…"))

        def fetch():
            return self.backend.get_skin_profile(name)

        def ok(profile):
            self.cape_btn.setText(tr("披风…"))
            self.cape_btn.setEnabled(True)
            dlg = CapeDialog(profile.get("capes") or [],
                             profile.get("active_cape") or "", self.window())
            if not dlg.exec():
                return
            if dlg.cape_id() == (profile.get("active_cape") or ""):
                return
            self._start_skin_task(self.backend.set_cape(name, dlg.cape_id()))

        def err(message):
            self.cape_btn.setText(tr("披风…"))
            self.cape_btn.setEnabled(True)
            InfoBar.error(tr("获取披风失败"), str(message), parent=self,
                          position=InfoBarPosition.TOP, duration=4000)

        self.backend.call_async(fetch, ok, err)

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
        self._login_dlg.exec()
        self._login_dlg = None
        self.reload()

    def _ygg(self):
        if self._auth_busy:
            return
        api = self.api.text().strip()
        if not api:
            InfoBar.error(tr("缺少地址"), tr("请填写 Yggdrasil API"), parent=self,
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
        if task_id == self._skin_task:
            self._skin_task = None
            if success:
                InfoBar.success(tr("皮肤"), message, parent=self,
                                position=InfoBarPosition.TOP, duration=4000)
            else:
                InfoBar.error(tr("皮肤操作失败"), message, parent=self,
                              position=InfoBarPosition.TOP, duration=5000)
            self.reload()
            return
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
