# -*- coding: utf-8 -*-
"""账号页：微软 / 离线 / 皮肤站，带皮肤预览与皮肤管理。"""
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QLabel, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel, CaptionLabel, ComboBox, FluentIcon as FIF, InfoBar, InfoBarPosition,
    LineEdit, MessageBoxBase, PasswordLineEdit, PrimaryPushButton, PushButton,
    SimpleCardWidget, StrongBodyLabel, SubtitleLabel, TransparentPushButton,
)

from ..widgets import DeviceCodeDialog, IconTile, Pill, ThumbnailTile
from ..pcl_chrome import Theme
from ..ui_alive import guard
from mclauncher.i18n import tr


class PlayerLookupDialog(MessageBoxBase):
    """玩家档案查询（PCL2 百宝箱「IGN / UUID 查询」同款）。

    输入任意正版玩家名或 UUID，展示档案、皮肤模型与预览。
    """

    def __init__(self, backend, parent=None):
        super().__init__(parent)
        self.backend = backend
        self._profile = {}
        self._pix_token = 0
        self.viewLayout.addWidget(SubtitleLabel(tr("玩家档案查询"), self))
        self.viewLayout.addWidget(CaptionLabel(
            tr("输入正版玩家名或 UUID（带不带连字符都行）"), self))
        row = QHBoxLayout()
        self.query = LineEdit(self)
        self.query.setPlaceholderText("Notch")
        self.query.setClearButtonEnabled(True)
        self.query.returnPressed.connect(self._go)
        self.go_btn = PushButton(tr("查询"), self)
        self.go_btn.clicked.connect(self._go)
        row.addWidget(self.query, 1)
        row.addWidget(self.go_btn)
        host = QWidget(self)
        host.setLayout(row)
        self.viewLayout.addWidget(host)

        result = QHBoxLayout()
        self.body = BodyLabel("")
        self.body.setFixedSize(120, 240)
        self.body.setAlignment(Qt.AlignCenter)
        self.body.setStyleSheet(f"background: {Theme.hover}; border-radius: 8px;")
        result.addWidget(self.body)
        info = QVBoxLayout()
        self.name_label = StrongBodyLabel("—", self)
        self.uuid_label = CaptionLabel("", self)
        self.uuid_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.meta_label = CaptionLabel("", self)
        info.addWidget(self.name_label)
        info.addWidget(self.uuid_label)
        info.addWidget(self.meta_label)
        btns = QHBoxLayout()
        self.copy_btn = PushButton(tr("复制 UUID"), self)
        self.copy_btn.setEnabled(False)
        self.copy_btn.clicked.connect(self._copy_uuid)
        self.skin_dl_btn = PushButton(tr("打开皮肤 PNG"), self)
        self.skin_dl_btn.setEnabled(False)
        self.skin_dl_btn.clicked.connect(self._open_skin)
        btns.addWidget(self.copy_btn)
        btns.addWidget(self.skin_dl_btn)
        btns.addStretch(1)
        info.addLayout(btns)
        info.addStretch(1)
        result.addLayout(info, 1)
        result_host = QWidget(self)
        result_host.setLayout(result)
        self.viewLayout.addWidget(result_host)

        self.yesButton.setText(tr("关闭"))
        self.cancelButton.hide()
        self.widget.setMinimumWidth(520)

    def _go(self):
        q = self.query.text().strip()
        if not q:
            return
        self.go_btn.setEnabled(False)
        self.go_btn.setText(tr("查询中…"))

        def ok(profile):
            self.go_btn.setEnabled(True)
            self.go_btn.setText(tr("查询"))
            self._profile = profile or {}
            self.name_label.setText(self._profile.get("name") or "—")
            self.uuid_label.setText(self._profile.get("uuid") or "")
            bits = [tr("细臂 (slim)") if self._profile.get("variant") == "slim"
                    else tr("粗臂 (classic)")]
            bits.append(tr("有披风") if self._profile.get("cape_url") else tr("无披风"))
            self.meta_label.setText(" · ".join(bits))
            self.copy_btn.setEnabled(bool(self._profile.get("uuid")))
            self.skin_dl_btn.setEnabled(bool(self._profile.get("skin_url")))
            self._load_body(self._profile.get("body") or "")

        def fail(exc):
            self.go_btn.setEnabled(True)
            self.go_btn.setText(tr("查询"))
            InfoBar.error(tr("查询失败"), str(exc), parent=self,
                          position=InfoBarPosition.TOP, duration=4000)

        self.backend.call_async(lambda: self.backend.lookup_player(q), ok, fail)

    def _load_body(self, url: str):
        self.body.setText(tr("加载预览…") if url else "")
        if not url:
            self.body.setPixmap(QPixmap())
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
                self.body.setText("")
                self.body.setPixmap(pix.scaled(
                    120, 240, Qt.KeepAspectRatio, Qt.SmoothTransformation))

        self.backend.call_async(fetch, ok, lambda *_: self.body.setText(""))

    def _copy_uuid(self):
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(self._profile.get("uuid") or "")
        InfoBar.success(tr("已复制"), self._profile.get("uuid") or "", parent=self,
                        position=InfoBarPosition.TOP, duration=2000)

    def _open_skin(self):
        url = self._profile.get("skin_url") or ""
        if url:
            QDesktopServices.openUrl(QUrl(url))


class SkinManagerDialog(MessageBoxBase):
    """微软账号皮肤 / 披风管理：上传、重置、启用/隐藏披风。"""

    def __init__(self, backend, account_name: str, parent=None):
        super().__init__(parent)
        self.backend = backend
        self.account = account_name
        self._capes = []
        self._busy = False

        self.viewLayout.addWidget(SubtitleLabel(tr("管理皮肤"), self))
        self.status = BodyLabel(tr("正在读取当前皮肤…"), self)
        self.status.setWordWrap(True)
        self.viewLayout.addWidget(self.status)

        row = QHBoxLayout()
        row.addWidget(BodyLabel(tr("模型"), self))
        self.variant_box = ComboBox(self)
        self.variant_box.addItems([tr("经典（粗臂）"), tr("纤细（细臂）")])
        self.variant_box.setFixedWidth(150)
        row.addWidget(self.variant_box)
        self.upload_btn = PushButton(FIF.PHOTO, tr("上传皮肤 PNG…"), self)
        self.upload_btn.clicked.connect(self._pick_and_upload)
        row.addWidget(self.upload_btn)
        self.reset_btn = PushButton(tr("重置为默认"), self)
        self.reset_btn.clicked.connect(self._reset)
        row.addWidget(self.reset_btn)
        row.addStretch(1)
        host = QWidget(self)
        host.setLayout(row)
        self.viewLayout.addWidget(host)

        cape_row = QHBoxLayout()
        cape_row.addWidget(BodyLabel(tr("披风"), self))
        self.cape_box = ComboBox(self)
        self.cape_box.setFixedWidth(220)
        self.cape_box.addItem(tr("（读取中…）"))
        self.cape_box.setEnabled(False)
        cape_row.addWidget(self.cape_box)
        self.cape_btn = PushButton(tr("应用披风"), self)
        self.cape_btn.setEnabled(False)
        self.cape_btn.clicked.connect(self._apply_cape)
        cape_row.addWidget(self.cape_btn)
        cape_row.addStretch(1)
        cape_host = QWidget(self)
        cape_host.setLayout(cape_row)
        self.viewLayout.addWidget(cape_host)

        tip = CaptionLabel(tr("皮肤要求 64x64（旧版 64x32）PNG。上传后游戏与预览可能需要几分钟生效。"), self)
        tip.setWordWrap(True)
        self.viewLayout.addWidget(tip)

        self.yesButton.setText(tr("关闭"))
        self.cancelButton.hide()
        self.widget.setMinimumWidth(520)
        self._refresh()

    # ---- 数据

    def _refresh(self):
        self.backend.call_async(
            lambda: self.backend.get_skin_profile(self.account),
            guard(self, self._on_profile), guard(self, self._on_err))

    def _on_profile(self, profile: dict):
        profile = profile or {}
        variant = profile.get("variant") or "classic"
        self.variant_box.setCurrentIndex(1 if variant == "slim" else 0)
        self._capes = list(profile.get("capes") or [])
        self.cape_box.clear()
        self.cape_box.addItem(tr("隐藏披风"))
        active_idx = 0
        for i, cape in enumerate(self._capes):
            self.cape_box.addItem(cape.get("alias") or "?")
            if cape.get("active"):
                active_idx = i + 1
        self.cape_box.setCurrentIndex(active_idx)
        has_capes = bool(self._capes)
        self.cape_box.setEnabled(has_capes)
        self.cape_btn.setEnabled(has_capes)
        model = tr("纤细") if variant == "slim" else tr("经典")
        parts = [tr("当前模型：{model}").format(model=model)]
        if profile.get("skin_url"):
            parts.append(tr("已设置自定义皮肤"))
        else:
            parts.append(tr("使用默认皮肤"))
        if not has_capes:
            parts.append(tr("该账号没有可用披风"))
        self.status.setText("  ·  ".join(parts))
        self._set_busy(False)

    def _on_err(self, err):
        self.status.setText(tr("操作失败：{err}").format(err=err))
        self._set_busy(False)

    def _set_busy(self, busy: bool):
        self._busy = busy
        self.upload_btn.setEnabled(not busy)
        self.reset_btn.setEnabled(not busy)
        if busy:
            self.cape_btn.setEnabled(False)
        elif self._capes:
            self.cape_btn.setEnabled(True)

    # ---- 操作

    def _pick_and_upload(self):
        if self._busy:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, tr("选择皮肤 PNG"), "", "PNG (*.png)")
        if not path:
            return
        variant = "slim" if self.variant_box.currentIndex() == 1 else "classic"
        self._set_busy(True)
        self.status.setText(tr("正在上传皮肤…"))
        self.backend.call_async(
            lambda: self.backend.upload_skin(self.account, path, variant),
            guard(self, self._on_changed), guard(self, self._on_err))

    def _reset(self):
        if self._busy:
            return
        self._set_busy(True)
        self.status.setText(tr("正在重置皮肤…"))
        self.backend.call_async(
            lambda: self.backend.reset_skin(self.account),
            guard(self, self._on_changed), guard(self, self._on_err))

    def _apply_cape(self):
        if self._busy:
            return
        idx = self.cape_box.currentIndex()
        cape_id = ""
        if idx > 0 and idx - 1 < len(self._capes):
            cape_id = self._capes[idx - 1].get("id") or ""
        self._set_busy(True)
        self.status.setText(tr("正在更新披风…"))
        self.backend.call_async(
            lambda: self.backend.set_cape(self.account, cape_id),
            guard(self, self._on_changed), guard(self, self._on_err))

    def _on_changed(self, profile: dict):
        InfoBar.success(tr("已更新"), tr("皮肤设置已保存，游戏内可能需要几分钟生效。"),
                        parent=self, position=InfoBarPosition.TOP, duration=3000)
        self._on_profile(profile)


class OfflineSkinDialog(MessageBoxBase):
    """离线账户皮肤：选择本地 PNG / 抓取正版玩家皮肤，启动时经本地皮肤服务注入。"""

    def __init__(self, backend, account_name: str, parent=None):
        super().__init__(parent)
        self.backend = backend
        self.account = account_name
        self._busy = False

        self.viewLayout.addWidget(SubtitleLabel(tr("离线皮肤"), self))
        self.status = BodyLabel(tr("正在读取皮肤配置…"), self)
        self.status.setWordWrap(True)
        self.viewLayout.addWidget(self.status)

        # 皮肤 2D 立绘（本地渲染，PCL2/HMCL 同款）
        self.preview = QLabel(self)
        self.preview.setFixedSize(96, 192)
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setStyleSheet("background: transparent;")
        self.preview.hide()
        prev_row = QHBoxLayout()
        prev_row.addStretch(1)
        prev_row.addWidget(self.preview)
        prev_row.addStretch(1)
        prev_host = QWidget(self)
        prev_host.setLayout(prev_row)
        self.viewLayout.addWidget(prev_host)

        row = QHBoxLayout()
        row.addWidget(BodyLabel(tr("模型"), self))
        self.model_box = ComboBox(self)
        self.model_box.addItems([tr("经典（粗臂）"), tr("纤细（细臂）")])
        self.model_box.setFixedWidth(150)
        self.model_box.activated.connect(self._apply_model)
        row.addWidget(self.model_box)
        self.skin_btn = PushButton(FIF.PHOTO, tr("选择皮肤 PNG…"), self)
        self.skin_btn.clicked.connect(self._pick_skin)
        row.addWidget(self.skin_btn)
        self.cape_btn = PushButton(tr("选择披风 PNG…"), self)
        self.cape_btn.clicked.connect(self._pick_cape)
        row.addWidget(self.cape_btn)
        row.addStretch(1)
        host = QWidget(self)
        host.setLayout(row)
        self.viewLayout.addWidget(host)

        fetch_row = QHBoxLayout()
        self.player_edit = LineEdit(self)
        self.player_edit.setPlaceholderText(tr("正版玩家名，例如 Notch"))
        self.player_edit.setFixedWidth(220)
        fetch_row.addWidget(self.player_edit)
        self.fetch_btn = PushButton(FIF.DOWNLOAD, tr("抓取该玩家皮肤"), self)
        self.fetch_btn.clicked.connect(self._fetch_premium)
        fetch_row.addWidget(self.fetch_btn)
        self.clear_btn = PushButton(tr("清除皮肤"), self)
        self.clear_btn.clicked.connect(self._clear)
        fetch_row.addWidget(self.clear_btn)
        fetch_row.addStretch(1)
        fetch_host = QWidget(self)
        fetch_host.setLayout(fetch_row)
        self.viewLayout.addWidget(fetch_host)

        tip = CaptionLabel(
            tr("启动时通过本地皮肤服务 + authlib-injector 注入，进入游戏即可看到。皮肤要求 64x64（旧版 64x32）PNG。"),
            self)
        tip.setWordWrap(True)
        self.viewLayout.addWidget(tip)

        uuid_row = QHBoxLayout()
        uuid_row.addWidget(BodyLabel("UUID", self))
        self.uuid_edit = LineEdit(self)
        self.uuid_edit.setPlaceholderText(tr("32 位十六进制，可带连字符"))
        uuid_row.addWidget(self.uuid_edit, 1)
        self.uuid_save_btn = PushButton(tr("保存 UUID"), self)
        self.uuid_save_btn.clicked.connect(self._save_uuid)
        uuid_row.addWidget(self.uuid_save_btn)
        self.uuid_reset_btn = PushButton(tr("重置"), self)
        self.uuid_reset_btn.clicked.connect(self._reset_uuid)
        uuid_row.addWidget(self.uuid_reset_btn)
        uuid_host = QWidget(self)
        uuid_host.setLayout(uuid_row)
        self.viewLayout.addWidget(uuid_host)
        uuid_tip = CaptionLabel(
            tr("从旧启动器迁移或进有白名单绑定的离线服时，填旧 UUID 可保住玩家数据。留空重置为按用户名推导的默认值。"),
            self)
        uuid_tip.setWordWrap(True)
        self.viewLayout.addWidget(uuid_tip)

        self.yesButton.setText(tr("关闭"))
        self.cancelButton.hide()
        self.widget.setMinimumWidth(520)
        self._refresh()

    # ---- 数据

    def _refresh(self):
        self.backend.call_async(
            lambda: self.backend.get_offline_skin(self.account),
            guard(self, self._on_config), guard(self, self._on_err))

    def _on_config(self, cfg: dict):
        cfg = cfg or {}
        self.model_box.setCurrentIndex(1 if cfg.get("model") == "slim" else 0)
        parts = []
        if cfg.get("skin_file"):
            from pathlib import Path as _P
            parts.append(tr("皮肤：{name}").format(name=_P(cfg["skin_file"]).name))
        else:
            parts.append(tr("未设置皮肤（游戏内为默认 Steve/Alex）"))
        if cfg.get("cape_file"):
            parts.append(tr("已设置披风"))
        self.status.setText("  ·  ".join(parts))
        if cfg.get("uuid") and not self.uuid_edit.text().strip():
            self.uuid_edit.setText(cfg["uuid"])
        self._render_preview(cfg)
        self._set_busy(False)

    def _render_preview(self, cfg: dict):
        """本地渲染皮肤立绘；没皮肤或渲染失败就藏起来，不打扰主流程。"""
        skin_file = cfg.get("skin_file") or ""
        model = cfg.get("model") or "default"
        if not skin_file:
            self.preview.hide()
            return
        self.backend.call_async(
            lambda: self.backend.render_skin_preview(skin_file, model=model, scale=6),
            guard(self, self._show_preview),
            guard(self, lambda _e: self.preview.hide()))

    def _show_preview(self, path: str):
        pix = QPixmap(str(path or ""))
        if pix.isNull():
            self.preview.hide()
            return
        self.preview.setPixmap(pix.scaled(
            self.preview.size(), Qt.KeepAspectRatio, Qt.FastTransformation))
        self.preview.show()

    def _on_err(self, err):
        self.status.setText(tr("操作失败：{err}").format(err=err))
        self._set_busy(False)

    def _set_busy(self, busy: bool):
        self._busy = busy
        for btn in (self.skin_btn, self.cape_btn, self.fetch_btn, self.clear_btn,
                    self.uuid_save_btn, self.uuid_reset_btn):
            btn.setEnabled(not busy)

    def _on_changed(self, cfg: dict):
        InfoBar.success(tr("已更新"), tr("离线皮肤已保存，下次启动生效。"),
                        parent=self, position=InfoBarPosition.TOP, duration=3000)
        self._on_config(cfg)

    # ---- 操作

    def _model_value(self) -> str:
        return "slim" if self.model_box.currentIndex() == 1 else "default"

    def _save_uuid(self):
        if self._busy:
            return
        raw = self.uuid_edit.text().strip()
        self._set_busy(True)
        self.backend.call_async(
            lambda: self.backend.set_offline_uuid(self.account, raw),
            guard(self, self._on_uuid_saved), guard(self, self._on_err))

    def _reset_uuid(self):
        if self._busy:
            return
        self._set_busy(True)
        self.backend.call_async(
            lambda: self.backend.set_offline_uuid(self.account, ""),
            guard(self, self._on_uuid_saved), guard(self, self._on_err))

    def _on_uuid_saved(self, uuid: str):
        self.uuid_edit.setText(uuid or "")
        InfoBar.success(tr("已更新"), tr("UUID 已保存，下次启动生效。"),
                        parent=self, position=InfoBarPosition.TOP, duration=3000)
        self._set_busy(False)

    def _apply_model(self, _idx: int = 0):
        if self._busy:
            return
        self._set_busy(True)
        model = self._model_value()
        self.backend.call_async(
            lambda: self.backend.set_offline_skin(self.account, model=model),
            guard(self, self._on_config), guard(self, self._on_err))

    def _pick_skin(self):
        if self._busy:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, tr("选择皮肤 PNG"), "", "PNG (*.png)")
        if not path:
            return
        self._set_busy(True)
        self.status.setText(tr("正在保存皮肤…"))
        model = self._model_value()
        self.backend.call_async(
            lambda: self.backend.set_offline_skin(self.account, skin_path=path, model=model),
            guard(self, self._on_changed), guard(self, self._on_err))

    def _pick_cape(self):
        if self._busy:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, tr("选择披风 PNG"), "", "PNG (*.png)")
        if not path:
            return
        self._set_busy(True)
        self.status.setText(tr("正在保存披风…"))
        self.backend.call_async(
            lambda: self.backend.set_offline_skin(self.account, cape_path=path),
            guard(self, self._on_changed), guard(self, self._on_err))

    def _fetch_premium(self):
        if self._busy:
            return
        player = (self.player_edit.text() or "").strip()
        if not player:
            InfoBar.warning(tr("请输入玩家名"), tr("填写要抓取皮肤的正版玩家名。"),
                            parent=self, position=InfoBarPosition.TOP, duration=3000)
            return
        self._set_busy(True)
        self.status.setText(tr("正在抓取皮肤…"))
        self.backend.call_async(
            lambda: self.backend.fetch_offline_skin_premium(self.account, player),
            guard(self, self._on_changed), guard(self, self._on_err))

    def _clear(self):
        if self._busy:
            return
        self._set_busy(True)
        self.backend.call_async(
            lambda: self.backend.clear_offline_skin(self.account),
            guard(self, self._on_changed), guard(self, self._on_err))


class AccountPage(QWidget):
    def __init__(self, backend, parent=None):
        super().__init__(parent)
        self.setObjectName("accountPage")
        self.backend = backend
        self._login_dlg = None
        self._login_task = None
        self._pix_token = 0
        self._auth_busy = False
        self._active_account = None

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
        self.skin_btn = PushButton(tr("管理皮肤"))
        self.skin_btn.clicked.connect(self._manage_skin)
        self.skin_btn.hide()
        sl.addWidget(self.skin_btn)
        self.lookup_btn = PushButton(tr("查询玩家档案"))
        self.lookup_btn.setToolTip(tr("输入任意正版玩家名或 UUID，查看档案与皮肤"))
        self.lookup_btn.clicked.connect(self._lookup_player)
        sl.addWidget(self.lookup_btn)
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
        self._active_account = active
        kind = active.get("type") if active else ""
        if kind == "microsoft":
            self.skin_btn.setText(tr("管理皮肤"))
            self.skin_btn.show()
        elif kind == "authlib":
            self.skin_btn.setText(tr("打开皮肤站"))
            self.skin_btn.show()
        elif active and (kind or "offline") == "offline":
            self.skin_btn.setText(tr("离线皮肤"))
            self.skin_btn.show()
        else:
            self.skin_btn.hide()

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

    def _lookup_player(self):
        PlayerLookupDialog(self.backend, self.window()).exec()

    def _manage_skin(self):
        active = getattr(self, "_active_account", None)
        if not active:
            return
        if active.get("type") == "microsoft":
            dlg = SkinManagerDialog(self.backend, active["name"], self.window())
            dlg.exec()
            self.reload()
            return
        if active.get("type") == "authlib":
            url = self.backend.skin_site_url(active["name"])
            if url:
                QDesktopServices.openUrl(QUrl(url))
            else:
                InfoBar.warning(tr("无法打开"), tr("该账号没有对应的皮肤站地址。"),
                                parent=self, position=InfoBarPosition.TOP, duration=3000)
            return
        if (active.get("type") or "offline") == "offline":
            dlg = OfflineSkinDialog(self.backend, active["name"], self.window())
            dlg.exec()
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
