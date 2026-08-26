# -*- coding: utf-8 -*-
"""PCL 同款搜索页：名称/来源/版本/类型 + 结果列表。"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget,
)
from PySide6.QtGui import QGuiApplication
from qfluentwidgets import (
    ComboBox, EditableComboBox, FluentIcon as FIF, HyperlinkButton, InfoBar,
    InfoBarPosition, LineEdit, MessageBoxBase, PushButton, ScrollArea,
    TransparentPushButton, TransparentToolButton,
)

from ..pcl_chrome import Theme, chip_qss, ghost_btn_qss, row_qss, _icon
from ..widgets import EmptyState, IconTile, InputDialog, ThumbnailTile
from mclauncher.i18n import tr

_HEART = getattr(FIF, "HEART", FIF.TAG)

try:
    from mclauncher.config import CONFIG
except ImportError:
    class CONFIG:
        @staticmethod
        def get(key, default=None):
            return default


def fmt_downloads(n) -> str:
    try:
        n = int(n or 0)
    except (TypeError, ValueError):
        return "—"
    if n >= 100_000_000:
        s = f" {n / 100_000_000:.1f}亿"
        return s.replace(".0", "").strip()
    if n >= 10_000:
        return f"{n / 10_000:.0f}万"
    return str(n) if n else "—"


class PclCard(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("pclCard")
        self._style_ver = -1
        self._apply_style()

    def _apply_style(self):
        self.setStyleSheet(
            f"#pclCard {{ background: {Theme.card}; border: 1px solid {Theme.line};"
            " border-radius: 10px; }"
        )

    def paintEvent(self, event):
        if self._style_ver != Theme._version:
            self._style_ver = Theme._version
            self._apply_style()
        super().paintEvent(event)


def _src_label(src) -> str:
    s = str(src or "").lower()
    if s.startswith("curse"):
        return "CurseForge"
    if s.startswith("modrinth") or s == "modrinth":
        return "Modrinth"
    if not src:
        return "—"
    return str(src)


def _meta_chip(fif, text: str) -> QWidget:
    w = QWidget()
    w.setStyleSheet("background: transparent;")
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(3)
    icon = QLabel()
    icon.setPixmap(_icon(fif, Theme.muted, 12))
    lab = QLabel(text)
    lab.setStyleSheet(f"color: {Theme.muted}; font-size: 11px; background: transparent;")
    h.addWidget(icon)
    h.addWidget(lab)
    return w


class DetailDialog(MessageBoxBase):
    """资源详情：正文 / 截图 / 元数据 / 外链（对标 PCL2 / HMCL 资源详情页）。"""

    def __init__(self, backend, detail: dict, parent=None):
        super().__init__(parent)
        self.backend = backend
        self.detail = dict(detail or {})

        head = QHBoxLayout()
        head.setSpacing(10)
        icon_url = self.detail.get("icon_url") or ""
        name = self.detail.get("name") or "?"
        if icon_url:
            head.addWidget(ThumbnailTile(name, icon_url, size=48))
        else:
            head.addWidget(IconTile(name, size=48))
        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        # mcmod 数据集的中文译名（对标 PCL2 详情页）
        name_cn = str(self.detail.get("name_cn") or "").strip()
        t = QLabel(f"{name_cn} ({name})" if name_cn and name_cn != name else name)
        t.setStyleSheet(
            f"color: {Theme.title}; font-size: 17px; font-weight: 700; background: transparent;")
        title_col.addWidget(t)
        sub_bits = [_src_label(self.detail.get("source"))]
        if self.detail.get("author"):
            sub_bits.append(self.detail["author"])
        if self.detail.get("license"):
            sub_bits.append(self.detail["license"])
        sub = QLabel("  ·  ".join(b for b in sub_bits if b))
        sub.setStyleSheet(f"color: {Theme.muted}; font-size: 12px; background: transparent;")
        title_col.addWidget(sub)
        head.addLayout(title_col, 1)
        self.viewLayout.addLayout(head)

        meta = QHBoxLayout()
        meta.setSpacing(14)
        meta.addWidget(_meta_chip(FIF.DOWNLOAD, fmt_downloads(self.detail.get("downloads"))))
        if self.detail.get("updated"):
            meta.addWidget(_meta_chip(FIF.UP, str(self.detail["updated"])))
        loaders = ", ".join(self.detail.get("loaders") or [])
        if loaders:
            meta.addWidget(_meta_chip(FIF.APPLICATION, loaders))
        versions = self.detail.get("game_versions") or []
        if versions:
            shown = ", ".join(versions[:6]) + ("…" if len(versions) > 6 else "")
            meta.addWidget(_meta_chip(FIF.GAME, shown))
        mcmod = str(self.detail.get("mcmod_url") or "")
        if mcmod:
            wiki = HyperlinkButton(mcmod, tr("mcmod 百科"), self)
            wiki.setFixedHeight(26)
            meta.addWidget(wiki)
        meta.addStretch(1)
        self.viewLayout.addLayout(meta)

        cats = self.detail.get("categories") or []
        if cats:
            cat_row = QHBoxLayout()
            cat_row.setSpacing(6)
            for c in cats[:8]:
                chip = QLabel(str(c))
                chip.setStyleSheet(chip_qss())
                cat_row.addWidget(chip)
            cat_row.addStretch(1)
            self.viewLayout.addLayout(cat_row)

        from PySide6.QtWidgets import QTextBrowser
        body = QTextBrowser(self)
        body.setOpenExternalLinks(True)
        body.setStyleSheet(
            f"QTextBrowser {{ background: {Theme.card}; color: {Theme.text};"
            f" border: 1px solid {Theme.line}; border-radius: 8px; padding: 8px; }}")
        text = self.detail.get("body") or self.detail.get("summary") or ""
        if self.detail.get("body_format") == "html":
            body.setHtml(text)
        else:
            body.setMarkdown(text)
        body.setMinimumSize(620, 300)
        self.viewLayout.addWidget(body, 1)

        gallery = [g for g in self.detail.get("gallery") or [] if g.get("url")][:4]
        if gallery:
            shots = QHBoxLayout()
            shots.setSpacing(8)
            for g in gallery:
                lab = QLabel(self)
                lab.setFixedSize(148, 92)
                lab.setAlignment(Qt.AlignCenter)
                lab.setStyleSheet(
                    f"background: {Theme.hover}; border-radius: 6px; color: {Theme.muted};")
                lab.setText("…")
                lab.setToolTip(g.get("title") or "")
                shots.addWidget(lab)
                self._load_shot(lab, g["url"])
            shots.addStretch(1)
            self.viewLayout.addLayout(shots)

        project_url = (self.detail.get("links") or {}).get("project") or ""
        self.yesButton.setText(tr("在浏览器打开"))
        self.yesButton.setEnabled(bool(project_url))
        self.cancelButton.setText(tr("关闭"))
        self._project_url = project_url
        self.yesButton.clicked.connect(self._open_page)
        self.widget.setMinimumWidth(680)

    def _open_page(self):
        if self._project_url:
            from PySide6.QtGui import QDesktopServices
            from PySide6.QtCore import QUrl
            QDesktopServices.openUrl(QUrl(self._project_url))

    def _load_shot(self, label: QLabel, url: str):
        def fetch():
            return self.backend.ensure_thumb(url)

        def ok(path):
            import shiboken6
            if not path or not shiboken6.isValid(label):
                return
            from PySide6.QtGui import QPixmap
            pix = QPixmap(path)
            if not pix.isNull():
                label.setPixmap(pix.scaled(
                    label.width(), label.height(),
                    Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation))

        self.backend.call_async(fetch, ok, lambda *_: None)


class PclResultRow(QFrame):
    def __init__(self, item: dict, on_install, parent=None, on_fav=None,
                 on_detail=None):
        super().__init__(parent)
        self.item = item
        self.setObjectName("pclRow")
        self.setStyleSheet(row_qss("pclRow"))
        self.setFixedHeight(88)
        name = item.get("name") or "?"
        desc = (item.get("description") or item.get("summary") or "").strip()
        tags = item.get("tags") or []
        ver = item.get("game_version") or item.get("version") or "—"
        updated = item.get("updated") or item.get("date") or "—"

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(12)
        thumb_url = item.get("icon_url") or item.get("thumb") or item.get("icon") or item.get("image")
        if thumb_url:
            layout.addWidget(ThumbnailTile(name, thumb_url, size=52))
        else:
            layout.addWidget(IconTile(name, size=52))

        info = QVBoxLayout()
        info.setSpacing(3)
        title_row = QHBoxLayout()
        title_row.setSpacing(6)
        # mcmod 数据集的中文译名（对标 PCL2 / HMCL 下载列表），有则主标题显示中文
        name_cn = str(item.get("name_cn") or "").strip()
        shown_name = f"{name_cn} ({name})" if name_cn and name_cn != name else name
        title = QLabel(shown_name)
        title.setStyleSheet(
            f"color: {Theme.title}; font-size: 14px; font-weight: 700; background: transparent;")
        title_row.addWidget(title)
        for tag in tags[:4]:
            chip = QLabel(str(tag))
            chip.setStyleSheet(
                chip_qss())
            title_row.addWidget(chip)
        title_row.addStretch(1)
        info.addLayout(title_row)
        if desc:
            d = QLabel(desc[:90])
            d.setStyleSheet(f"color: {Theme.muted}; font-size: 12px; background: transparent;")
            info.addWidget(d)
        meta = QHBoxLayout()
        meta.setSpacing(14)
        meta.addWidget(_meta_chip(FIF.GAME, str(ver)))
        meta.addWidget(_meta_chip(FIF.DOWNLOAD, fmt_downloads(item.get("downloads"))))
        meta.addWidget(_meta_chip(FIF.UP, str(updated)))
        meta.addWidget(_meta_chip(FIF.GLOBE, _src_label(item.get("source"))))
        meta.addStretch(1)
        info.addLayout(meta)
        layout.addLayout(info, 1)

        if on_detail and (item.get("slug") or item.get("id")):
            detail_btn = PushButton(tr("详情"))
            detail_btn.setFixedSize(64, 30)
            detail_btn.setStyleSheet(ghost_btn_qss())
            detail_btn.clicked.connect(lambda: on_detail(item, detail_btn))
            layout.addWidget(detail_btn)
        btn = PushButton(tr("选择版本"))
        btn.setFixedSize(88, 30)
        btn.setStyleSheet(ghost_btn_qss())
        btn.clicked.connect(lambda: on_install(item, btn))
        layout.addWidget(btn)
        if on_fav:
            star = TransparentToolButton(_HEART)
            star.setToolTip(tr("收藏"))
            star.clicked.connect(lambda: on_fav(item))
            layout.addWidget(star)


MOD_SPEC = {
    "object_name": "modPage",
    "search_title": tr("搜索 Mod"),
    "empty_search": tr("没有找到相关模组"),
    "empty_installed": tr("还没有安装模组"),
    "installed_title": tr("已安装"),
    "local_label": tr("导入 jar"),
    "local_filter": tr("模组 (*.jar)"),
    "local_dialog": tr("选择模组"),
    "link_label": tr("从链接安装"),
    "link_title": tr("从链接安装模组"),
    "link_hint": tr("模组下载链接 (URL)"),
    "link_ph": "https://…/mod.jar",
    "icon": FIF.TAG,
    "search": "search_mods",
    "install": "install_mod",
    "list_installed": "get_installed_mods",
    "delete": "delete_mod",
    "task_prefix": tr("安装模组"),
    "types": [tr("全部"), tr("优化"), tr("科技"), tr("魔法"), tr("冒险")],
}

MODPACK_SPEC = {
    "object_name": "modpackPage",
    "search_title": tr("搜索整合包"),
    "empty_search": tr("没有找到相关整合包"),
    "empty_installed": tr("还没有安装整合包"),
    "installed_title": tr("已安装"),
    "local_label": tr("导入文件"),
    "local_filter": tr("整合包 (*.mrpack *.zip)"),
    "local_dialog": tr("选择整合包"),
    "link_label": tr("从链接安装"),
    "link_title": tr("从链接安装整合包"),
    "link_hint": tr("整合包链接或文件"),
    "link_ph": "https://…/pack.mrpack",
    "icon": FIF.ZIP_FOLDER,
    "search": "search_modpacks",
    "install": "install_modpack",
    "list_installed": "get_installed_modpacks",
    "delete": "delete_modpack",
    "task_prefix": tr("安装整合包"),
    "types": [tr("全部"), tr("生存"), tr("空岛"), tr("科技"), tr("魔法")],
}

RESOURCE_SPEC = {
    "object_name": "resourcePackPage",
    "search_title": tr("搜索资源包"),
    "empty_search": tr("没有找到相关资源包"),
    "empty_installed": tr("还没有安装资源包"),
    "installed_title": tr("已安装"),
    "local_label": tr("导入 zip"),
    "local_filter": tr("资源包 (*.zip)"),
    "local_dialog": tr("选择资源包"),
    "link_label": tr("从链接安装"),
    "link_title": tr("从链接安装资源包"),
    "link_hint": tr("资源包下载链接 (URL)"),
    "link_ph": "https://…/pack.zip",
    "icon": FIF.PHOTO,
    "search": "search_resourcepacks",
    "install": "install_resourcepack",
    "list_installed": "get_installed_resourcepacks",
    "delete": "delete_resourcepack",
    "task_prefix": tr("安装资源包"),
    "types": [tr("全部"), "16x", "32x", "64x", tr("写实"), tr("现代风"), tr("动态效果")],
}

SHADER_SPEC = {
    "object_name": "shaderPage",
    "search_title": tr("搜索光影包"),
    "empty_search": tr("没有找到相关光影"),
    "empty_installed": tr("还没有安装光影"),
    "installed_title": tr("已安装"),
    "local_label": tr("导入 zip"),
    "local_filter": tr("光影包 (*.zip)"),
    "local_dialog": tr("选择光影包"),
    "link_label": tr("从链接安装"),
    "link_title": tr("从链接安装光影"),
    "link_hint": tr("光影包下载链接 (URL)"),
    "link_ph": "https://…/shader.zip",
    "icon": FIF.BRIGHTNESS,
    "search": "search_shaders",
    "install": "install_shader",
    "list_installed": "get_installed_shaders",
    "delete": "delete_shader",
    "task_prefix": tr("安装光影"),
    "types": [tr("全部"), tr("写实"), tr("卡通"), tr("高性能"), tr("光追")],
}

DATAPACK_SPEC = {
    "object_name": "datapackPage",
    "search_title": tr("搜索数据包"),
    "empty_search": tr("没有找到相关数据包"),
    "empty_installed": tr("还没有安装数据包"),
    "installed_title": tr("已安装"),
    "local_label": tr("导入 zip"),
    "local_filter": tr("数据包 (*.zip)"),
    "local_dialog": tr("选择数据包"),
    "link_label": tr("从链接安装"),
    "link_title": tr("从链接安装数据包"),
    "link_hint": tr("数据包下载链接 (URL)"),
    "link_ph": "https://…/datapack.zip",
    "icon": FIF.LEAF,
    "search": "search_datapacks",
    "install": "install_datapack",
    "list_installed": "get_installed_datapacks",
    "delete": "delete_datapack",
    "task_prefix": tr("安装数据包"),
    "types": [tr("全部"), tr("生存"), tr("冒险"), tr("装饰")],
}

WORLD_SPEC = {
    "object_name": "worldPage",
    "search_title": tr("搜索世界"),
    "empty_search": tr("没有找到相关世界"),
    "empty_installed": tr("还没有安装世界"),
    "installed_title": tr("已安装"),
    "local_label": tr("导入 zip"),
    "local_filter": tr("世界 (*.zip)"),
    "local_dialog": tr("选择世界"),
    "link_label": tr("从链接安装"),
    "link_title": tr("从链接安装世界"),
    "link_hint": tr("世界下载链接 (URL)"),
    "link_ph": "https://…/world.zip",
    "icon": FIF.GLOBE,
    "search": "search_worlds",
    "install": "install_world",
    "list_installed": "list_saves",
    "delete": "delete_save",
    "task_prefix": tr("安装世界"),
    "types": [tr("全部"), tr("生存"), tr("冒险"), tr("创造")],
}


def _version_filter_items(backend) -> list[str]:
    """版本筛选项：只读本地缓存清单，避免打开目录页时同步联网卡住 UI。

    缓存为空（还没拉过版本列表）时退回经典版本；输入框可自行填写 26.x。
    """
    try:
        from mclauncher.manifest import CLASSIC_CATALOG_VERSIONS, catalog_release_ids
    except ImportError:
        return ["1.20.1", "1.19.2", "1.18.2", "1.16.5", "1.12.2"]
    rows = []
    try:
        rows = backend.get_version_list() or []
    except Exception:
        rows = []
    return catalog_release_ids(rows) or list(CLASSIC_CATALOG_VERSIONS)


class PclCatalogPage(QWidget):
    def __init__(self, backend, spec: dict, parent=None):
        super().__init__(parent)
        self.setObjectName(spec["object_name"])
        self.backend = backend
        self.spec = spec
        self.setStyleSheet("background: transparent;")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        search_card = PclCard()
        sc = QVBoxLayout(search_card)
        sc.setContentsMargins(16, 12, 16, 14)
        sc.setSpacing(10)

        head = QHBoxLayout()
        title = QLabel(spec["search_title"])
        title.setStyleSheet("font-size: 15px; font-weight: 700; background: transparent;")
        head.addWidget(title)
        head.addStretch(1)
        self.instance_box = ComboBox()
        self.instance_box.setFixedWidth(120)
        self.link_btn = TransparentPushButton(FIF.LINK, spec["link_label"])
        self.local_btn = TransparentPushButton(FIF.FOLDER, spec["local_label"])
        head.addWidget(self.instance_box)
        head.addWidget(self.link_btn)
        head.addWidget(self.local_btn)
        sc.addLayout(head)

        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(10)
        self.name_edit = LineEdit()
        self.name_edit.setPlaceholderText(tr("名称"))
        self.source_box = ComboBox()
        self.source_box.addItems([tr("全部"), "Modrinth", "CurseForge"])
        self.version_box = EditableComboBox()
        self.version_box.addItems([tr("全部 (也可自行输入)")] + _version_filter_items(backend))
        self.version_box.setCurrentIndex(0)
        self.type_box = ComboBox()
        self.type_box.addItems(spec.get("types") or [tr("全部")])
        grid.addWidget(self._lab(tr("名称")), 0, 0)
        grid.addWidget(self.name_edit, 0, 1)
        grid.addWidget(self._lab(tr("来源")), 0, 2)
        grid.addWidget(self.source_box, 0, 3)
        grid.addWidget(self._lab(tr("版本")), 1, 0)
        grid.addWidget(self.version_box, 1, 1)
        grid.addWidget(self._lab(tr("类型")), 1, 2)
        grid.addWidget(self.type_box, 1, 3)
        grid.setColumnStretch(1, 3)
        grid.setColumnStretch(3, 2)
        sc.addLayout(grid)

        btns = QHBoxLayout()
        btns.addStretch(1)
        self.search_btn = PushButton(tr("搜索"))
        self.search_btn.setFixedSize(88, 32)
        self.search_btn.setStyleSheet(ghost_btn_qss())
        self.reset_btn = PushButton(tr("重置条件"))
        self.reset_btn.setFixedSize(88, 32)
        btns.addWidget(self.search_btn)
        btns.addSpacing(12)
        btns.addWidget(self.reset_btn)
        btns.addStretch(1)
        sc.addLayout(btns)
        root.addWidget(search_card)

        result_card = PclCard()
        rc = QVBoxLayout(result_card)
        rc.setContentsMargins(8, 6, 8, 8)
        mode_row = QHBoxLayout()
        self.mode_search = PushButton(tr("浏览"))
        self.mode_installed = PushButton(tr("已安装"))
        for b in (self.mode_search, self.mode_installed):
            b.setFixedHeight(28)
            b.setCheckable(True)
        self.mode_search.setChecked(True)
        self.update_btn = TransparentPushButton(FIF.SYNC, tr("检查更新"))
        self.installed_ver_box = ComboBox()
        self.installed_ver_box.setFixedWidth(160)
        self.installed_ver_box.addItem(tr("实例目录"))
        self.installed_ver_box.setVisible(False)
        self.fav_btn = TransparentPushButton(_HEART, tr("收藏"))
        mode_row.addWidget(self.mode_search)
        mode_row.addWidget(self.mode_installed)
        mode_row.addWidget(self.installed_ver_box)
        mode_row.addStretch(1)
        mode_row.addWidget(self.fav_btn)
        mode_row.addWidget(self.update_btn)
        rc.addLayout(mode_row)
        scroll = ScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("ScrollArea { background: transparent; border: none; }")
        host = QWidget()
        self.list_layout = QVBoxLayout(host)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(0)
        scroll.setWidget(host)
        rc.addWidget(scroll)
        root.addWidget(result_card, 1)

        self.name_edit.returnPressed.connect(self._search)
        self.search_btn.clicked.connect(self._search)
        self.reset_btn.clicked.connect(self._reset)
        self.link_btn.clicked.connect(self._install_from_link)
        self.local_btn.clicked.connect(self._import_local)
        self.instance_box.currentTextChanged.connect(self.reload_installed)
        self.installed_ver_box.currentTextChanged.connect(self.reload_installed)
        self.mode_search.clicked.connect(lambda: self._set_mode("search"))
        self.mode_installed.clicked.connect(lambda: self._set_mode("installed"))
        self.update_btn.clicked.connect(self._check_updates)
        self.fav_btn.clicked.connect(self._show_favs)
        self.update_btn.setVisible(self.spec.get("list_installed") == "get_installed_mods")

        self._search_token = 0
        self._popular_loaded = False
        self._mode = "search"
        # 翻页（对标 PCL2 下载页翻页 / HMCL 加载更多）
        self._next_offset = 0
        self._seen_keys = set()
        self._more_btn = None
        self.setAcceptDrops(True)
        self._reload_instances()
        self._show_idle()

    def _lab(self, text: str) -> QLabel:
        lab = QLabel(text)
        lab.setStyleSheet(f"color: {Theme.muted}; font-size: 12px; background: transparent;")
        lab.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        lab.setFixedWidth(40)
        return lab

    def _current_instance(self) -> str:
        return self.instance_box.currentText() or CONFIG.get("default_instance", "default") or "default"

    def _reload_instances(self):
        cur = self.instance_box.currentText()
        getter = getattr(self.backend, "get_instances", None)
        names = [i["name"] for i in getter()] if callable(getter) else ["default"]
        self.instance_box.blockSignals(True)
        self.instance_box.clear()
        self.instance_box.addItems(names)
        if cur in names:
            self.instance_box.setCurrentText(cur)
        elif CONFIG.get("default_instance") in names:
            self.instance_box.setCurrentText(CONFIG.get("default_instance"))
        self.instance_box.blockSignals(False)

    def _source(self) -> str:
        text = self.source_box.currentText()
        if text == "CurseForge":
            return "CurseForge"
        if text == "Modrinth":
            return "Modrinth"
        return tr("全部")

    def _reset(self):
        self.name_edit.clear()
        self.source_box.setCurrentIndex(0)
        self.version_box.setCurrentIndex(0)
        self.type_box.setCurrentIndex(0)
        self._search()

    def _clear_list(self):
        self._more_btn = None
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _drop_tail(self):
        """追加下一页前，摘掉尾部的 stretch 与「加载更多」按钮。"""
        for i in range(self.list_layout.count() - 1, -1, -1):
            item = self.list_layout.itemAt(i)
            w = item.widget()
            if w is None or w is self._more_btn:
                self.list_layout.takeAt(i)
                if w is not None:
                    w.deleteLater()
        self._more_btn = None

    def _show_idle(self):
        self._search_token += 1
        self._clear_list()
        self.list_layout.addWidget(EmptyState(self.spec["icon"], tr("输入名称后点击搜索")))
        self.list_layout.addStretch(1)

    PAGE_STEP = 30  # 每页条目数（Modrinth offset / CurseForge index 的步长）

    def _search(self, append: bool = False):
        self._search_token += 1
        token = self._search_token
        if not append:
            self._next_offset = 0
            self._seen_keys = set()
            self._clear_list()
        elif self._more_btn is not None:
            self._more_btn.setEnabled(False)
            self._more_btn.setText(tr("正在加载…"))
        fn = getattr(self.backend, self.spec["search"], None)
        if not callable(fn):
            self.list_layout.addWidget(EmptyState(self.spec["icon"], self.spec["empty_search"]))
            self.list_layout.addStretch(1)
            return
        query = self.name_edit.text().strip()
        source = self._source()
        type_f = self.type_box.currentText()
        gv = self.version_box.currentText()
        extra = {
            "game_version": "" if (not gv or str(gv).startswith(tr("全部"))) else gv,
            "category": type_f,
            "offset": self._next_offset,
        }
        call_async = getattr(self.backend, "call_async", None)

        def _call():
            try:
                return fn(query, source, extra)
            except TypeError:
                return fn(query, source)

        if callable(call_async):
            if not append:
                self.list_layout.addWidget(EmptyState(self.spec["icon"], tr("正在搜索…")))
                self.list_layout.addStretch(1)
            call_async(
                _call,
                lambda rows, t=token, tf=type_f, a=append: self._on_search_ok(t, rows, tf, a),
                lambda err, t=token, a=append: self._on_search_err(t, err, a),
            )
            return
        self._on_search_ok(token, _call(), type_f, append)

    def _on_search_err(self, token, err, append: bool = False):
        if token != self._search_token:
            return
        if append:
            # 保留已加载的结果，恢复按钮让用户可以重试
            if self._more_btn is not None:
                self._more_btn.setEnabled(True)
                self._more_btn.setText(tr("加载更多"))
            InfoBar.error(tr("加载更多失败"), str(err), parent=self,
                          position=InfoBarPosition.TOP, duration=4000)
            return
        self._clear_list()
        self.list_layout.addWidget(EmptyState(self.spec["icon"], f"搜索失败: {err}"))
        self.list_layout.addStretch(1)

    def _make_more_button(self):
        btn = PushButton(tr("加载更多"))
        btn.setFixedHeight(34)
        btn.setStyleSheet(ghost_btn_qss())
        btn.clicked.connect(lambda: self._search(append=True))
        return btn

    def _on_search_ok(self, token, results, type_f, append: bool = False):
        if token != self._search_token:
            return
        results = list(results or [])
        del type_f
        query = self.name_edit.text().strip()
        # 翻页时 CurseForge 人气排序会漂移，按 (来源, id/slug) 去重
        fresh = []
        for row in results:
            key = (str(row.get("source") or ""),
                   str(row.get("id") or row.get("slug") or row.get("name") or ""))
            if key in self._seen_keys:
                continue
            self._seen_keys.add(key)
            fresh.append(row)
        if append:
            self._drop_tail()
            if not fresh:
                InfoBar.info(tr("没有更多结果"), "", parent=self,
                             position=InfoBarPosition.TOP, duration=2500)
                self.list_layout.addStretch(1)
                return
        else:
            self._clear_list()
            if not query:
                head = QLabel(tr("热门推荐"))
                head.setStyleSheet(
                    f"color: {Theme.title}; font-size: 13px; font-weight: 700;"
                    " background: transparent; padding: 10px 12px 6px 12px;")
                self.list_layout.addWidget(head)
            if not results:
                self.list_layout.addWidget(EmptyState(self.spec["icon"], self.spec["empty_search"]))
                self.list_layout.addStretch(1)
                return
        for row in fresh:
            self.list_layout.addWidget(PclResultRow(
                row, self._install, on_fav=self._toggle_fav, on_detail=self._show_detail))
        self._next_offset += self.PAGE_STEP
        # 满页（含近满页的双源合并）才显示「加载更多」；空查询是静态热门推荐，没有下一页
        if query and len(results) >= 20:
            self._more_btn = self._make_more_button()
            self.list_layout.addWidget(self._more_btn)
        self.list_layout.addStretch(1)

    def _show_detail(self, item: dict, btn=None):
        source = str(item.get("source") or "")
        ident = item.get("slug") if source.lower().startswith("modrinth") or item.get("slug") else item.get("id")
        if source.lower().startswith("curse"):
            ident = item.get("id") or item.get("slug")
        if not ident:
            return
        if btn is not None:
            btn.setEnabled(False)
            btn.setText(tr("加载中…"))

        def restore():
            import shiboken6
            if btn is not None and shiboken6.isValid(btn):
                btn.setEnabled(True)
                btn.setText(tr("详情"))

        def ok(detail):
            restore()
            DetailDialog(self.backend, detail, self.window()).exec()

        def err(message):
            restore()
            InfoBar.error(tr("获取详情失败"), str(message), parent=self,
                          position=InfoBarPosition.TOP, duration=4000)

        self.backend.call_async(
            lambda: self.backend.get_project_detail(source, str(ident)), ok, err)

    def _set_mode(self, mode: str):
        self._mode = mode
        self.mode_search.setChecked(mode == "search")
        self.mode_installed.setChecked(mode == "installed")
        show_ver = mode == "installed" and self.spec.get("list_installed") == "get_installed_mods"
        self.installed_ver_box.setVisible(show_ver)
        if mode == "installed":
            self.reload_installed()
        else:
            self._search()

    def _installed_version(self) -> str:
        if self.spec.get("list_installed") != "get_installed_mods":
            return ""
        text = self.installed_ver_box.currentText()
        if not text or text == tr("实例目录"):
            return ""
        return text

    def _fill_installed_versions(self):
        if self.spec.get("list_installed") != "get_installed_mods":
            return
        inst = self._current_instance()
        getter = getattr(self.backend, "get_installed_versions", None)
        ids = getter(inst) if callable(getter) else []
        cur = self.installed_ver_box.currentText()
        self.installed_ver_box.blockSignals(True)
        self.installed_ver_box.clear()
        self.installed_ver_box.addItem(tr("实例目录"))
        self.installed_ver_box.addItems(ids)
        if cur and cur in [tr("实例目录"), *ids]:
            self.installed_ver_box.setCurrentText(cur)
        self.installed_ver_box.blockSignals(False)

    def reload_installed(self):
        self._reload_instances()
        self._fill_installed_versions()
        if self._mode != "installed":
            if not self._popular_loaded:
                self._popular_loaded = True
                self._search()
            return
        self._clear_list()
        inst = self._current_instance()
        version = self._installed_version()
        getter = getattr(self.backend, "get_installed_mod_entries", None)
        rows = []
        list_fn = getattr(self.backend, self.spec.get("list_installed") or "", None)
        if self.spec.get("list_installed") == "get_installed_mods" and callable(getter):
            try:
                rows = getter(inst, version) or []
            except TypeError:
                rows = getter(inst) or []
        elif callable(list_fn):
            names = list_fn(inst) or []
            if names and isinstance(names[0], dict):
                rows = names
            else:
                rows = [{"filename": n} for n in names]
        else:
            rows = []
        if not rows:
            self.list_layout.addWidget(EmptyState(self.spec["icon"], self.spec["empty_installed"]))
            self.list_layout.addStretch(1)
            return
        for row in rows:
            self.list_layout.addWidget(self._installed_row(row))
        self.list_layout.addStretch(1)

    def _installed_row(self, row):
        from PySide6.QtWidgets import QHBoxLayout
        from qfluentwidgets import SwitchButton, TransparentToolButton
        host = QFrame()
        host.setObjectName("pclRow")
        host.setStyleSheet(row_qss("pclRow"))
        host.setFixedHeight(52)
        lay = QHBoxLayout(host)
        lay.setContentsMargins(12, 6, 12, 6)
        name = row.get("filename") or row.get("name") or "?"
        lab = QLabel(name)
        lab.setStyleSheet("font-size: 13px; background: transparent;")
        lay.addWidget(lab, 1)
        if "enabled" in row:
            sw = SwitchButton()
            sw.setChecked(bool(row.get("enabled")))
            sw.checkedChanged.connect(lambda on, n=name: self._toggle(n, on))
            lay.addWidget(sw)
        btn = TransparentToolButton(FIF.DELETE)
        btn.clicked.connect(lambda _, n=name: self._delete_installed(n))
        lay.addWidget(btn)
        return host

    def _toggle(self, filename, enabled):
        inst = self._current_instance()
        ver = self._installed_version()
        try:
            if enabled:
                self.backend.enable_mod(inst, filename, ver)
            else:
                self.backend.disable_mod(inst, filename, ver)
        except Exception as e:
            InfoBar.error(tr("切换失败"), str(e), parent=self,
                          position=InfoBarPosition.TOP, duration=4000)
            self.reload_installed()

    def _delete_installed(self, filename):
        inst = self._current_instance()
        kind = self.spec.get("list_installed") or ""
        fn = {
            "get_installed_mods": "delete_mod",
            "get_installed_shaders": "delete_shader",
            "get_installed_resourcepacks": "delete_resourcepack",
            "get_installed_datapacks": "delete_datapack",
            "get_installed_modpacks": "delete_modpack",
            "list_saves": "delete_save",
        }.get(kind)
        # 以前只有整合包会二次确认，mod / 光影 / 资源包 / 数据包 / **世界存档** 全是点一下就没。
        # 世界存档那条尤其要命：删掉的是玩家自己的游戏进度，重下不回来。
        from qfluentwidgets import MessageBox
        if fn == "delete_modpack":
            box = MessageBox(
                tr("删除整合包实例"),
                f"将删除整个实例「{inst}」及其文件，不可恢复。",
                self,
            )
            box.yesButton.setText(tr("删除实例"))
        elif fn == "delete_save":
            box = MessageBox(
                tr("删除世界存档"),
                f"将永久删除世界「{filename}」，其中的建筑与游戏进度都无法恢复。\n"
                + tr("建议先在「存档管理」里备份。"),
                self,
            )
            box.yesButton.setText(tr("永久删除"))
        elif fn:
            box = MessageBox(tr("删除确认"), f"将删除「{filename}」。", self)
            box.yesButton.setText(tr("删除"))
        else:
            box = None
        if box is not None:
            box.cancelButton.setText(tr("取消"))
            if not box.exec():
                return
        try:
            if fn == "delete_mod":
                self.backend.delete_mod(inst, filename, self._installed_version())
            elif fn:
                getattr(self.backend, fn)(inst, filename)
        except Exception as e:
            InfoBar.error(tr("删除失败"), str(e), parent=self,
                          position=InfoBarPosition.TOP, duration=4000)
            return
        self.reload_installed()

    def _check_updates(self):
        inst = self._current_instance()
        self.backend.start_mod_updates(inst)

    def _install(self, item, tile=None):
        if isinstance(item, str):
            item = {"name": item}
        if item.get("path") or item.get("url"):
            self._do_install(item, tile)
            return
        if not (item.get("slug") or item.get("id")):
            self._do_install(item, tile)
            return
        from .file_pick import FilePickDialog
        kind = {
            "install_mod": "mod",
            "install_modpack": "modpack",
            "install_shader": "shader",
            "install_resourcepack": "resourcepack",
            "install_datapack": "datapack",
            "install_world": "world",
        }.get(self.spec.get("install") or "", "mod")
        gv = self.version_box.currentText()
        item = dict(item)
        item.setdefault("instance", self._current_instance())
        dlg = FilePickDialog(self.backend, item, kind, gv, self)
        if dlg.exec():
            extra = dlg.selected_extra()
            if kind == "datapack":
                extra = self._maybe_datapack_save(extra)
            self._do_install(extra, tile)

    def _maybe_datapack_save(self, extra):
        try:
            saves = self.backend.list_saves(self._current_instance()) or []
        except Exception:
            return extra
        names = [s.get("name") for s in saves if s.get("name")]
        if not names:
            return extra
        from ..widgets import ComboDialog
        dlg = ComboDialog(tr("装进存档"), tr("可选：把数据包装进某个存档，或只放到 datapacks 文件夹。"),
                          [tr("不装进存档")] + names, tr("不装进存档"), self)
        if dlg.exec() and dlg.value() and dlg.value() != tr("不装进存档"):
            extra = dict(extra)
            extra["save"] = dlg.value()
        return extra

    def _toggle_fav(self, item):
        try:
            self.backend.toggle_favorite(item)
            InfoBar.success(tr("已更新收藏"), item.get("name") or "", parent=self,
                            position=InfoBarPosition.TOP, duration=1800)
        except Exception as e:
            InfoBar.error(tr("收藏失败"), str(e), parent=self,
                          position=InfoBarPosition.TOP, duration=3000)

    def _show_favs(self):
        self._clear_list()
        rows = self.backend.catalog_favorites() or []
        if not rows:
            self.list_layout.addWidget(EmptyState(_HEART, tr("还没有收藏")))
            self.list_layout.addStretch(1)
            return
        for row in rows:
            self.list_layout.addWidget(PclResultRow(
                row, self._install, on_fav=self._toggle_fav, on_detail=self._show_detail))
        self.list_layout.addStretch(1)

    def showEvent(self, event):
        super().showEvent(event)
        clip = QGuiApplication.clipboard().text().strip()
        win = self.window()
        seen = getattr(win, "_clip_seen", None) if win is not None else getattr(self, "_clip_seen", None)
        if not clip or clip == seen:
            return
        low = clip.lower()
        if not any(x in low for x in ("modrinth.com", "curseforge.com")):
            return
        if win is not None:
            win._clip_seen = clip
        self._clip_seen = clip
        if not self.name_edit.text().strip():
            self.name_edit.setText(clip)
        InfoBar.info(tr("识别到剪贴板链接"), clip[:96], parent=self,
                     position=InfoBarPosition.TOP, duration=3500)

    def _do_install(self, item, tile=None):
        name = item.get("name") or ""
        win = self.window()
        if tile is not None and hasattr(win, "fly_to_tasks"):
            win.fly_to_tasks(tile, name)
        fn = getattr(self.backend, self.spec["install"], None)
        extra = dict(item)
        # FilePickDialog 里选过的安装目标（实例/版本）不能被页面当前值覆盖
        extra.setdefault("instance", self._current_instance())
        extra["source"] = item.get("source") or self._source()
        gv = self.version_box.currentText()
        extra.setdefault("game_version", "" if (not gv or str(gv).startswith(tr("全部"))) else gv)
        if callable(fn):
            try:
                if self.spec.get("install") == "install_modpack":
                    fn(name, extra.get("source") or "Modrinth", extra=extra)
                else:
                    fn(name, extra["instance"], extra=extra)
            except TypeError:
                try:
                    fn(name, extra["instance"])
                except TypeError:
                    fn(name)
            return
        start = getattr(self.backend, "start_task", None)
        if callable(start):
            def _pending(progress, log, *_a, **_k):
                log(tr("待后端对接"))
                progress(1, 1, tr("待对接"))
            start(f"{self.spec['task_prefix']} {name}".strip(), _pending)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path:
                self._do_install({"name": path, "path": path}, self.local_btn)

    def _install_from_link(self):
        dlg = InputDialog(self.spec["link_title"], self.spec["link_hint"],
                          placeholder=self.spec["link_ph"], parent=self)
        if dlg.exec() and dlg.value():
            url = dlg.value()
            self._install({"name": url, "url": url}, self.link_btn)

    def _import_local(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, self.spec["local_dialog"], "", self.spec["local_filter"])
        for p in paths:
            self._install({"name": p, "path": p}, self.local_btn)


class ModPage(PclCatalogPage):
    def __init__(self, backend, parent=None):
        super().__init__(backend, MOD_SPEC, parent)


class ModpackPage(PclCatalogPage):
    def __init__(self, backend, parent=None):
        super().__init__(backend, MODPACK_SPEC, parent)


class ShaderPage(PclCatalogPage):
    def __init__(self, backend, parent=None):
        super().__init__(backend, SHADER_SPEC, parent)


class ResourcePackPage(PclCatalogPage):
    def __init__(self, backend, parent=None):
        super().__init__(backend, RESOURCE_SPEC, parent)


class DatapackPage(PclCatalogPage):
    def __init__(self, backend, parent=None):
        super().__init__(backend, DATAPACK_SPEC, parent)


class WorldPage(PclCatalogPage):
    def __init__(self, backend, parent=None):
        super().__init__(backend, WORLD_SPEC, parent)
        self.source_box.blockSignals(True)
        self.source_box.clear()
        self.source_box.addItems(["CurseForge"])
        self.source_box.blockSignals(False)
