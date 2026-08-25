# -*- coding: utf-8 -*-
"""主窗口：细顶栏 + 主侧栏（启动/下载/AI 助手/更多）+ 底部下载任务。

页面懒加载：__init__ 只构造首屏必需的壳（启动页、下载/更多分区壳、
任务页），其余 16 个子页 + AI 页记录成工厂，第一次导航进入才构造。
冷启动从「同步建 21 个页面（每个还各自扫一遍磁盘）」变成建 4 个。
"""

import os
import time

from qfluentwidgets import FluentIcon as FIF, InfoBar, InfoBarPosition, setTheme, setThemeColor, Theme as FluentTheme
from qfluentwidgets.window.fluent_window import FluentWindowBase
from PySide6.QtCore import Qt, QEasingCurve, QPoint, QPropertyAnimation, QTimer
from PySide6.QtWidgets import QApplication, QLabel

from mclauncher import APP_DISPLAY_NAME, APP_VERSION
from .backend import BackendAPI
from .fly_anim import fly_to
from .pcl_chrome import (
    Theme, fade_stack_to, paint_theme_surfaces, ensure_theme_surfaces,
    PclSideBar, PclTitleBar, TITLE_H, SIDE_W,
)
from .widgets import pick_color
from .pages.launch_page import LaunchPage
from .pages.download_hub import DownloadSection, MoreSection
from .pages.tasks_page import DownloadDock, TasksPage
from mclauncher.i18n import tr

# 分区默认成员（子页归属可由 ui_section_members 自定义：哪栏、栏内顺序）
_SUB_DEFAULT_MEMBERS = {
    "download": ["version", "mod", "modpack", "datapack", "resource", "shader", "world", "java"],
    "more": ["instance", "mods", "account", "multiplayer", "servers", "playtime", "feedback", "settings"],
}
_ALL_SUB_KEYS = frozenset(k for keys in _SUB_DEFAULT_MEMBERS.values() for k in keys)
_TOP_KEYS = ("launch", "download", "ai", "more", "tasks")

# 子页标题与工厂方法名（归属按配置动态决定，这两份是静态元数据）
_SUB_TITLES = {
    "version": "原版游戏", "mod": "Mod", "modpack": "整合包", "datapack": "数据包",
    "resource": "资源包", "shader": "光影包", "world": "世界", "java": "Java",
    "instance": "实例", "mods": "模组", "account": "账号", "multiplayer": "联机",
    "servers": "服务器", "playtime": "时长", "feedback": "反馈", "settings": "设置",
}
_SUB_FACTORIES = {
    "version": "_make_version_page", "mod": "_make_mod_page",
    "modpack": "_make_modpack_page", "datapack": "_make_datapack_page",
    "resource": "_make_resource_page", "shader": "_make_shader_page",
    "world": "_make_world_page", "java": "_make_java_page",
    "instance": "_make_instance_page", "mods": "_make_mods_page",
    "account": "_make_account_page", "multiplayer": "_make_multiplayer_page",
    "servers": "_make_servers_page", "playtime": "_make_playtime_page",
    "feedback": "_make_feedback_page", "settings": "_make_settings_page",
}


def sub_title(key: str) -> str:
    return tr(_SUB_TITLES.get(key, key))


def section_members_from_config() -> dict[str, list[str]]:
    """读 ui_section_members：{download: [key…], more: [key…]}。

    非法键剔除、重复去重、漏掉的子页按默认归属补齐，顺序保留用户排列。
    """
    from mclauncher.config import CONFIG
    pinned = set(pinned_from_config())
    raw = CONFIG.get("ui_section_members")
    result: dict[str, list[str]] = {}
    seen: set[str] = set()
    for sec in ("download", "more"):
        keys = raw.get(sec) if isinstance(raw, dict) else None
        picked = []
        for k in keys or []:
            # 固定到侧栏的子页不属于任何分区（拖出去 = 移动），
            # 配置里残留的成员记录也一并忽略
            if k in _ALL_SUB_KEYS and k not in seen and k not in pinned:
                seen.add(k)
                picked.append(k)
        result[sec] = picked
    for sec, defaults in _SUB_DEFAULT_MEMBERS.items():
        for k in defaults:
            if k not in seen and k not in pinned:
                seen.add(k)
                result[sec].append(k)
    return result


def _default_section_for(key: str) -> str:
    for sec, defaults in _SUB_DEFAULT_MEMBERS.items():
        if key in defaults:
            return sec
    return "more"


# 侧栏一级项的图标与标题（供自定义排序/显隐重建用）
_NAV_SPECS = {
    "launch": (FIF.PLAY, "启动"),
    "download": (FIF.DOWNLOAD, "下载"),
    "ai": (getattr(FIF, "CHAT", None) or FIF.HELP, "AI 助手"),
    "more": (getattr(FIF, "MORE", None) or FIF.MENU, "更多"),
    "tasks": (FIF.CLOUD_DOWNLOAD, "下载任务"),
}


def pinned_from_config() -> list[str]:
    """固定到顶级侧栏的分区子页 key（拖拽固定，非法键过滤）。"""
    from mclauncher.config import CONFIG
    raw = CONFIG.get("ui_nav_pinned") or []
    seen, picked = set(), []
    for k in raw:
        if k in _ALL_SUB_KEYS and k not in seen:
            seen.add(k)
            picked.append(k)
    return picked


def _pinned_nav_spec(key: str):
    from .pages.home_cards import quick_icon
    return ("item", key, quick_icon(key), sub_title(key), False, True)


def nav_items_from_config() -> list:
    """生成侧栏条目：一级项与固定的分区子页按 ui_nav_order 混排。

    ui_nav_order 是完整序列（可同时含一级键和固定子页键，拖拽自由混排
    的落点就存在这里）；没进序列的固定子页插在「更多」前（都不在则插在
    「下载任务」前 / 末尾），一级键缺失自动补到末尾。
    """
    from mclauncher.config import CONFIG
    raw = list(CONFIG.get("ui_nav_order") or [])
    pinned = pinned_from_config()
    hidden = set(CONFIG.get("ui_nav_hidden") or [])
    order = []
    for k in raw:
        if k in _TOP_KEYS and k not in order:
            order.append(k)
        elif k in pinned and k not in order:
            order.append(k)
    for k in _TOP_KEYS:
        if k not in order:
            order.append(k)
    # 缺席的固定子页：插到锚点前
    late = [k for k in pinned if k not in order]
    anchor = next((a for a in ("more", "tasks") if a in order), None)
    if late:
        if anchor is not None:
            order[order.index(anchor):order.index(anchor)] = late
        else:
            order.extend(late)
    items = []
    visible = [k for k in order if not (k in _TOP_KEYS and k in hidden)]
    for i, key in enumerate(visible):
        if key == "tasks" and i == len(visible) - 1 and len(visible) > 1:
            items.append(("stretch",))
        if key in _TOP_KEYS:
            fif, title = _NAV_SPECS[key]
            items.append(("item", key, fif, tr(title), False, True))
        else:
            items.append(_pinned_nav_spec(key))
    return items


def sidebar_width_from_config() -> int:
    from mclauncher.config import CONFIG
    try:
        w = int(CONFIG.get("ui_sidebar_width") or 0)
    except (TypeError, ValueError):
        w = 0
    return w if 140 <= w <= 320 else SIDE_W


class MainWindow(FluentWindowBase):
    def __init__(self):
        # FluentWindowBase 在 super() / resize 时就会发 resizeEvent，
        # 这些属性必须先占位，否则一点开就闪退。
        self.side = None
        self.task_badge = None
        self.download_dock = None
        self._pages = {}
        self._nav_cover = None
        self._nav_fade = None
        self._dock_anim = None
        self._fly_jobs = []
        self._launch_after = {}
        self._clip_seen = None
        self._quit_on_exit = False
        self._deferred_boot_reload = False
        self._built = {}          # 子页 key -> 已构造页面（懒加载缓存）
        self._by_obj = {}         # id(page) -> key（反向查找，避免比较时触发构造）
        self._data_dirty = False  # ui_changed 置位：下次导航/刷新必须真刷数据
        super().__init__()
        self.setWindowTitle(f"{APP_DISPLAY_NAME} v{APP_VERSION}")
        self.setMicaEffectEnabled(False)
        self.setCustomBackgroundColor("#FFFFFF", "#1B1B1B")
        setThemeColor("#2E9B6B", save=False)

        self.backend = BackendAPI(self)
        self.apply_theme()

        # ---- 分区壳（便宜，先建；子页进分区时才构造）----
        self.download_section = DownloadSection(self.backend, self)
        self.more_section = MoreSection(self.backend, self)
        # 分区也注册进反向表：_visible_key 才能从「下载/更多」下钻到
        # 分区内子页（下载悬浮球隐藏规则、快捷入口跳转都依赖它）。
        self._by_obj[id(self.download_section)] = "download"
        self._by_obj[id(self.more_section)] = "more"
        self._bind_sections()
        self._connect_section_signals()

        # ---- 首屏页面 ----
        self.launch_page = LaunchPage(self.backend, self)
        self._register_page(self.launch_page, "launch")
        self.tasks_page = TasksPage(self.backend, self)
        self._register_page(self.tasks_page, "tasks")

        self._pages = {
            "launch": self.launch_page,
            "download": self.download_section,
            "more": self.more_section,
            "tasks": self.tasks_page,
        }

        bar = PclTitleBar(self)
        self.setTitleBar(bar)

        self._side_items = nav_items_from_config()
        self.side = PclSideBar(self._side_items, width=sidebar_width_from_config())
        self.side.currentChanged.connect(self._on_nav)
        self.side.widthCommitted.connect(self._on_side_width)
        self.side.pinAtRequested.connect(self._pin_nav_at)
        self.side.reorderRequested.connect(self._on_sidebar_reorder)
        self.side.editLayoutRequested.connect(
            lambda: self.launch_page.canvas.set_edit_mode(True))

        self.hBoxLayout.setContentsMargins(0, TITLE_H, 0, 0)
        self.hBoxLayout.addWidget(self.side)
        self.hBoxLayout.addWidget(self.stackedWidget)
        for page in self._pages.values():
            self.stackedWidget.addWidget(page)
        self.stackedWidget.setCurrentWidget(self.launch_page)
        self.side.set_current("launch", emit=False)

        self._create_task_badge()

        self._launch_after = {}
        self.download_dock = DownloadDock(self.backend, self)
        self.backend.finished.connect(self._notify_task)
        self.backend.theme_changed.connect(self.apply_theme)
        self._ui_refresh = QTimer(self)
        self._ui_refresh.setSingleShot(True)
        self._ui_refresh.setInterval(280)
        self._ui_refresh.timeout.connect(lambda: self._refresh_pages(force=True))
        self.backend.ui_changed.connect(self._on_ui_changed)
        self.backend.task_count_changed.connect(self._update_task_badge)
        self.backend.game_started.connect(self._on_game_started)
        self.backend.game_exited.connect(self._on_game_exited)
        self.stackedWidget.currentChanged.connect(lambda *_: self._place_download_dock())
        self.resize(1180, 760)
        # 拖拽导入：整合包 / 模组 / 世界 / 资源包 / 光影 / 数据包丢进窗口即装
        self.setAcceptDrops(True)
        # 上面 apply_theme() 时 _pages 还是空的，ScrollArea 表面没刷到。
        # 页面全部就位后再刷一遍，深色启动才不会白字压浅底。
        self.apply_theme()
        QTimer.singleShot(400, self._boot_extras)

    # ------------------------------------------------------------------
    # 懒加载基建
    # ------------------------------------------------------------------
    def _sub_getter(self, key: str):
        return lambda: self._ensure_sub(key)

    def _bind_sections(self, built_pages: dict | None = None):
        """按 ui_section_members 组装 _sub_specs 并 bind 两个分区壳。

        built_pages 传入时（重建分区），把已构造的子页直接 add_page 进
        新壳，保持懒加载缓存不丢、不重复构造。
        """
        members = section_members_from_config()
        pinned = set(pinned_from_config())
        self._sub_specs = {}
        for sec_key in ("download", "more"):
            section = getattr(self, f"{sec_key}_section")
            for key in members[sec_key]:
                self._sub_specs[key] = (section, sub_title(key),
                                        getattr(self, _SUB_FACTORIES[key]))
                section.bind([(sub_title(key), self._sub_getter(key), key)])
        # 固定到侧栏的子页：不 bind（无横条按钮），但保留工厂与归属，
        # 点侧栏按钮时构造进默认分区的栈里直接展示
        for key in pinned:
            if key not in self._sub_specs:
                sec_key = _default_section_for(key)
                section = getattr(self, f"{sec_key}_section")
                self._sub_specs[key] = (section, sub_title(key),
                                        getattr(self, _SUB_FACTORIES[key]))
        if built_pages:
            for key, page in built_pages.items():
                spec = self._sub_specs.get(key)
                if spec is not None:
                    # 固定页只进栈、不建横条按钮（title 空）
                    spec[0].add_page(page, "" if key in pinned else spec[1])

    def _rebuild_sections(self):
        """应用分区内容自定义后重建两个分区壳（已构造子页随迁）。"""
        cur_key = self._visible_key()
        old = {"download": self.download_section, "more": self.more_section}
        # 记录当前停留在哪个壳上，重建后回到同一视图
        cur_widget = self.stackedWidget.currentWidget()
        cur_section_key = next((k for k, w in old.items() if w is cur_widget), None)

        self.download_section = DownloadSection(self.backend, self)
        self.more_section = MoreSection(self.backend, self)
        for k, w in old.items():
            self._by_obj.pop(id(w), None)
        for sec_key, w in (("download", self.download_section),
                           ("more", self.more_section)):
            self._by_obj[id(w)] = sec_key
        self._bind_sections(built_pages=self._built)
        self._connect_section_signals()

        for sec_key, w in old.items():
            self.stackedWidget.removeWidget(w)
            w.deleteLater()
        self.stackedWidget.addWidget(self.download_section)
        self.stackedWidget.addWidget(self.more_section)
        self._pages["download"] = self.download_section
        self._pages["more"] = self.more_section
        # 新壳要完整刷一次主题（注册页时清签名，apply_theme 不会短路跳过）
        self._theme_sig = None

        # 回到原来的视图：原来在分区里就回到那个分区的同一个子页
        target = None
        if cur_key in self._sub_specs and cur_key in self._built:
            target = self._built[cur_key]
        if target is not None and cur_section_key is not None:
            shell = getattr(self, f"{cur_section_key}_section")
            if shell.has_page(target):
                self.stackedWidget.setCurrentWidget(shell)
                shell.show_page(target)
                self.side.set_current(cur_section_key, emit=False)
                self.apply_theme()
                return
        fallback = (self.download_section if cur_section_key == "download"
                    else self.more_section if cur_section_key == "more"
                    else None)
        if fallback is not None:
            fallback.ensure_first()
            self.stackedWidget.setCurrentWidget(fallback)
        self.apply_theme()

    def _create_task_badge(self):
        """把任务角标挂到当前侧栏的「下载任务」按钮上（侧栏重建后重挂）。"""
        target = self.side.button("tasks")
        self.task_badge = QLabel("0", target)
        self.task_badge.setObjectName("taskBadge")
        self.task_badge.setAlignment(Qt.AlignCenter)
        self.task_badge.setFixedHeight(16)
        self.task_badge.setMinimumWidth(16)
        self.task_badge.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.task_badge.setStyleSheet(
            "#taskBadge { background: #E23C3C; color: #fff; border-radius: 8px;"
            " font-size: 10px; font-weight: 700; padding: 0 4px; }"
        )
        self.task_badge.hide()

    def _connect_section_signals(self):
        # 注意不能用 UniqueConnection：该标志只对成员函数槽有效，接
        # lambda 会静默连不上（拖回分区横条没反应的根因）。每次重建
        # 分区壳都会换新对象，天然不会重复连接。
        for sec_key, shell in (("download", self.download_section),
                               ("more", self.more_section)):
            shell.cat.unpinRequested.connect(
                lambda k, s=sec_key: self._unpin_nav(k, s))

    def _on_side_width(self, width: int):
        from mclauncher.config import CONFIG
        CONFIG.set("ui_sidebar_width", int(width))
        CONFIG.save()
        self._place_task_badge()

    def _sidebar_sequence(self) -> list[str]:
        """当前侧栏可见键序列（一级 + 固定子页，按显示顺序）。"""
        return [s[1] for s in nav_items_from_config() if s[0] == "item"]

    def _write_sidebar_sequence(self, seq: list[str]):
        """落盘完整混合序列：ui_nav_order 存位置（一级键+固定子页的
        真实排列），ui_nav_pinned 只记哪些子页被固定。"""
        from mclauncher.config import CONFIG
        seen: set[str] = set()
        clean = [k for k in seq
                 if (k in _TOP_KEYS or k in _ALL_SUB_KEYS)
                 and not (k in seen or seen.add(k))]
        for k in _TOP_KEYS:
            if k not in clean:
                clean.append(k)
        pinned = [k for k in clean if k in _ALL_SUB_KEYS]
        CONFIG.set("ui_nav_order", clean)
        CONFIG.set("ui_nav_pinned", pinned or None)
        CONFIG.save()

    def _on_sidebar_reorder(self, key: str, target: str, before: bool):
        # 固定子页拖到「下载/更多」一级按钮上 = 放回那个分区（直觉手势）
        if key in _ALL_SUB_KEYS and target in ("download", "more"):
            self._unpin_nav(key, target)
            return
        seq = self._sidebar_sequence()
        if key not in seq or target not in seq or key == target:
            return
        seq.remove(key)
        idx = seq.index(target) + (0 if before else 1)
        seq.insert(idx, key)
        self._write_sidebar_sequence(seq)
        self._rebuild_sidebar()

    def _pin_nav_at(self, key: str, target: str | None, before: bool):
        """分区子页固定到侧栏落点（target 为空则追加在「更多」前）。"""
        if key not in self._sub_specs:
            return
        if key in pinned_from_config():
            return
        if target:
            seq = self._sidebar_sequence()
            if target in seq:
                if not self._take_from_section(key):
                    return
                seq.insert(seq.index(target) + (0 if before else 1), key)
                self._write_sidebar_sequence(seq)
                self._rebuild_sections()
                self._rebuild_sidebar()
                return
        self._pin_nav(key)

    def _take_from_section(self, key: str) -> bool:
        """移动语义：固定前把 key 从分区成员里拿走；分区只剩它时拒绝。"""
        from mclauncher.config import CONFIG
        members = section_members_from_config()
        for sec in ("download", "more"):
            if key in members[sec]:
                if len(members[sec]) <= 1:
                    from qfluentwidgets import InfoBar, InfoBarPosition
                    InfoBar.warning(
                        tr("无法移出"),
                        tr("该分区只剩这一个子页，移走会变空栏；先在「自定义分区」里补充其它子页"),
                        parent=self, position=InfoBarPosition.TOP, duration=3500)
                    return False
                members[sec].remove(key)
                CONFIG.set("ui_section_members", members)
                CONFIG.save()
                return True
        return True  # 本就不在分区里（例如配置残留），直接放行

    def _pin_nav(self, key: str):
        """分区子页固定为顶级侧栏项（移动：原分区里不再显示）。"""
        from mclauncher.config import CONFIG
        if key not in self._sub_specs:
            return
        pinned = pinned_from_config()
        if key in pinned:
            return
        if not self._take_from_section(key):
            return
        pinned.append(key)
        CONFIG.set("ui_nav_pinned", pinned)
        # 同步进混合序列（插在「更多」前），保持落点位置持久
        seq = self._sidebar_sequence()
        if "more" in seq:
            seq.insert(seq.index("more"), key)
        else:
            seq.append(key)
        CONFIG.set("ui_nav_order", [k for k in seq if k in _TOP_KEYS])
        CONFIG.save()
        self._rebuild_sections()
        self._rebuild_sidebar()

    def _unpin_nav(self, key: str, back_section: str | None = None):
        """取消固定；拖回某个分区横条时放回那个分区，否则回默认分区。"""
        from mclauncher.config import CONFIG
        pinned = pinned_from_config()
        if key not in pinned:
            return
        pinned.remove(key)
        CONFIG.set("ui_nav_pinned", pinned or None)
        if back_section in ("download", "more"):
            members = section_members_from_config()
            for sec in ("download", "more"):
                if key in members[sec]:
                    members[sec].remove(key)
            members[back_section].append(key)
            CONFIG.set("ui_section_members", members)
        CONFIG.save()
        self._rebuild_sections()
        self._rebuild_sidebar()

    def _rebuild_sidebar(self):
        """应用侧栏自定义（排序/显隐/宽度）后重建侧栏，保留当前选中项。"""
        current = None
        old = getattr(self, "side", None)
        if old is not None:
            for key, btn in old._buttons.items():
                if btn.isChecked():
                    current = key
                    break
        badge = getattr(self, "task_badge", None)
        if badge is not None:
            badge.hide()
            badge.setParent(None)
            badge.deleteLater()
            self.task_badge = None
        if old is not None:
            self.hBoxLayout.removeWidget(old)
            old.deleteLater()
        self._side_items = nav_items_from_config()
        self.side = PclSideBar(self._side_items, width=sidebar_width_from_config())
        self.side.currentChanged.connect(self._on_nav)
        self.side.widthCommitted.connect(self._on_side_width)
        self.side.pinAtRequested.connect(self._pin_nav_at)
        self.side.reorderRequested.connect(self._on_sidebar_reorder)
        self.side.editLayoutRequested.connect(
            lambda: self.launch_page.canvas.set_edit_mode(True))
        self.hBoxLayout.insertWidget(0, self.side)
        self._create_task_badge()
        # 恢复选中态；原来的键被隐藏时回落到第一个可见项
        keys = [s[1] for s in self._side_items if s[0] == "item"]
        want = current if current in keys else (keys[0] if keys else None)
        if want is not None:
            self.side.set_current(want, emit=False)
        self._place_task_badge()
        try:
            self._update_task_badge(self.backend._download_task_count())
        except Exception:
            pass

    def _register_page(self, page, key: str):
        self._by_obj[id(page)] = key
        # 新页面入列后，apply_theme 的签名短路必须失效：
        # 否则启动时「页面建好后的第二次 apply_theme」会被同签名跳过，
        # 首屏页面表面没刷（深色启动白字压浅底的老 bug 就回来了）。
        self._theme_sig = None

    def _finish_page_build(self, page):
        """晚于 apply_theme 构造的页面：补一次表面刷新 + 一次性样式。"""
        try:
            ensure_theme_surfaces(page)
        except Exception:
            pass
        restyle = getattr(page, "restyle", None)
        if callable(restyle):
            try:
                restyle()
            except Exception:
                pass

    def _ensure_sub(self, key: str):
        page = self._built.get(key)
        if page is not None:
            return page
        section, title, factory = self._sub_specs[key]
        page = factory()
        self._built[key] = page
        self._register_page(page, key)
        # 固定到侧栏的子页只进分区栈展示，不建横条按钮（移动语义）
        pinned = set(pinned_from_config())
        section.add_page(page, "" if key in pinned else title)
        self._finish_page_build(page)
        return page

    def _ensure_top(self, key: str):
        page = self._pages.get(key)
        if page is not None:
            return page
        if key == "ai":
            page = self._make_ai_page()
        else:
            return None
        self._pages[key] = page
        self._register_page(page, key)
        self.stackedWidget.addWidget(page)
        self._finish_page_build(page)
        return page

    # ---- 子页工厂（import 放在工厂里：未访问的页面连模块都不加载）----
    def _make_version_page(self):
        from .pages.version_page import VersionPage
        return VersionPage(self.backend, self)

    def _make_mod_page(self):
        from .pages.catalog_page import ModPage
        return ModPage(self.backend, self)

    def _make_modpack_page(self):
        from .pages.catalog_page import ModpackPage
        return ModpackPage(self.backend, self)

    def _make_datapack_page(self):
        from .pages.catalog_page import DatapackPage
        return DatapackPage(self.backend, self)

    def _make_resource_page(self):
        from .pages.catalog_page import ResourcePackPage
        return ResourcePackPage(self.backend, self)

    def _make_shader_page(self):
        from .pages.catalog_page import ShaderPage
        return ShaderPage(self.backend, self)

    def _make_world_page(self):
        from .pages.catalog_page import WorldPage
        return WorldPage(self.backend, self)

    def _make_java_page(self):
        from .pages.java_page import JavaPage
        return JavaPage(self.backend, self)

    def _make_instance_page(self):
        from .pages.instance_page import InstancePage
        return InstancePage(self.backend, self)

    def _make_mods_page(self):
        from .pages.mod_page import ModManagerPage
        return ModManagerPage(self.backend, self)

    def _make_account_page(self):
        from .pages.account_page import AccountPage
        return AccountPage(self.backend, self)

    def _make_multiplayer_page(self):
        from .pages.multiplayer_page import MultiplayerPage
        return MultiplayerPage(self.backend, self)

    def _make_servers_page(self):
        from .pages.servers_page import ServerPage
        return ServerPage(self.backend, self)

    def _make_playtime_page(self):
        from .pages.playtime_page import PlaytimePage
        return PlaytimePage(self.backend, self)

    def _make_feedback_page(self):
        from .pages.feedback_page import FeedbackPage
        return FeedbackPage(self.backend, self)

    def _make_settings_page(self):
        from .pages.settings_page import SettingsPage
        return SettingsPage(self.backend, self)

    def _make_ai_page(self):
        from .pages.ai_page import AiPage
        return AiPage(self.backend, self)

    # ---- 懒加载属性：只在确实要用时才构造 ----
    @property
    def version_page(self):
        return self._ensure_sub("version")

    @property
    def mod_page(self):
        return self._ensure_sub("mod")

    @property
    def modpack_page(self):
        return self._ensure_sub("modpack")

    @property
    def datapack_page(self):
        return self._ensure_sub("datapack")

    @property
    def resource_page(self):
        return self._ensure_sub("resource")

    @property
    def shader_page(self):
        return self._ensure_sub("shader")

    @property
    def world_page(self):
        return self._ensure_sub("world")

    @property
    def java_page(self):
        return self._ensure_sub("java")

    @property
    def instance_page(self):
        return self._ensure_sub("instance")

    @property
    def mods_page(self):
        return self._ensure_sub("mods")

    @property
    def account_page(self):
        return self._ensure_sub("account")

    @property
    def multiplayer_page(self):
        return self._ensure_sub("multiplayer")

    @property
    def servers_page(self):
        return self._ensure_sub("servers")

    @property
    def playtime_page(self):
        return self._ensure_sub("playtime")

    @property
    def feedback_page(self):
        return self._ensure_sub("feedback")

    @property
    def settings_page(self):
        return self._ensure_sub("settings")

    @property
    def ai_page(self):
        return self._ensure_top("ai")

    def apply_theme(self):
        color = self.backend.get_setting("theme_color", "#2E9B6B") or "#2E9B6B"
        dark = bool(self.backend.get_setting("ui_dark", False))
        image = str(self.backend.get_setting("ui_background", "") or "").strip()
        # 签名短路：主题相关三键没变就直接返回。设置保存、探针、双保险
        # 路径都会重复触发 apply_theme，全量跑一次要重刷所有已构造页面。
        # 新页面注册时会清掉签名（见 _register_page），不会漏刷首屏。
        sig = (dark, str(color), image)
        if getattr(self, "_theme_sig", None) == sig:
            return
        self._theme_sig = sig
        self.setUpdatesEnabled(False)
        try:
            self._apply_theme_impl(color, dark)
        finally:
            self.setUpdatesEnabled(True)

    def _apply_theme_impl(self, color, dark):
        Theme.apply(dark)
        # lazy=True：qfluentwidgets 只重刷当前可见控件，隐藏页打 dirty-qss
        # 标记、下一次 Paint 时由 DirtyStyleSheetWatcher 补刷。全量重刷是
        # 主题翻转卡 3 秒多的元凶——页面建得越多越惨。
        setThemeColor(color, save=False, lazy=True)
        setTheme(FluentTheme.DARK if dark else FluentTheme.LIGHT, save=False, lazy=True)
        # 必须固定传「浅色槽 / 深色槽」两套值。以前写 Theme.bg 当浅色槽，
        # 一切深色 Theme.bg 已是 #1B1B1B，会把浅色槽也污染成深色，切回浅色时
        # Fluent 背景动画/缓存还会短暂甚至一直停在脏值上。
        self.setCustomBackgroundColor("#FFFFFF", "#1B1B1B")
        if hasattr(self, "_updateBackgroundColor"):
            try:
                self._updateBackgroundColor()
            except Exception:
                pass
        bar = self.titleBar
        if hasattr(bar, "restyle"):
            bar.restyle()
        side = getattr(self, "side", None)
        if side is not None and hasattr(side, "restyle"):
            side.restyle()
        for key in ("download_section", "more_section"):
            cat = getattr(getattr(self, key, None), "cat", None)
            if cat is not None and hasattr(cat, "restyle"):
                cat.restyle()
        # 只立即重刷当前可见页；其余已构造页面打「主题待刷」标记，
        # 导航进入时再补。主题翻转最贵的不是改样式，而是每次
        # setStyleSheet 后 Qt 的样式重算+重排——给几十个看不见的
        # 页面全做一遍纯属浪费（一次翻转 3 秒多就是这么来的）。
        visible = self._visible_key()
        for page in list(self._pages.values()) + list(self._built.values()):
            if page is None:
                continue
            key = self._by_obj.get(id(page))
            if (page is not self.download_section and page is not self.more_section
                    and key is not None and key != visible):
                page._pymcl_theme_stale = True
                continue
            if hasattr(page, "restyle"):
                try:
                    page.restyle()
                except Exception:
                    pass
        self._apply_background()
        self._paint_page_surfaces()
        if getattr(self, "_pages", None):
            page = self.stackedWidget.currentWidget()
            if page is not None:
                if self.isVisible():
                    self._reload_page(page, force=True)
                elif not self._deferred_boot_reload:
                    # 首帧前的两次 apply_theme 都会走到这里；同步 reload 会在
                    # show() 前扫盘（实例/账号/版本），合并成事件循环空转后的
                    # 一次，首帧先出壳。singleShot(0) 在 exec() 后才触发，
                    # 那时 show() 已发生。
                    self._deferred_boot_reload = True
                    QTimer.singleShot(0, self._boot_reload)
        self.update()

    def _boot_reload(self):
        self._deferred_boot_reload = False
        page = self.stackedWidget.currentWidget() if getattr(self, "_pages", None) else None
        if page is not None:
            self._reload_page(page, force=True)

    def _refresh_if_stale(self, page):
        """主题翻转时被延迟重刷的页面，进入视野前补刷。"""
        if page is None or not getattr(page, "_pymcl_theme_stale", False):
            return
        try:
            page._pymcl_theme_stale = False
            ensure_theme_surfaces(page)
            restyle = getattr(page, "restyle", None)
            if callable(restyle):
                restyle()
        except Exception:
            pass

    def _paint_page_surfaces(self):
        """刷页面 + ScrollArea/viewport/宿主底色，并修正 QFormLayout 系统标签。

        Fluent 卡片深色是半透明白，必须压在 Theme.bg 上；只刷 page 本身不够，
        设置/实例里的 ScrollArea 仍是浅灰时就会白字压浅底。
        ensure_ 带主题版本守卫：Theme.apply 自增 _version，切主题必真刷；
        导航路径重复调用时同键直接跳过，省掉 4 轮 findChildren 全树遍历。
        隐藏页面在这里只打待刷标记（见 _apply_theme_impl）。
        """
        visible = self._visible_key()
        for page in list(self._pages.values()) + list(self._built.values()):
            if page is None:
                continue
            key = self._by_obj.get(id(page))
            if (page is not self.download_section and page is not self.more_section
                    and key is not None and key != visible):
                page._pymcl_theme_stale = True
                continue
            ensure_theme_surfaces(page)
        dock = getattr(self, "download_dock", None)
        if dock is not None:
            ensure_theme_surfaces(dock)
            if hasattr(dock, "restyle"):
                try:
                    dock.restyle()
                except Exception:
                    pass

    def _apply_background(self):
        """把设置里的背景图刷到内容区。

        路径是直接拼进 QSS 的，两个坑必须挡住：
        文件被用户删掉后 Qt 只会静默画成空白，界面看起来像坏了；
        路径里带单引号（`D:/我的'图/bg.png`）会提前闭合 url('...')，整张样式表连带失效。

        只给 stacked 设 border-image 还不够：页面作为子控件铺着不透明 Theme.bg，
        永远盖在父背景之上。所以这里同时裁决 Theme.background_active，
        paint_theme_surfaces 按它把页面表面刷透明，图才真正透得出来。
        """
        image = str(self.backend.get_setting("ui_background", "") or "").strip()
        bg = Theme.bg
        active = bool(image) and os.path.isfile(image)
        Theme.background_active = active
        if not active:
            # 无背景图时也要显式铺 Theme.bg，否则 stacked 透明，下面页又是浅色默认底
            self.stackedWidget.setStyleSheet(
                f"QStackedWidget {{ background-color: {bg}; border: none; }}"
            )
            return
        path = image.replace("\\", "/").replace("'", "%27")
        self.stackedWidget.setStyleSheet(
            f"QStackedWidget {{ background-color: {bg};"
            f" border-image: url('{path}') 0 0 0 0 stretch stretch; }}"
        )

    def _boot_extras(self):
        if self.backend.get_setting("first_run", True):
            from .pages.first_run import FirstRunDialog
            dlg = FirstRunDialog(self.backend, self)
            if dlg.exec():
                dlg.apply()
            else:
                data = self.backend.get_settings()
                data["first_run"] = False
                self.backend.save_settings(data)
        self._ask_feedback_consent()
        if not self.backend.get_setting("auto_check_update", True):
            return

        def ok(info):
            info = info or {}
            if info.get("has_update"):
                InfoBar.info(tr("发现更新"), info.get("message") or tr("到设置里安装"), parent=self,
                             position=InfoBarPosition.TOP_RIGHT, duration=5000)

        self.backend.call_async(self.backend.check_update, ok, lambda *_: None)

    def _on_game_started(self):
        mode = self.backend.get_setting("launcher_visibility") or "keep"
        if mode == "close":
            self._quit_on_exit = True
            self.hide()
        elif mode in ("hide", "hide_reopen"):
            self.hide()
        elif mode == "minimize":
            self.showMinimized()

    def _on_game_exited(self, _code):
        if self._quit_on_exit:
            self._quit_on_exit = False
            QApplication.instance().quit()

    # ------------------------------------------------------------------
    # 拖拽导入（对标 PCL2：文件拖进窗口自动识别安装）
    # ------------------------------------------------------------------
    def dragEnterEvent(self, e):
        from mclauncher.import_files import SUPPORTED_EXTS
        md = e.mimeData()
        if md.hasUrls() and any(
                u.isLocalFile() and u.toLocalFile().lower().endswith(SUPPORTED_EXTS)
                for u in md.urls()):
            e.acceptProposedAction()
            return
        super().dragEnterEvent(e)

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if not paths:
            super().dropEvent(e)
            return
        e.acceptProposedAction()
        self._import_dropped(paths)

    def _import_dropped(self, paths: list):
        infos = [self.backend.classify_import(p) for p in paths]
        known = [i for i in infos if i.get("kind") != "unknown"]
        unknown = [i for i in infos if i.get("kind") == "unknown"]
        if not known:
            InfoBar.warning(
                tr("无法识别"),
                tr("支持整合包(.mrpack/.zip)、模组(.jar)、世界、资源包、光影包、数据包"),
                parent=self, position=InfoBarPosition.TOP, duration=4000)
            return
        from mclauncher.config import CONFIG
        from qfluentwidgets import MessageBox
        inst = str(CONFIG.get("default_instance") or "default")
        lines = [f"· {i['name']} → {tr(i.get('label') or '')}" for i in known]
        lines += [f"· {i['name']} → {tr('无法识别，跳过')}" for i in unknown]
        body = (tr("检测到 {n} 个可导入文件：").format(n=len(known)) + "\n"
                + "\n".join(lines) + "\n\n"
                + tr("导入到实例「{inst}」？整合包会安装对应游戏版本，其余直接放入对应目录。").format(inst=inst))
        box = MessageBox(tr("拖拽导入"), body, self)
        box.yesButton.setText(tr("导入"))
        box.cancelButton.setText(tr("取消"))
        if not box.exec():
            return
        started = 0
        for i in known:
            try:
                self.backend.import_local_file(i["path"], kind=i["kind"])
                started += 1
            except Exception as err:
                InfoBar.error(tr("导入失败"), f"{i['name']}: {err}", parent=self,
                              position=InfoBarPosition.TOP_RIGHT, duration=5000)
        if started:
            InfoBar.success(
                tr("已开始导入"),
                tr("共 {n} 个任务，进度见「下载任务」").format(n=started),
                parent=self, position=InfoBarPosition.TOP_RIGHT, duration=3500)
            return
        mode = self.backend.get_setting("launcher_visibility") or "keep"
        if mode == "hide_reopen":
            self.show()
            self.raise_()
            self.activateWindow()

    def _ask_feedback_consent(self):
        from mclauncher import feedback as fb
        from .widgets import prompt_feedback_consent
        if not fb.consent_asked():
            prompt_feedback_consent(self)
            return
        if fb.has_consent():
            fb.start_heartbeat()

    def _on_ui_changed(self):
        self._data_dirty = True
        self._ui_refresh.start()

    def _on_nav(self, key: str):
        if key in self._sub_specs:
            # 固定到侧栏的分区子页：进分区展示，选中态留在固定按钮上
            self.switchTo(key)
            self.side.set_current(key, emit=False)
            return
        page = self._pages.get(key)
        if page is None:
            page = self._ensure_top(key)
        if page is None:
            return
        if hasattr(page, "ensure_first"):
            page.ensure_first()
        # 主题翻转时被延迟的页面，切过来之前补刷，首帧就是正确配色
        self._refresh_if_stale(page)
        if hasattr(page, "current_page"):
            self._refresh_if_stale(page.current_page())
        fade_stack_to(self.stackedWidget, page, self)
        ensure_theme_surfaces(page)
        self._reload_page(page)

    def _visible_key(self):
        """当前显示页的 key；落在下载/更多分区时下钻到分区内子页。"""
        page = self.stackedWidget.currentWidget()
        if page is None:
            return None
        key = self._by_obj.get(id(page))
        if key in ("download", "more") and hasattr(page, "current_page"):
            inner = page.current_page()
            if inner is not None and inner is not page:
                key = self._by_obj.get(id(inner)) or key
        return key

    def switchTo(self, interface):
        if isinstance(interface, str):
            interface = (self._ensure_top(interface) if interface in _TOP_KEYS
                         else self._ensure_sub(interface))
            if interface is None:
                return
        for key, sec in (("download", self.download_section), ("more", self.more_section)):
            if sec.has_page(interface) and interface is not sec:
                fade_stack_to(self.stackedWidget, sec, self)
                self.side.set_current(key, emit=False)
                sec.show_page(interface)
                return
        fade_stack_to(self.stackedWidget, interface, self)
        for key, page in self._pages.items():
            if page is interface:
                self.side.set_current(key, emit=False)
                self._reload_page(page)
                return

    def _reload_page(self, page, force: bool = False):
        if page is None:
            return
        self._refresh_if_stale(page)
        # 同一页面 1.2 秒内的重复 reload 直接跳过（导航来回点、分区壳
        # 二次触发都会撞上）；数据真变了走 force 或 _data_dirty。
        now = time.monotonic()
        if not force and not self._data_dirty \
                and (now - getattr(page, "_pymcl_last_reload", 0.0)) < 1.2:
            return
        try:
            page._pymcl_last_reload = now
        except Exception:
            pass
        if page is self.download_section or page is self.more_section:
            inner = page.current_page()
            if inner is not None and inner is not page:
                self._reload_page(inner, force)
            return
        key = self._by_obj.get(id(page))
        if key == "version":
            if getattr(page, "_all_versions", None):
                page.reload_installed_only()
            else:
                page.reload()
            return
        if key == "java":
            page.reload(scan_system=False)
            return
        if hasattr(page, "reload_installed"):
            page.reload_installed()
            return
        if hasattr(page, "reload"):
            try:
                page.reload()
            except TypeError:
                pass

    def _update_task_badge(self, count: int):
        if count <= 0:
            self.task_badge.hide()
            return
        prev = int(self.task_badge.property("count") or 0)
        self.task_badge.setText("99+" if count > 99 else str(count))
        self.task_badge.adjustSize()
        self.task_badge.setFixedHeight(16)
        self.task_badge.show()
        self.task_badge.setProperty("count", int(count))
        self._place_task_badge()
        if count > prev:
            from .motion import pop
            pop(self.task_badge)

    def _place_task_badge(self):
        side = getattr(self, "side", None)
        badge = getattr(self, "task_badge", None)
        if side is None or badge is None or badge.isHidden():
            return
        btn = side.button("tasks")
        if btn is None:
            return
        icon_w = btn.iconSize().width() if not btn.icon().isNull() else 16
        pad, gap = 14, 6
        text_w = btn.fontMetrics().horizontalAdvance(btn.text())
        x = pad + icon_w + gap + text_w + 6
        y = (btn.height() - badge.height()) // 2
        x = min(x, btn.width() - badge.width() - 8)
        x = max(pad + icon_w, x)
        badge.move(x, y)
        badge.raise_()

    def _place_download_dock(self, animate=True):
        dock = getattr(self, "download_dock", None)
        if not dock:
            return
        hide_on = {"settings", "instance", "tasks", "feedback"}
        want = bool(getattr(dock, "_active", None)) and self._visible_key() not in hide_on
        g = self.stackedWidget.geometry()
        dock.adjustSize()
        w = min(640, max(420, g.width() - 40))
        h = dock.sizeHint().height()
        x = g.x() + (g.width() - w) // 2
        y = g.y() + g.height() - h - 18
        dest = QPoint(max(g.x() + 12, x), max(g.y() + 12, y))
        dock.setFixedWidth(w)
        prev = getattr(self, "_dock_anim", None)
        if prev is not None:
            prev.stop()
            self._dock_anim = None
        if want:
            if not dock.isVisible():
                dock.move(dest.x(), dest.y() + 28)
                dock.show()
                dock.raise_()
                if animate:
                    self._dock_anim = self._anim_pos(dock, dest, 280)
                else:
                    dock.move(dest)
            else:
                dock.move(dest)
                dock.raise_()
            return
        if not dock.isVisible():
            return
        if not animate:
            dock.hide()
            return

        def after():
            dock.hide()
            self._dock_anim = None

        self._dock_anim = self._anim_pos(dock, QPoint(dest.x(), dest.y() + 24), 200, after)

    def _anim_pos(self, widget, end, ms, done=None):
        anim = QPropertyAnimation(widget, b"pos", self)
        anim.setDuration(ms)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.setStartValue(widget.pos())
        anim.setEndValue(end)
        if done:
            anim.finished.connect(done)
        anim.start()
        return anim

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if getattr(self, "side", None) is None:
            return
        self._place_download_dock(animate=False)
        self._place_task_badge()

    def closeEvent(self, event):
        try:
            self.backend.terracotta_shutdown()
        except Exception:
            pass
        try:
            from mclauncher.feedback import stop_heartbeat
            stop_heartbeat(send_offline=True)
        except Exception:
            pass
        try:
            self.backend.shutdown()
        except Exception:
            pass
        super().closeEvent(event)

    def fly_to_tasks(self, source, text: str, color: str | None = None):
        if source is None:
            return
        if not self.backend.get_setting("ui_fly_animation", True):
            return
        duration = max(1, int(self.backend.get_setting("ui_fly_duration_ms", 620)))
        letter = (str(text or "").strip()[:1] or "↓").upper()
        side = getattr(self, "side", None)
        target = side.button("tasks") if side is not None else None
        if target is None:
            return
        fly_to(
            self, source, letter, color or pick_color(str(text or "")),
            target=target, duration=duration,
        )

    def queue_launch_after(self, task_id, instance: str, version: str, loader: str = tr("无")):
        if not task_id:
            return
        self._launch_after[task_id] = (instance, version, loader or tr("无"))

    def _launch_installed(self, instance: str, version: str, loader: str = tr("无")):
        last = getattr(self.backend, "_last_installed", None) or {}
        vid = version
        if last.get("instance") == instance and last.get("version"):
            vid = last["version"]
        self.switchTo(self.launch_page)
        self.launch_page.reload()
        if instance:
            self.launch_page.instance_box.setCurrentText(instance)
            self.launch_page.reload()
        box = self.launch_page.version_box
        ids = [box.itemText(i) for i in range(box.count())]
        pick = vid if vid in ids else next(
            (i for i in ids if vid and vid in i),
            next((i for i in ids if version and version in i and (
                loader in ("", tr("无")) or (loader or "").lower() in i.lower()
            )), ids[0] if ids else ""),
        )
        if pick:
            box.setCurrentText(pick)
        self.launch_page._on_launch()

    def _notify_task(self, task_id, success, message):
        pending = self._launch_after.pop(task_id, None)
        title = self.backend.task_title(task_id)
        if pending and success:
            instance, version, loader = pending
            InfoBar.success(tr("安装完成"), tr("正在启动游戏…"), parent=self,
                            position=InfoBarPosition.TOP_RIGHT, duration=2500)
            QTimer.singleShot(
                380, lambda i=instance, v=version, l=loader: self._launch_installed(i, v, l))
            return
        if str(title).startswith(tr("启动游戏")) or str(title).startswith(tr("微软登录")):
            return
        self._place_download_dock()
        if success:
            InfoBar.success(title, message, parent=self,
                            position=InfoBarPosition.TOP_RIGHT, duration=3000)
        elif message != tr("已取消"):
            InfoBar.error(title, message, parent=self,
                          position=InfoBarPosition.TOP_RIGHT, duration=5000)

    def _refresh_pages(self, force: bool = False):
        if force:
            self._data_dirty = False
        key = self._visible_key()
        if key is None:
            return
        page = self._pages.get(key) or self._built.get(key)
        if page is None:
            return
        if key == "launch":
            self.launch_page.reload()
            return
        if key == "mods":
            page.reload_list()
            return
        if key == "version":
            if hasattr(page, "reload_installed_only"):
                page.reload_installed_only()
            else:
                page.reload()
            return
        if key == "java":
            page.reload(scan_system=False)
            return
        if hasattr(page, "reload_installed"):
            page.reload_installed()
            return
        if hasattr(page, "reload"):
            try:
                page.reload()
            except TypeError:
                pass
