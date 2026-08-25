# -*- coding: utf-8 -*-
"""PCL 同款搜索页：名称/来源/版本/类型 + 结果列表。"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget,
)
from PySide6.QtGui import QGuiApplication
from qfluentwidgets import (
    ComboBox, EditableComboBox, FluentIcon as FIF, InfoBar, InfoBarPosition, LineEdit, PushButton,
    ScrollArea, TransparentPushButton, TransparentToolButton,
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


class PclResultRow(QFrame):
    def __init__(self, item: dict, on_install, parent=None, on_fav=None):
        super().__init__(parent)
        self.item = item
        self.setObjectName("pclRow")
        self.setStyleSheet(row_qss("pclRow"))
        self.setFixedHeight(88)
        name = item.get("name") or "?"
        # mcmod.cn 中文名（HMCL/PCL2 同款展示：中文在前，原名在后）
        cn = str(item.get("cn_name") or "").strip()
        if cn and cn not in name:
            name = f"{cn} · {name}"
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
        title = QLabel(name)
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

        btn = PushButton(tr("选择版本"))
        btn.setFixedSize(88, 30)
        btn.setStyleSheet(ghost_btn_qss())
        btn.clicked.connect(lambda: on_install(item, btn))
        layout.addWidget(btn)
        if item.get("mcmod_url"):
            wiki = TransparentToolButton(FIF.GLOBE)
            wiki.setToolTip(tr("打开 MC 百科页面"))
            wiki.clicked.connect(lambda: self._open_url(item["mcmod_url"]))
            layout.addWidget(wiki)
        if on_fav:
            star = TransparentToolButton(_HEART)
            star.setToolTip(tr("收藏"))
            star.clicked.connect(lambda: on_fav(item))
            layout.addWidget(star)

    @staticmethod
    def _open_url(url: str):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl(url))


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
        self.version_box.addItems([tr("全部 (也可自行输入)"), "1.21.1", "1.20.1", "1.19.2", "1.18.2", "1.16.5", "1.12.2"])
        self.version_box.setCurrentIndex(0)
        self.type_box = ComboBox()
        self.type_box.addItems(spec.get("types") or [tr("全部")])
        # 排序方式（PCL2/HMCL 下载页同款）；键与 mclauncher.mods.SORT_KEYS 对应
        self._sort_keys = ["", "downloads", "updated", "newest"]
        self.sort_box = ComboBox()
        self.sort_box.addItems([tr("默认排序"), tr("下载量"), tr("最近更新"), tr("最新发布")])
        grid.addWidget(self._lab(tr("名称")), 0, 0)
        grid.addWidget(self.name_edit, 0, 1)
        grid.addWidget(self._lab(tr("来源")), 0, 2)
        grid.addWidget(self.source_box, 0, 3)
        grid.addWidget(self._lab(tr("版本")), 1, 0)
        grid.addWidget(self.version_box, 1, 1)
        grid.addWidget(self._lab(tr("类型")), 1, 2)
        grid.addWidget(self.type_box, 1, 3)
        grid.addWidget(self._lab(tr("排序")), 2, 0)
        grid.addWidget(self.sort_box, 2, 1)
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
        # 分页状态：偏移量 + 累计结果（「加载更多」在旧结果后追加）
        self._offset = 0
        self._results: list[dict] = []
        self._page_size = 25 if spec.get("search") == "search_modpacks" else 30
        self.sort_box.currentIndexChanged.connect(lambda _i: self._search())
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
        self.sort_box.blockSignals(True)
        self.sort_box.setCurrentIndex(0)
        self.sort_box.blockSignals(False)
        self._search()

    def _sort_key(self) -> str:
        idx = self.sort_box.currentIndex()
        return self._sort_keys[idx] if 0 <= idx < len(self._sort_keys) else ""

    def _clear_list(self):
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _show_idle(self):
        self._search_token += 1
        self._clear_list()
        self.list_layout.addWidget(EmptyState(self.spec["icon"], tr("输入名称后点击搜索")))
        self.list_layout.addStretch(1)

    def _search(self, *_sig, append=False):
        self._search_token += 1
        token = self._search_token
        if not append:
            self._offset = 0
            self._results = []
            self._clear_list()
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
            "sort": self._sort_key(),
            "offset": self._offset,
        }
        # 无关键词但选了版本/分类/排序或在翻页 → 浏览源站真实榜单
        # （PCL2/HMCL 下载页同款）；全默认才显示本地热门推荐
        browsing = bool(extra["game_version"] or extra["sort"]
                        or self.type_box.currentIndex() > 0 or self._offset)
        extra["browse"] = browsing and not query
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
                lambda rows, t=token, ap=append, br=browsing: self._on_search_ok(t, rows, ap, br),
                lambda err, t=token: self._on_search_err(t, err),
            )
            return
        self._on_search_ok(token, _call(), append, browsing)

    def _load_more(self):
        self._offset += self._page_size
        self._search(append=True)

    def _on_search_err(self, token, err):
        if token != self._search_token:
            return
        self._clear_list()
        self.list_layout.addWidget(EmptyState(self.spec["icon"], f"搜索失败: {err}"))
        self.list_layout.addStretch(1)

    def _on_search_ok(self, token, results, append=False, browsing=False):
        if token != self._search_token:
            return
        results = list(results or [])
        if append:
            # 翻页去重：两源合并或镜像抖动可能重发同一条
            seen = {(r.get("source"), r.get("id") or r.get("slug") or r.get("name"))
                    for r in self._results}
            results = [r for r in results
                       if (r.get("source"), r.get("id") or r.get("slug") or r.get("name"))
                       not in seen]
            self._results.extend(results)
        else:
            self._results = results
        self._clear_list()
        query = self.name_edit.text().strip()
        if not query and not browsing:
            head = QLabel(tr("热门推荐"))
            head.setStyleSheet(
                f"color: {Theme.title}; font-size: 13px; font-weight: 700;"
                " background: transparent; padding: 10px 12px 6px 12px;")
            self.list_layout.addWidget(head)
        if not self._results:
            self.list_layout.addWidget(EmptyState(self.spec["icon"], self.spec["empty_search"]))
            self.list_layout.addStretch(1)
            return
        for row in self._results:
            self.list_layout.addWidget(PclResultRow(row, self._install, on_fav=self._toggle_fav))
        # 本页拿满说明源站可能还有下一页（本地热门推荐列表不翻页）
        if (query or browsing) and len(results) >= self._page_size:
            more = PushButton(tr("加载更多"))
            more.setFixedHeight(34)
            more.clicked.connect(self._load_more)
            self.list_layout.addWidget(more)
        self.list_layout.addStretch(1)

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
        elif self.spec.get("list_installed") == "get_installed_resourcepacks":
            # 资源包走带元数据的入口：pack.png 图标 / 描述 / 兼容版本（PCL2 同款）
            rows = self.backend.get_resourcepack_entries(inst) or []
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
        from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout
        from qfluentwidgets import SwitchButton, TransparentToolButton
        host = QFrame()
        host.setObjectName("pclRow")
        host.setStyleSheet(row_qss("pclRow"))
        lay = QHBoxLayout(host)
        lay.setContentsMargins(12, 6, 12, 6)
        name = row.get("filename") or row.get("name") or "?"
        rich = bool(row.get("description") or row.get("icon") or row.get("pack_format"))
        if rich:
            from mclauncher.utils import format_size
            host.setFixedHeight(64)
            lay.setSpacing(10)
            lay.addWidget(IconTile(name, size=40, image=row.get("icon") or None))
            col = QVBoxLayout()
            col.setSpacing(2)
            display = row.get("name") or (name[:-4] if name.lower().endswith(".zip") else name)
            cn = str(row.get("cn_name") or "").strip()
            if cn and cn not in display:
                display = f"{cn} · {display}"
            top_lab = QLabel(display)
            top_lab.setStyleSheet(
                f"color: {Theme.title}; font-size: 13px; font-weight: 600; background: transparent;")
            top_lab.setToolTip(name)
            col.addWidget(top_lab)
            bits = []
            if row.get("version"):
                bits.append(str(row["version"]))
            if row.get("mc_range"):
                bits.append("MC " + str(row["mc_range"]))
            elif row.get("pack_format"):
                bits.append(tr("格式 {n}").format(n=row["pack_format"]))
            if row.get("bytes"):
                bits.append(format_size(row["bytes"]))
            desc = " ".join(str(row.get("description") or "").split())
            if desc:
                bits.append(desc)
            sub = QLabel("  ·  ".join(bits)[:140] or " ")
            sub.setStyleSheet(f"color: {Theme.muted}; font-size: 11px; background: transparent;")
            if desc:
                sub.setToolTip(desc)
            col.addWidget(sub)
            lay.addLayout(col, 1)
        else:
            host.setFixedHeight(52)
            lab = QLabel(name)
            lab.setStyleSheet("font-size: 13px; background: transparent;")
            lay.addWidget(lab, 1)
        if row.get("mcmod_url"):
            wiki = TransparentToolButton(FIF.GLOBE)
            wiki.setToolTip(tr("打开 MC 百科页面"))
            wiki.clicked.connect(lambda _, u=row["mcmod_url"]: self._open_wiki(u))
            lay.addWidget(wiki)
        if "enabled" in row:
            sw = SwitchButton()
            sw.setChecked(bool(row.get("enabled")))
            sw.checkedChanged.connect(lambda on, n=name: self._toggle(n, on))
            lay.addWidget(sw)
        btn = TransparentToolButton(FIF.DELETE)
        btn.clicked.connect(lambda _, n=name: self._delete_installed(n))
        lay.addWidget(btn)
        return host

    def _open_wiki(self, url: str):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl(url))

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
                tr("将删除整个实例「{name}」及其文件（会尽量移入系统回收站，可找回）。").format(name=inst),
                self,
            )
            box.yesButton.setText(tr("删除实例"))
        elif fn == "delete_save":
            box = MessageBox(
                tr("删除世界存档"),
                tr("将删除世界「{name}」（会尽量移入系统回收站，可找回）。").format(name=filename)
                + "\n" + tr("建议先在「存档管理」里备份。"),
                self,
            )
            box.yesButton.setText(tr("删除"))
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
            self.list_layout.addWidget(PclResultRow(row, self._install, on_fav=self._toggle_fav))
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
