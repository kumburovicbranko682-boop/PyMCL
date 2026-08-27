# -*- coding: utf-8 -*-
"""设置页：WinUI 风格设置卡片组。"""

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel, CaptionLabel, ComboBox, FluentIcon as FIF, InfoBar, InfoBarPosition,
    LineEdit, PasswordLineEdit, PrimaryPushButton, PushButton, ScrollArea, SettingCard,
    SettingCardGroup, SpinBox, SubtitleLabel, SwitchButton,
)
from mclauncher.i18n import tr


def _spin_card(icon, title, desc, lo, hi, value):
    card = SettingCard(icon, title, desc)
    spin = SpinBox(card)
    spin.setRange(lo, hi)
    spin.setValue(value)
    spin.setFixedWidth(120)
    card.hBoxLayout.addWidget(spin, 0, Qt.AlignRight)
    card.hBoxLayout.addSpacing(16)
    return card, spin


def _switch_card(icon, title, desc, checked=False):
    card = SettingCard(icon, title, desc)
    switch = SwitchButton(card)
    switch.setChecked(checked)
    card.hBoxLayout.addWidget(switch, 0, Qt.AlignRight)
    card.hBoxLayout.addSpacing(16)
    return card, switch


def _combo_card(icon, title, desc, items, current):
    card = SettingCard(icon, title, desc)
    box = ComboBox(card)
    box.addItems(items)
    if current in items:
        box.setCurrentText(current)
    box.setFixedWidth(260)
    card.hBoxLayout.addWidget(box, 0, Qt.AlignRight)
    card.hBoxLayout.addSpacing(16)
    return card, box


def _line_card(icon, title, desc, password=False, placeholder=""):
    card = SettingCard(icon, title, desc)
    edit = PasswordLineEdit(card) if password else LineEdit(card)
    edit.setPlaceholderText(placeholder)
    edit.setFixedWidth(280)
    card.hBoxLayout.addWidget(edit, 0, Qt.AlignRight)
    card.hBoxLayout.addSpacing(16)
    return card, edit


class SettingsPage(QWidget):
    def __init__(self, backend, parent=None):
        super().__init__(parent)
        self.setObjectName("settingsPage")
        self.backend = backend
        settings = backend.get_settings()

        scroll = ScrollArea(self)
        scroll.setWidgetResizable(True)
        host = QWidget()
        root = QVBoxLayout(host)
        root.setContentsMargins(28, 20, 28, 20)
        root.setSpacing(16)
        scroll.setWidget(host)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        root.addWidget(SubtitleLabel(tr("设置")))

        iso_group = SettingCardGroup(tr("版本隔离与存储"), host)
        self.share_libs_card, self.share_libs = _switch_card(
            FIF.LIBRARY, tr("共享 libraries"),
            tr("所有实例共享依赖库（节省空间，但会降低隔离性）"),
            checked=settings["share_libraries"])
        self.share_assets_card, self.share_assets = _switch_card(
            FIF.PHOTO, tr("共享 assets 资源"),
            tr("所有实例共享资源文件（节省空间，但会降低隔离性）"),
            checked=settings["share_assets"])
        iso_group.addSettingCard(self.share_libs_card)
        iso_group.addSettingCard(self.share_assets_card)
        iso_map = {
            "none": tr("关闭（共用实例目录）"),
            "saves": tr("隔离存档"),
            "mods": tr("隔离 Mod 与配置"),
            "all": tr("隔离全部"),
        }
        self._iso_keys = {v: k for k, v in iso_map.items()}
        self.iso_card, self.iso_box = _combo_card(
            FIF.FOLDER, tr("新版本默认隔离"),
            tr("安装新版本时写入该版本的隔离模式，可稍后在版本设置改"),
            list(iso_map.values()),
            iso_map.get(settings.get("default_isolation") or "none", iso_map["none"]))
        iso_group.addSettingCard(self.iso_card)
        game_card = SettingCard(FIF.FOLDER, tr("游戏目录"), tr("实例与版本所在文件夹"))
        self.game_dir = LineEdit(game_card)
        self.game_dir.setText(settings.get("game_dir") or "")
        self.game_dir.setFixedWidth(220)
        browse = PushButton(tr("浏览"))
        browse.clicked.connect(self._browse_game)
        game_card.hBoxLayout.addWidget(self.game_dir, 0, Qt.AlignRight)
        game_card.hBoxLayout.addWidget(browse, 0, Qt.AlignRight)
        game_card.hBoxLayout.addSpacing(16)
        iso_group.addSettingCard(game_card)
        # 目录列表（HMCL 目录列表 / PCL2 文件夹列表）：记住多个游戏目录，一键切换
        self.dirs_card = SettingCard(
            FIF.FOLDER_ADD if hasattr(FIF, "FOLDER_ADD") else FIF.FOLDER,
            tr("目录列表"),
            tr("记住多个游戏目录并随时切换；移除只出列表、不删文件"))
        self._dir_entries = []
        self._dirs_updating = False
        self.dirs_box = ComboBox(self.dirs_card)
        self.dirs_box.setFixedWidth(260)
        self.dirs_box.currentIndexChanged.connect(self._switch_dir_entry)
        dir_add = PushButton(tr("添加"))
        dir_add.clicked.connect(self._add_game_dir_entry)
        dir_del = PushButton(tr("移除"))
        dir_del.clicked.connect(self._remove_game_dir_entry)
        self.dirs_card.hBoxLayout.addWidget(self.dirs_box, 0, Qt.AlignRight)
        self.dirs_card.hBoxLayout.addWidget(dir_add, 0, Qt.AlignRight)
        self.dirs_card.hBoxLayout.addWidget(dir_del, 0, Qt.AlignRight)
        self.dirs_card.hBoxLayout.addSpacing(16)
        iso_group.addSettingCard(self.dirs_card)
        self._reload_game_dirs()
        root.addWidget(iso_group)

        ui_group = SettingCardGroup(tr("界面"), host)
        self.motion_card, self.motion_sw = _switch_card(
            FIF.PLAY if hasattr(FIF, "PLAY") else FIF.SYNC,
            tr("界面动画"),
            tr("换页过渡、布局编辑、进度条、横幅微光等动效；关闭则全部瞬时"),
            checked=bool(settings.get("ui_motion", True)))
        ui_group.addSettingCard(self.motion_card)
        self.fly_card, self.fly_sw = _switch_card(
            FIF.SYNC,
            tr("下载飞入动画"),
            tr("点击安装时，图标抛物线飞入侧栏「下载任务」"),
            checked=bool(settings.get("ui_fly_animation", True)))
        ui_group.addSettingCard(self.fly_card)
        self.fly_dur_card, self.fly_dur_spin = _spin_card(
            FIF.HISTORY if hasattr(FIF, "HISTORY") else FIF.SYNC,
            tr("飞入动画时长"),
            tr("毫秒，建议 400–800；越小越快"),
            200, 1200,
            int(settings.get("ui_fly_duration_ms") or 620))
        ui_group.addSettingCard(self.fly_dur_card)
        self.dark_card, self.dark_sw = _switch_card(
            FIF.BRIGHTNESS, tr("深色模式"), tr("立即生效，接近 PCL 夜间主题"),
            checked=bool(settings.get("ui_dark")))
        self.color_card, self.color_edit = _line_card(
            FIF.PALETTE if hasattr(FIF, "PALETTE") else FIF.EDIT,
            tr("主题色"), tr("例如 #2E9B6B"))
        self.color_edit.setText(settings.get("theme_color") or "#2E9B6B")
        self.bg_card, self.bg_edit = _line_card(
            FIF.PHOTO, tr("背景图"), tr("本地图片路径，留空为纯色"))
        self.bg_edit.setText(settings.get("ui_background") or "")
        self.bg_pick = PushButton(tr("选择文件"))
        self.bg_pick.clicked.connect(self._browse_background)
        self.bg_card.hBoxLayout.addWidget(self.bg_pick, 0, Qt.AlignRight)
        # 界面字体（HMCL 设置「字体」同款）：默认 = Fluent 字族
        self._font_default_label = tr("默认字体")
        cur_font = str(settings.get("ui_font_family") or "").strip()
        font_items = [self._font_default_label]
        try:
            from PySide6.QtGui import QFontDatabase
            font_items += [f for f in QFontDatabase.families()
                           if f and not f.startswith(".")]
        except Exception:
            pass
        if cur_font and cur_font not in font_items:
            # 字体被卸载后仍显示当前配置值，用户能看到并改掉
            font_items.insert(1, cur_font)
        self.font_card, self.font_box = _combo_card(
            FIF.FONT if hasattr(FIF, "FONT") else FIF.EDIT,
            tr("界面字体"), tr("新窗口立即生效；已打开的页面重启后全部生效"),
            font_items, cur_font or self._font_default_label)
        vis_map = {
            "keep": tr("保持显示"),
            "minimize": tr("最小化"),
            "hide": tr("隐藏"),
            "hide_reopen": tr("隐藏，退出后重开"),
            "close": tr("关闭启动器"),
        }
        self._vis_keys = {v: k for k, v in vis_map.items()}
        self.vis_card, self.vis_box = _combo_card(
            FIF.VIEW, tr("启动器可见性"),
            tr("游戏启动后启动器窗口怎么处理。关闭不会杀掉游戏进程。"),
            list(vis_map.values()),
            vis_map.get(settings.get("launcher_visibility") or "keep", vis_map["keep"]))
        # HMCL「显示日志」同款：启动游戏时自动弹实时日志窗口
        self.show_log_card, self.show_log_sw = _switch_card(
            FIF.DOCUMENT, tr("启动时显示游戏日志"),
            tr("启动游戏后自动弹出实时日志窗口（级别高亮 / 搜索 / 导出）。版本设置可覆盖。"),
            checked=bool(settings.get("show_log_window", False)))
        home_map = {"news": tr("Minecraft 新闻"), "custom": tr("本地 HTML"), "blank": tr("空白")}
        self._home_keys = {v: k for k, v in home_map.items()}
        self.home_card, self.home_box = _combo_card(
            FIF.HOME if hasattr(FIF, "HOME") else FIF.VIEW, tr("启动页主页"), tr("右侧栏显示新闻、自定义 HTML 或留空"),
            list(home_map.values()),
            home_map.get(settings.get("homepage_mode") or "news", home_map["news"]))
        self.hp_card, self.hp_edit = _line_card(
            FIF.DOCUMENT if hasattr(FIF, "DOCUMENT") else FIF.EDIT, tr("自定义主页"), tr("本地 .html 文件路径"))
        self.hp_edit.setText(settings.get("custom_homepage") or "")
        win_map = {"window": tr("窗口"), "maximize": tr("全屏")}
        self._win_keys = {v: k for k, v in win_map.items()}
        self.win_card, self.win_box = _combo_card(
            FIF.FULL_SCREEN if hasattr(FIF, "FULL_SCREEN") else FIF.VIEW,
            tr("默认游戏窗口"), tr("可被版本设置覆盖"),
            list(win_map.values()),
            win_map.get(settings.get("window_mode") or "window", win_map["window"]))
        glang_map = {
            "auto": tr("跟随启动器语言"),
            "zh_cn": tr("简体中文"),
            "en_us": "English",
            "off": tr("不设置"),
        }
        self._glang_keys = {v: k for k, v in glang_map.items()}
        self.glang_card, self.glang_box = _combo_card(
            FIF.LANGUAGE if hasattr(FIF, "LANGUAGE") else FIF.EDIT,
            tr("游戏语言"),
            tr("和 PCL 一样：版本首次启动时自动写入 options.txt，进游戏就是中文。已改过语言的存档不受影响"),
            list(glang_map.values()),
            glang_map.get(settings.get("game_lang") or "auto", glang_map["auto"]))
        ui_group.addSettingCard(self.dark_card)
        ui_group.addSettingCard(self.color_card)
        ui_group.addSettingCard(self.bg_card)
        ui_group.addSettingCard(self.font_card)
        ui_group.addSettingCard(self.vis_card)
        ui_group.addSettingCard(self.show_log_card)
        ui_group.addSettingCard(self.home_card)
        ui_group.addSettingCard(self.hp_card)
        ui_group.addSettingCard(self.win_card)
        ui_group.addSettingCard(self.glang_card)
        # 语言
        self.lang_card, self.lang_box = _combo_card(
            FIF.EDIT if hasattr(FIF, "EDIT") else FIF.SETTING,
            tr("语言"), tr("切换界面语言，改后重启生效"),
            list(self.backend.available_languages().values()),
            self.backend.available_languages().get(self.backend.get_language(), tr("简体中文")))
        ui_group.addSettingCard(self.lang_card)
        # 主题包
        theme_card = SettingCard(
            FIF.PALETTE if hasattr(FIF, "PALETTE") else FIF.EDIT,
            tr("主题包"), tr("保存/加载当前主题配色"))
        self.save_theme_btn = PushButton(tr("保存当前主题"))
        self.load_theme_btn = PushButton(tr("加载主题"))
        self.del_theme_btn = PushButton(tr("删除主题"))
        for b in (self.save_theme_btn, self.load_theme_btn, self.del_theme_btn):
            theme_card.hBoxLayout.addWidget(b, 0, Qt.AlignRight)
        theme_card.hBoxLayout.addSpacing(8)
        ui_group.addSettingCard(theme_card)
        # 启动器背景音乐（PCL2 音乐播放器同款）
        music_card = SettingCard(
            FIF.MUSIC if hasattr(FIF, "MUSIC") else FIF.PLAY,
            tr("启动器背景音乐"),
            tr("把音频文件放进 music 文件夹，启动器随机循环播放"))
        self.music_open_btn = PushButton(tr("打开音乐文件夹"))
        self.music_next_btn = PushButton(tr("下一曲"))
        self.music_vol = SpinBox(music_card)
        self.music_vol.setRange(0, 100)
        self.music_vol.setValue(int(settings.get("music_volume") or 50))
        self.music_vol.setFixedWidth(110)
        self.music_sw = SwitchButton(music_card)
        self.music_sw.setChecked(bool(settings.get("music_enabled")))
        for w in (self.music_open_btn, self.music_next_btn, self.music_vol, self.music_sw):
            music_card.hBoxLayout.addWidget(w, 0, Qt.AlignRight)
        music_card.hBoxLayout.addSpacing(16)
        ui_group.addSettingCard(music_card)
        root.addWidget(ui_group)

        # ---- 个性化布局：自由画布 + 方案 + 侧栏 ----
        from . import layout_settings as lset
        self._lset = lset
        layout_group = SettingCardGroup(tr("个性化布局"), host)

        edit_card = SettingCard(
            FIF.EDIT if hasattr(FIF, "EDIT") else FIF.SETTING,
            tr("启动页布局"), tr("卡片随意拖动 / 缩放 / 增删，自由摆放"))
        self.layout_edit_btn = PushButton(tr("进入编辑"))
        self.layout_edit_btn.clicked.connect(self._edit_home_layout)
        edit_card.hBoxLayout.addWidget(self.layout_edit_btn, 0, Qt.AlignRight)
        edit_card.hBoxLayout.addSpacing(16)
        layout_group.addSettingCard(edit_card)

        labels, names = lset.profile_labels()
        self._profile_names = names
        self.profile_card, self.profile_box = _combo_card(
            FIF.TILES if hasattr(FIF, "TILES") else FIF.LIBRARY,
            tr("布局方案"), tr("切换整套布局；编辑后自动记住"),
            labels, names[0])
        cur_profile = lset.layout_model.active_profile()
        if cur_profile in names:
            self.profile_box.setCurrentIndex(names.index(cur_profile))
        self.profile_box.currentIndexChanged.connect(self._on_profile_changed)
        self.profile_save_btn = PushButton(tr("另存为…"))
        self.profile_save_btn.clicked.connect(self._save_profile_as)
        self.profile_del_btn = PushButton(tr("删除"))
        self.profile_del_btn.clicked.connect(self._delete_profile)
        self.profile_reset_btn = PushButton(tr("重置默认"))
        self.profile_reset_btn.clicked.connect(self._reset_home_layout)
        for b in (self.profile_save_btn, self.profile_del_btn, self.profile_reset_btn):
            self.profile_card.hBoxLayout.addWidget(b, 0, Qt.AlignRight)
        layout_group.addSettingCard(self.profile_card)

        io_card = SettingCard(
            FIF.SYNC, tr("布局导入/导出"),
            tr("把布局存成 JSON 文件，换机或分享给朋友"))
        self.layout_export_btn = PushButton(tr("导出…"))
        self.layout_export_btn.clicked.connect(self._export_home_layout)
        self.layout_import_btn = PushButton(tr("导入…"))
        self.layout_import_btn.clicked.connect(self._import_home_layout)
        io_card.hBoxLayout.addWidget(self.layout_export_btn, 0, Qt.AlignRight)
        io_card.hBoxLayout.addWidget(self.layout_import_btn, 0, Qt.AlignRight)
        io_card.hBoxLayout.addSpacing(16)
        layout_group.addSettingCard(io_card)

        side_card = SettingCard(
            FIF.MENU if hasattr(FIF, "MENU") else FIF.SETTING,
            tr("侧栏自定义"), tr("导航项排序 / 显示隐藏 / 侧栏宽度"))
        self.sidebar_btn = PushButton(tr("自定义侧栏…"))
        self.sidebar_btn.clicked.connect(self._open_sidebar_editor)
        side_card.hBoxLayout.addWidget(self.sidebar_btn, 0, Qt.AlignRight)
        side_card.hBoxLayout.addSpacing(16)
        layout_group.addSettingCard(side_card)

        section_card = SettingCard(
            FIF.TILES if hasattr(FIF, "TILES") else FIF.LIBRARY,
            tr("分区内容"), tr("子页在「下载」和「更多」之间移动、排序"))
        self.section_btn = PushButton(tr("自定义分区…"))
        self.section_btn.clicked.connect(self._open_section_editor)
        section_card.hBoxLayout.addWidget(self.section_btn, 0, Qt.AlignRight)
        section_card.hBoxLayout.addSpacing(16)
        layout_group.addSettingCard(section_card)
        root.addWidget(layout_group)

        perf_group = SettingCardGroup(tr("下载与性能"), host)
        self.threads_card, self.threads_spin = _spin_card(
            FIF.SYNC, tr("下载并发线程数"), tr("同时下载的文件数量"),
            1, 64, settings["download_threads"])
        self.memory_card, self.memory_spin = _spin_card(
            FIF.DEVELOPER_TOOLS, tr("默认内存 (MB)"), tr("新实例的默认 JVM 内存"),
            512, 32768, settings["default_memory_mb"])
        self.auto_mem_card, self.auto_mem_sw = _switch_card(
            FIF.ROBOT if hasattr(FIF, "ROBOT") else FIF.DEVELOPER_TOOLS, tr("自动分配内存"),
            tr("和 PCL 一样：启动时按可用物理内存实时计算，低配不爆内存、高配吃得满。版本设置里的内存仍然优先"),
            checked=bool(settings.get("auto_memory", False)))
        gc_map = {
            "auto": tr("G1（推荐）"),
            "g1": "G1",
            "g1_tuned": tr("调优 G1"),
            "zgc": "ZGC",
            "none": tr("不指定"),
        }
        self._gc_keys = {v: k for k, v in gc_map.items()}
        self.gc_card, self.gc_box = _combo_card(
            FIF.SPEED_HIGH if hasattr(FIF, "SPEED_HIGH") else FIF.DEVELOPER_TOOLS, tr("内存回收器"), tr("启动时写入 JVM。版本设置可覆盖。"),
            list(gc_map.values()),
            gc_map.get(settings.get("gc_preset") or "auto", gc_map["auto"]))
        # 显卡偏好（PCL2「尝试使用独立显卡」/ HMCL PRIME offload 同款）
        gpu_map = {
            "auto": tr("自动（系统默认）"),
            "discrete": tr("强制独立显卡（高性能）"),
            "integrated": tr("强制核芯显卡（省电）"),
        }
        self._gpu_keys = {v: k for k, v in gpu_map.items()}
        self.gpu_card, self.gpu_box = _combo_card(
            FIF.GAME if hasattr(FIF, "GAME") else FIF.DEVELOPER_TOOLS, tr("游戏显卡"),
            tr("双显卡设备可强制游戏走独显。Windows 写注册表偏好；Linux 注入 PRIME offload。版本设置可覆盖。"),
            list(gpu_map.values()),
            gpu_map.get(settings.get("gpu_mode") or "auto", gpu_map["auto"]))
        # 渲染器（HMCL 同款，仅 Linux/Mesa 生效）
        rnd_map = {
            "auto": tr("默认（硬件 OpenGL）"),
            "llvmpipe": tr("LLVMpipe（CPU 软渲染）"),
            "zink": tr("Zink（OpenGL over Vulkan）"),
        }
        self._rnd_keys = {v: k for k, v in rnd_map.items()}
        self.rnd_card, self.rnd_box = _combo_card(
            FIF.BRUSH if hasattr(FIF, "BRUSH") else FIF.DEVELOPER_TOOLS, tr("渲染器"),
            tr("驱动异常时可换 LLVMpipe 软渲染或 Zink。仅 Linux（Mesa）生效。版本设置可覆盖。"),
            list(rnd_map.values()),
            rnd_map.get(settings.get("renderer") or "auto", rnd_map["auto"]))
        self.limit_card, self.limit_spin = _spin_card(
            FIF.CLOUD_DOWNLOAD, tr("下载限速 (KB/s)"), tr("0 表示不限制"),
            0, 102400, int(settings.get("download_limit_kbps") or 0))
        src_map = {"auto": tr("自动（官方>4秒改 BMCLAPI）"), "official": tr("仅官方"), "bmclapi": tr("仅 BMCLAPI")}
        comm_map = {"auto": tr("自动"), "official": tr("仅官方"), "mcim": tr("仅 MCIM")}
        self._src_keys = {v: k for k, v in src_map.items()}
        self._comm_keys = {v: k for k, v in comm_map.items()}
        self.src_card, self.src_box = _combo_card(
            FIF.CLOUD_DOWNLOAD, tr("文件下载源"),
            tr("和 PCL 一样：自动测速，官方慢于 4 秒就改走 BMCLAPI"),
            list(src_map.values()), src_map.get(settings.get("download_source") or "auto", src_map["auto"]))
        self.comm_card, self.comm_box = _combo_card(
            FIF.LIBRARY, tr("社区资源源"),
            tr("模组 / 整合包：自动 = 官方优先，失败再走 MCIM 国内镜像"),
            list(comm_map.values()), comm_map.get(settings.get("community_source") or "auto", comm_map["auto"]))
        # 代理（HMCL 设置同款）：系统 / 直连 / 自定义 HTTP / SOCKS5
        proxy_map = {
            "system": tr("跟随系统代理"),
            "direct": tr("直连（禁用代理）"),
            "http": tr("HTTP 代理"),
            "socks5": tr("SOCKS5 代理"),
        }
        self._proxy_keys = {v: k for k, v in proxy_map.items()}
        cur_proxy = settings.get("proxy_mode") or (
            "system" if settings.get("use_system_proxy", True) else "direct")
        self.proxy_card, self.proxy_box = _combo_card(
            FIF.VPN, tr("代理"),
            tr("跟随系统时 Clash 7897 会生效；也可强制直连或自定义代理服务器"),
            list(proxy_map.values()), proxy_map.get(cur_proxy, proxy_map["system"]))
        self.proxy_addr_card = SettingCard(
            FIF.GLOBE if hasattr(FIF, "GLOBE") else FIF.VPN,
            tr("代理服务器"), tr("主机与端口"))
        self.proxy_host_edit = LineEdit(self.proxy_addr_card)
        self.proxy_host_edit.setPlaceholderText("127.0.0.1")
        self.proxy_host_edit.setFixedWidth(200)
        self.proxy_host_edit.setText(settings.get("proxy_host") or "")
        self.proxy_port_spin = SpinBox(self.proxy_addr_card)
        self.proxy_port_spin.setRange(0, 65535)
        self.proxy_port_spin.setValue(int(settings.get("proxy_port") or 0))
        self.proxy_port_spin.setFixedWidth(130)
        self.proxy_test_btn = PushButton(tr("测试代理"))
        for w in (self.proxy_test_btn, self.proxy_host_edit, self.proxy_port_spin):
            self.proxy_addr_card.hBoxLayout.addWidget(w, 0, Qt.AlignRight)
        self.proxy_addr_card.hBoxLayout.addSpacing(16)
        self.proxy_auth_card = SettingCard(
            FIF.PEOPLE if hasattr(FIF, "PEOPLE") else FIF.VPN,
            tr("代理认证"), tr("可选。代理不要账号密码就留空"))
        self.proxy_user_edit = LineEdit(self.proxy_auth_card)
        self.proxy_user_edit.setPlaceholderText(tr("用户名"))
        self.proxy_user_edit.setFixedWidth(160)
        self.proxy_user_edit.setText(settings.get("proxy_user") or "")
        self.proxy_pass_edit = PasswordLineEdit(self.proxy_auth_card)
        self.proxy_pass_edit.setFixedWidth(180)
        self.proxy_pass_edit.setText(settings.get("proxy_pass") or "")
        for w in (self.proxy_user_edit, self.proxy_pass_edit):
            self.proxy_auth_card.hBoxLayout.addWidget(w, 0, Qt.AlignRight)
        self.proxy_auth_card.hBoxLayout.addSpacing(16)
        perf_group.addSettingCard(self.threads_card)
        perf_group.addSettingCard(self.src_card)
        perf_group.addSettingCard(self.comm_card)
        perf_group.addSettingCard(self.proxy_card)
        perf_group.addSettingCard(self.proxy_addr_card)
        perf_group.addSettingCard(self.proxy_auth_card)
        self._sync_proxy_mode()
        perf_group.addSettingCard(self.memory_card)
        perf_group.addSettingCard(self.auto_mem_card)
        perf_group.addSettingCard(self.gc_card)
        perf_group.addSettingCard(self.gpu_card)
        perf_group.addSettingCard(self.rnd_card)
        perf_group.addSettingCard(self.limit_card)

        self.jvm_card, self.jvm_edit = _line_card(
            FIF.DEVELOPER_TOOLS, tr("默认 JVM 参数"), tr("所有版本都会带上，版本设置可再追加"))
        self.jvm_edit.setText(settings.get("default_jvm_args") or "")
        perf_group.addSettingCard(self.jvm_card)

        res_card = SettingCard(FIF.VIEW, tr("默认分辨率"), tr("游戏窗口的默认宽高"))
        res_row = QHBoxLayout()
        self.width_spin = SpinBox(res_card)
        self.width_spin.setRange(320, 7680)
        self.width_spin.setValue(settings["default_resolution"][0])
        self.height_spin = SpinBox(res_card)
        self.height_spin.setRange(240, 4320)
        self.height_spin.setValue(settings["default_resolution"][1])
        res_row.addWidget(self.width_spin)
        res_row.addWidget(BodyLabel("×"))
        res_row.addWidget(self.height_spin)
        res_card.hBoxLayout.addLayout(res_row)
        res_card.hBoxLayout.addSpacing(16)
        perf_group.addSettingCard(res_card)
        root.addWidget(perf_group)

        acc_group = SettingCardGroup(tr("账号与下载源"), host)
        self.ms_card, self.ms_client_edit = _line_card(
            FIF.PEOPLE, tr("微软 OAuth 客户端 ID"), tr("一般无需修改"))
        self.ms_client_edit.setText(settings["ms_client_id"])
        self.curse_card, self.curse_key_edit = _line_card(
            FIF.VPN, tr("CurseForge API 密钥"),
            tr("可选；仅在国内镜像不可用时用于搜索兜底"), password=True)
        self.curse_key_edit.setText(settings["curseforge_api_key"])
        acc_group.addSettingCard(self.ms_card)
        acc_group.addSettingCard(self.curse_card)
        root.addWidget(acc_group)

        maint_group = SettingCardGroup(tr("维护"), host)
        self.upd_card, self.upd_url = _line_card(
            FIF.UPDATE if hasattr(FIF, "UPDATE") else FIF.SYNC,
            tr("更新清单 URL"), "JSON：version / url / notes")
        self.upd_url.setText(settings.get("update_url") or "")
        maint_group.addSettingCard(self.upd_card)
        self.auto_upd_card, self.auto_upd = _switch_card(
            FIF.SYNC, tr("启动时检查更新"), tr("打开启动器后在后台检查自更新清单"),
            checked=bool(settings.get("auto_check_update", True)))
        maint_group.addSettingCard(self.auto_upd_card)
        # 多开
        self.multi_card, self.multi_sw = _switch_card(
            FIF.PLAY if hasattr(FIF, "PLAY") else FIF.SYNC,
            tr("允许多开"), tr("取消勾选 = 游戏运行时再次启动会提示"),
            checked=bool(settings.get("allow_multi_instance", False)))
        maint_group.addSettingCard(self.multi_card)
        # 游戏目录迁移：官方自动检测 + 任意目录（PCL / HMCL / 旧电脑拷来的 .minecraft）
        mig_card = SettingCard(
            FIF.DOWNLOAD if hasattr(FIF, "DOWNLOAD") else FIF.SYNC,
            tr("游戏目录迁移"), tr("从官方启动器 / PCL / HMCL 的 .minecraft 导入已装版本"))
        self.mig_btn = PushButton(tr("检测并迁移"))
        self.mig_pick_btn = PushButton(tr("选目录导入…"))
        mig_card.hBoxLayout.addWidget(self.mig_btn, 0, Qt.AlignRight)
        mig_card.hBoxLayout.addWidget(self.mig_pick_btn, 0, Qt.AlignRight)
        mig_card.hBoxLayout.addSpacing(8)
        maint_group.addSettingCard(mig_card)
        # 智能推荐
        rec_card = SettingCard(
            FIF.DEVELOPER_TOOLS, tr("智能推荐"), tr("根据硬件自动推荐内存和 Java 设置"))
        self.rec_btn = PushButton(tr("查看推荐"))
        rec_card.hBoxLayout.addWidget(self.rec_btn, 0, Qt.AlignRight)
        rec_card.hBoxLayout.addSpacing(8)
        maint_group.addSettingCard(rec_card)
        tool_card = SettingCard(FIF.DEVELOPER_TOOLS, tr("维护工具"), tr("更新、清理、导出、全局 Mod"))
        self.chk_upd = PrimaryPushButton(tr("检查更新"))
        self.clean_btn = PushButton(tr("清理"))
        self.export_btn = PushButton(tr("导出实例"))
        self.global_btn = PushButton(tr("全局 Mod"))
        for b in (self.chk_upd, self.clean_btn, self.export_btn, self.global_btn):
            tool_card.hBoxLayout.addWidget(b, 0, Qt.AlignRight)
        tool_card.hBoxLayout.addSpacing(8)
        maint_group.addSettingCard(tool_card)
        root.addWidget(maint_group)
        self.chk_upd.clicked.connect(self._check_update)
        self.clean_btn.clicked.connect(self._clean)
        self.export_btn.clicked.connect(self._export)
        self.global_btn.clicked.connect(self._global_mods)
        self.save_theme_btn.clicked.connect(self._save_theme)
        self.load_theme_btn.clicked.connect(self._load_theme)
        self.del_theme_btn.clicked.connect(self._del_theme)
        self.mig_btn.clicked.connect(self._migrate_official)
        self.mig_pick_btn.clicked.connect(self._migrate_from_dir)
        self.rec_btn.clicked.connect(self._show_recommendation)

        ai_group = SettingCardGroup(tr("AI 助手"), host)
        mode_card = SettingCard(getattr(FIF, "CHAT", None) or FIF.HELP, tr("接入方式"), tr("公益接口走网关，不在本机存密钥"))
        self.ai_mode = ComboBox(mode_card)
        self.ai_mode.addItems([tr("公益接口"), tr("自定义 NewAPI")])
        self.ai_mode.setCurrentText(
            tr("自定义 NewAPI") if settings.get("ai_mode") == "custom" else tr("公益接口"))
        self.ai_mode.setFixedWidth(180)
        mode_card.hBoxLayout.addWidget(self.ai_mode, 0, Qt.AlignRight)
        mode_card.hBoxLayout.addSpacing(16)
        self.gw_card, self.ai_gateway = _line_card(
            FIF.CLOUD_DOWNLOAD, tr("公益网关地址"),
            tr("HTTPS 地址；发行版已内置，自建填自己的（不带 /v1）"))
        self.ai_gateway.setText(settings.get("ai_gateway_url") or "")
        self.base_card, self.ai_base = _line_card(
            FIF.VIEW, "NewAPI Base URL", tr("自定义模式：填到 /v1 为止"))
        self.ai_base.setText(settings.get("ai_base_url") or "")
        self.key_card, self.ai_key = _line_card(
            FIF.VPN, tr("NewAPI 令牌"), tr("只在自定义模式使用，不要用站长无限额令牌"), password=True)
        self.ai_key.setText(settings.get("ai_api_key") or "")
        self.model_card, self.ai_model = _line_card(
            FIF.EDIT, tr("模型名"), tr("公益模式在网关白名单内可切换，不在名单会回落默认"))
        self.ai_model.setText(settings.get("ai_model") or "deepseek-v4-flash")
        ai_group.addSettingCard(mode_card)
        ai_group.addSettingCard(self.gw_card)
        ai_group.addSettingCard(self.base_card)
        ai_group.addSettingCard(self.key_card)
        ai_group.addSettingCard(self.model_card)
        root.addWidget(ai_group)
        self.ai_mode.currentTextChanged.connect(self._sync_ai_mode)
        self._sync_ai_mode()

        fb_group = SettingCardGroup(tr("反馈与诊断"), host)
        self.fb_consent_card, self.fb_consent = _switch_card(
            FIF.VPN, tr("允许上传诊断数据"),
            tr("第一次打开会询问。未同意时不会上传反馈和电脑配置"),
            checked=bool(settings.get("feedback_consent")))
        self.fb_url_card, self.fb_url = _line_card(
            FIF.CLOUD_DOWNLOAD, tr("反馈上报地址"), tr("指向上报口，不要填看板端口"))
        self.fb_url.setText(settings.get("feedback_url") or "")
        self.fb_hb_card, self.fb_hb = _switch_card(
            FIF.SYNC, tr("定时上报本机配置"),
            tr("同意上传后，启动器打开时把电脑配置发到上报口"),
            checked=bool(settings.get("feedback_heartbeat", True)))
        fb_group.addSettingCard(self.fb_consent_card)
        fb_group.addSettingCard(self.fb_url_card)
        fb_group.addSettingCard(self.fb_hb_card)
        self.log_card = SettingCard(
            FIF.DOCUMENT, tr("启动器日志"),
            tr("每次运行一个文件，保留最近 5 次；崩溃日志也在这里"))
        self.open_log_btn = PushButton(tr("打开日志文件夹"))
        self.log_card.hBoxLayout.addWidget(self.open_log_btn, 0, Qt.AlignRight)
        self.log_card.hBoxLayout.addSpacing(16)
        fb_group.addSettingCard(self.log_card)
        root.addWidget(fb_group)

        row = QHBoxLayout()
        self.save_btn = PrimaryPushButton(FIF.SAVE, tr("保存设置"))
        self.save_btn.setFixedHeight(36)
        self.test_ai_btn = PushButton(FIF.SYNC, tr("测试 AI 连接"))
        self.test_ai_btn.setFixedHeight(36)
        row.addWidget(self.save_btn)
        row.addWidget(self.test_ai_btn)
        row.addStretch(1)
        root.addLayout(row)
        root.addWidget(CaptionLabel(tr("启动器主目录: {0}").format(settings.get('root', ''))))
        root.addStretch(1)

        self.save_btn.clicked.connect(self._save)
        self.test_ai_btn.clicked.connect(self._test_ai)
        # 文案写「立即生效」，开关本身必须落盘并刷主题；不能只等点「保存设置」
        self.dark_sw.checkedChanged.connect(self._on_dark_toggled)
        self.color_edit.editingFinished.connect(self._on_theme_color_committed)
        # 背景图同理：手输路径回车/失焦就应用，不必先点「保存设置」
        self.bg_edit.editingFinished.connect(self._on_bg_committed)
        # 字体选完就落盘并应用到 Fluent 字族；已建控件等重启
        self.font_box.currentTextChanged.connect(self._on_font_changed)
        # 背景音乐：开关立即播/停；音量拖动即时生效、失焦才落盘
        self.music_sw.checkedChanged.connect(self._on_music_toggled)
        self.music_vol.valueChanged.connect(self._on_music_volume_live)
        self.music_vol.editingFinished.connect(self._on_music_volume_commit)
        self.music_next_btn.clicked.connect(self._music_next)
        self.music_open_btn.clicked.connect(self._open_music_folder)
        # 代理：切模式即时显隐地址/认证卡；测试按钮先落盘再试连
        self.proxy_box.currentTextChanged.connect(self._sync_proxy_mode)
        self.proxy_test_btn.clicked.connect(self._test_proxy)
        self.open_log_btn.clicked.connect(self._open_launcher_logs)

    def refresh_from_config(self):
        """把磁盘上的最新设置推回控件。

        故意不叫 reload()：MainWindow._reload_page 会在每次切页时调用 reload()，
        那样会把用户还没保存的编辑悄悄丢掉。只在确实从外部改了配置时手动调用。
        """
        settings = self.backend.get_settings()
        self.dark_sw.setChecked(bool(settings.get("ui_dark")))
        self.color_edit.setText(settings.get("theme_color") or "#2E9B6B")
        self.bg_edit.setText(settings.get("ui_background") or "")
        cur_font = str(settings.get("ui_font_family") or "").strip()
        # blockSignals：回填不算用户改动，别再触发一次落盘+应用
        self.font_box.blockSignals(True)
        if cur_font and self.font_box.findText(cur_font) < 0:
            self.font_box.insertItem(1, cur_font)
        self.font_box.setCurrentText(cur_font or self._font_default_label)
        self.font_box.blockSignals(False)
        self.multi_sw.setChecked(bool(settings.get("allow_multi_instance", False)))

    def _open_launcher_logs(self):
        try:
            self.backend.open_launcher_logs()
        except Exception as e:
            InfoBar.error(tr("无法打开"), str(e), parent=self,
                          position=InfoBarPosition.TOP, duration=4000)

    def _sync_proxy_mode(self, _text=""):
        mode = self._proxy_keys.get(self.proxy_box.currentText(), "system")
        custom = mode in ("http", "socks5")
        self.proxy_addr_card.setVisible(custom)
        self.proxy_auth_card.setVisible(custom)

    def _collect_proxy(self) -> dict:
        mode = self._proxy_keys.get(self.proxy_box.currentText(), "system")
        return {
            "proxy_mode": mode,
            # 旧开关跟着走：system=跟随系统，其余都不读环境变量
            "use_system_proxy": mode == "system",
            "proxy_host": self.proxy_host_edit.text().strip(),
            "proxy_port": int(self.proxy_port_spin.value()),
            "proxy_user": self.proxy_user_edit.text().strip(),
            "proxy_pass": self.proxy_pass_edit.text(),
        }

    def _test_proxy(self):
        # 先把当前代理输入落盘并应用策略，测试的才是用户眼前的配置
        self.backend.save_settings(self._collect_proxy())
        self.proxy_test_btn.setEnabled(False)
        self.proxy_test_btn.setText(tr("测试中…"))

        def done(result):
            self.proxy_test_btn.setEnabled(True)
            self.proxy_test_btn.setText(tr("测试代理"))
            result = result or {}
            if result.get("ok"):
                InfoBar.success(tr("代理可用"),
                                tr("连通，延迟 {ms} 毫秒").format(ms=result.get("latency_ms", "?")),
                                parent=self, position=InfoBarPosition.TOP, duration=3500)
            else:
                InfoBar.error(tr("代理不可用"), str(result.get("message") or ""),
                              parent=self, position=InfoBarPosition.TOP, duration=5000)

        def failed(exc):
            self.proxy_test_btn.setEnabled(True)
            self.proxy_test_btn.setText(tr("测试代理"))
            InfoBar.error(tr("代理不可用"), str(exc), parent=self,
                          position=InfoBarPosition.TOP, duration=5000)

        self.backend.call_async(self.backend.test_proxy, done, failed)

    def _sync_ai_mode(self, _text=""):
        custom = self.ai_mode.currentText() == tr("自定义 NewAPI")
        self.gw_card.setVisible(not custom)
        self.base_card.setVisible(custom)
        self.key_card.setVisible(custom)
        # 模型名两种模式都能改：公益模式由网关白名单把关
        self.model_card.setVisible(True)

    def _apply_theme_now(self):
        win = self.window()
        if hasattr(win, "apply_theme"):
            win.apply_theme()
        elif hasattr(self.backend, "theme_changed"):
            self.backend.theme_changed.emit()

    def _on_dark_toggled(self, checked: bool):
        # 单独写 ui_dark，走 update 局部语义，不把整页未保存草稿一并落盘
        self.backend.save_settings({"ui_dark": bool(checked)})
        self._apply_theme_now()

    def _on_theme_color_committed(self):
        color = (self.color_edit.text() or "").strip() or "#2E9B6B"
        if not color.startswith("#"):
            color = "#" + color
        self.backend.save_settings({"theme_color": color})
        self._apply_theme_now()

    def _browse_background(self):
        cur = self.bg_edit.text().strip()
        start = cur if cur and os.path.isfile(cur) else ""
        path, _ = QFileDialog.getOpenFileName(
            self, tr("选择背景图"), start,
            tr("图片 (*.png *.jpg *.jpeg *.bmp *.webp *.gif)") + ";;" + tr("所有文件 (*)"))
        if not path:
            return
        self.bg_edit.setText(path)
        # 选完立即落盘并刷主题，马上能看到效果；之后点「保存设置」写的也是同一个值
        self.backend.save_settings({"ui_background": path})
        self._apply_theme_now()
        InfoBar.success(tr("已应用"), tr("背景已更新"), parent=self,
                        position=InfoBarPosition.TOP, duration=2000)

    def _on_bg_committed(self):
        path = self.bg_edit.text().strip()
        if path == (self.backend.get_setting("ui_background") or ""):
            return
        self.backend.save_settings({"ui_background": path})
        self._apply_theme_now()

    def _on_font_changed(self, text: str):
        family = "" if (text or "") == self._font_default_label else (text or "").strip()
        if family == (self.backend.get_setting("ui_font_family") or ""):
            return
        self.backend.save_settings({"ui_font_family": family})
        from ..pcl_chrome import apply_ui_font
        apply_ui_font()
        InfoBar.success(tr("已应用"), tr("新窗口立即生效；已打开的页面重启后全部生效"),
                        parent=self, position=InfoBarPosition.TOP, duration=3000)

    # ------------------------------------------------------------------
    # 启动器背景音乐（PCL2 音乐播放器同款）
    # ------------------------------------------------------------------
    def _music_player(self):
        win = self.window()
        return win.music_player() if hasattr(win, "music_player") else None

    def _on_music_toggled(self, checked: bool):
        self.backend.save_settings({"music_enabled": bool(checked)})
        player = self._music_player()
        if player is None:
            return
        if checked:
            if not player.start():
                InfoBar.info(tr("提示"), tr("music 文件夹里还没有音频文件"),
                             parent=self, position=InfoBarPosition.TOP, duration=3000)
        else:
            player.stop()

    def _on_music_volume_live(self, value: int):
        # 拖动即时改音量；落盘等 editingFinished，避免每格都触发一次保存
        player = self._music_player()
        if player is not None:
            player.set_volume(int(value))

    def _on_music_volume_commit(self):
        value = int(self.music_vol.value())
        if value == int(self.backend.get_setting("music_volume", 50) or 0):
            return
        self.backend.save_settings({"music_volume": value})

    def _music_next(self):
        player = self._music_player()
        if player is None:
            return
        name = player.next_track()
        if name:
            InfoBar.success(tr("正在播放"), name, parent=self,
                            position=InfoBarPosition.TOP, duration=3000)
        else:
            InfoBar.info(tr("提示"), tr("music 文件夹里还没有音频文件"),
                         parent=self, position=InfoBarPosition.TOP, duration=3000)

    def _open_music_folder(self):
        try:
            self.backend.open_music_folder()
        except Exception as e:
            InfoBar.error(tr("无法打开"), str(e), parent=self,
                          position=InfoBarPosition.TOP, duration=4000)

    # ------------------------------------------------------------------
    # 个性化布局
    # ------------------------------------------------------------------
    def _win(self):
        win = self.window()
        return win if hasattr(win, "launch_page") else None

    def _edit_home_layout(self):
        win = self._win()
        if win is None:
            return
        win.switchTo("launch")
        win.launch_page.enter_edit_mode()

    def _refresh_profiles(self):
        labels, names = self._lset.profile_labels()
        self._profile_names = names
        self.profile_box.blockSignals(True)
        self.profile_box.clear()
        self.profile_box.addItems(labels)
        cur = self._lset.layout_model.active_profile()
        if cur in names:
            self.profile_box.setCurrentIndex(names.index(cur))
        self.profile_box.blockSignals(False)

    def _on_profile_changed(self):
        idx = self.profile_box.currentIndex()
        names = getattr(self, "_profile_names", None) or []
        if idx < 0 or idx >= len(names):
            return
        win = self._win()
        if win is None:
            return
        self._lset.switch_profile(names[idx], win)

    def _save_profile_as(self):
        from ..widgets import InputDialog
        dlg = InputDialog(tr("另存为布局方案"), tr("方案名称"), parent=self)
        if not dlg.exec():
            return
        name = dlg.value()
        if not name.strip():
            return
        win = self._win()
        if win is None:
            return
        if self._lset.save_current_as_profile(name.strip(), win):
            self._refresh_profiles()
            InfoBar.success(tr("已保存"), tr("布局方案「{0}」已保存").format(name.strip()),
                            parent=self, position=InfoBarPosition.TOP, duration=2500)

    def _delete_profile(self):
        names = getattr(self, "_profile_names", None) or []
        idx = self.profile_box.currentIndex()
        if idx < 0 or idx >= len(names):
            return
        name = names[idx]
        if not name:
            InfoBar.info(tr("默认布局"), tr("默认布局不能删除，可用「重置默认」恢复"),
                         parent=self, position=InfoBarPosition.TOP, duration=2500)
            return
        win = self._win()
        if win is None:
            return
        if self._lset.delete_profile(name, win):
            self._refresh_profiles()
            InfoBar.success(tr("已删除"), tr("布局方案「{0}」已删除").format(name),
                            parent=self, position=InfoBarPosition.TOP, duration=2500)

    def _reset_home_layout(self):
        win = self._win()
        if win is None:
            return
        self._lset.switch_profile("", win)
        self._refresh_profiles()
        InfoBar.success(tr("已重置"), tr("启动页布局已恢复默认"),
                        parent=self, position=InfoBarPosition.TOP, duration=2500)

    def _export_home_layout(self):
        win = self._win()
        if win is not None:
            self._lset.export_current_layout(win, self)

    def _import_home_layout(self):
        win = self._win()
        if win is not None:
            if self._lset.import_layout_file(win, self):
                self._refresh_profiles()

    def _open_sidebar_editor(self):
        win = self._win()
        if win is None:
            return
        from .layout_settings import SidebarEditorDialog
        SidebarEditorDialog(win, self).exec()

    def _open_section_editor(self):
        win = self._win()
        if win is None:
            return
        from .layout_settings import SectionEditorDialog
        SectionEditorDialog(win, self).exec()

    def _save(self):
        # 游戏目录不走 collect()：切目录得经 set_game_dir 做校验和迁移，
        # 不能当普通配置项写。但输入框是可编辑的，用户手打 / 粘贴一个路径后
        # 只点「保存设置」也该生效，不能非得先点「浏览」。
        typed = self.game_dir.text().strip()
        if typed and typed != (self.backend.get_setting("game_dir") or ""):
            try:
                self.backend.set_game_dir(typed)
                self._reload_game_dirs()
            except Exception as e:
                InfoBar.error(tr("游戏目录无效"), str(e), parent=self,
                              position=InfoBarPosition.TOP, duration=4000)
                self.game_dir.setText(self.backend.get_setting("game_dir") or "")
                return
        self.backend.save_settings(self.collect())
        from mclauncher import feedback as fb
        if self.fb_consent.isChecked():
            fb.set_consent(True)
            fb.start_heartbeat()
        else:
            fb.set_consent(False)
            fb.stop_heartbeat(send_offline=False)
        # 语言切换
        lang_map = {v: k for k, v in self.backend.available_languages().items()}
        lang = lang_map.get(self.lang_box.currentText(), "zh_CN")
        from mclauncher import i18n
        # 界面文本在各页构造时就已取好，切语言不会自动重排，得让用户重开
        lang_changed = lang != i18n.current_language()
        i18n.set_language(lang)
        InfoBar.success(tr("已保存"), tr("设置已写入 config.json"), parent=self,
                        position=InfoBarPosition.TOP, duration=2500)
        if lang_changed:
            self._offer_language_restart()
        win = self.window()
        if hasattr(win, "apply_theme"):
            win.apply_theme()
        lp = getattr(win, "launch_page", None)
        if lp is not None and hasattr(lp, "reload"):
            lp.reload()

    def _offer_language_restart(self):
        """界面文案在构造时已取好，切语言后需重启；PCL 同款给立即重启。"""
        from qfluentwidgets import MessageBox
        box = MessageBox(
            tr("语言已切换"),
            tr("界面文字要重启启动器才会全部变成新语言。是否现在重启？"),
            self,
        )
        box.yesButton.setText(tr("立即重启"))
        box.cancelButton.setText(tr("稍后"))
        if not box.exec():
            InfoBar.info(tr("语言已切换"), tr("下次启动启动器后界面才会变成新语言"), parent=self,
                         position=InfoBarPosition.TOP, duration=5000)
            return
        self._restart_launcher()

    def _restart_launcher(self):
        import sys
        from PySide6.QtCore import QProcess
        from PySide6.QtWidgets import QApplication
        args = list(sys.argv)
        # pythonw/python + main.py … 或打包后的 exe
        ok = QProcess.startDetached(sys.executable, args)
        if not ok:
            InfoBar.error(tr("重启失败"), tr("无法拉起新进程，请手动关闭后重新打开"), parent=self,
                          position=InfoBarPosition.TOP, duration=5000)
            return
        QApplication.instance().quit()

    def _browse_game(self):
        path = QFileDialog.getExistingDirectory(self, tr("选择游戏目录"), self.game_dir.text())
        if not path:
            return
        self.game_dir.setText(path)
        try:
            self.backend.set_game_dir(path)
            InfoBar.success(tr("已切换目录"), path, parent=self,
                            position=InfoBarPosition.TOP, duration=2500)
        except Exception as e:
            InfoBar.error(tr("切换失败"), str(e), parent=self,
                          position=InfoBarPosition.TOP, duration=4000)
        self._reload_game_dirs()

    def _reload_game_dirs(self):
        """重建目录列表下拉；active 项即当前生效目录。"""
        self._dirs_updating = True
        try:
            self._dir_entries = self.backend.list_game_dirs()
            self.dirs_box.clear()
            labels, active_idx = [], 0
            for i, e in enumerate(self._dir_entries):
                label = e.get("name") or e.get("path") or ""
                if not e.get("exists"):
                    label += tr("（不存在）")
                labels.append(label)
                if e.get("active"):
                    active_idx = i
            if labels:
                self.dirs_box.addItems(labels)
                self.dirs_box.setCurrentIndex(active_idx)
        except Exception:
            pass
        finally:
            self._dirs_updating = False

    def _switch_dir_entry(self, idx: int):
        if self._dirs_updating or idx < 0 or idx >= len(self._dir_entries):
            return
        entry = self._dir_entries[idx]
        if entry.get("active"):
            return
        try:
            self.backend.set_game_dir(entry.get("raw") or entry.get("path") or "")
            self.game_dir.setText(self.backend.get_setting("game_dir") or "")
            InfoBar.success(tr("已切换目录"), entry.get("path") or "", parent=self,
                            position=InfoBarPosition.TOP, duration=2500)
        except Exception as e:
            InfoBar.error(tr("切换失败"), str(e), parent=self,
                          position=InfoBarPosition.TOP, duration=4000)
        self._reload_game_dirs()

    def _add_game_dir_entry(self):
        path = QFileDialog.getExistingDirectory(self, tr("选择要添加的游戏目录"), "")
        if not path:
            return
        try:
            self.backend.add_game_dir(path)
        except Exception as e:
            InfoBar.error(tr("添加失败"), str(e), parent=self,
                          position=InfoBarPosition.TOP, duration=4000)
            return
        self._reload_game_dirs()

    def _remove_game_dir_entry(self):
        idx = self.dirs_box.currentIndex()
        if idx < 0 or idx >= len(self._dir_entries):
            return
        entry = self._dir_entries[idx]
        try:
            self.backend.remove_game_dir(entry.get("raw") or entry.get("path") or "")
            InfoBar.success(tr("已移除"), tr("仅从列表移除，磁盘文件未动"), parent=self,
                            position=InfoBarPosition.TOP, duration=2500)
        except Exception as e:
            InfoBar.error(tr("移除失败"), str(e), parent=self,
                          position=InfoBarPosition.TOP, duration=4000)
        self._reload_game_dirs()

    def _global_mods(self):
        from .global_mods_dialog import GlobalModsDialog
        GlobalModsDialog(self.backend, self).exec()

    def _check_update(self):
        def ok(info):
            info = info or {}
            if info.get("has_update"):
                self.backend.start_self_update()
                InfoBar.success(tr("发现更新"), info.get("message") or "", parent=self,
                                position=InfoBarPosition.TOP, duration=4000)
            else:
                InfoBar.info(tr("检查更新"), info.get("message") or tr("已是最新"), parent=self,
                             position=InfoBarPosition.TOP, duration=3000)

        def err(exc):
            InfoBar.error(tr("检查失败"), str(exc), parent=self,
                          position=InfoBarPosition.TOP, duration=4000)

        self.backend.call_async(self.backend.check_update, ok, err)

    def _clean(self):
        """清理缓存。预览和实际删除都要遍历整个实例目录，必须走后台线程，
        否则库多的时候点一下按钮界面就是几秒白屏无响应。"""
        self.clean_btn.setEnabled(False)
        self.clean_btn.setText(tr("扫描中…"))

        def scanned(info):
            self.clean_btn.setEnabled(True)
            self.clean_btn.setText(tr("清理"))
            info = info or {}
            from mclauncher.utils import format_size
            from qfluentwidgets import MessageBox
            box_msg = tr("将删除 {0} 个未引用库 / 残留 .part / 更新缓存，约 {1}").format(
                info.get('count') or 0, format_size(info.get('bytes') or 0))
            if not MessageBox(tr("清理文件"), box_msg, self).exec():
                return
            self.clean_btn.setEnabled(False)
            self.clean_btn.setText(tr("清理中…"))
            self.backend.call_async(self.backend.cleaner_apply, cleaned, failed)

        def cleaned(result):
            self.clean_btn.setEnabled(True)
            self.clean_btn.setText(tr("清理"))
            InfoBar.success(tr("清理完成"), tr("删除 {0} 个文件").format((result or {}).get('removed')),
                            parent=self, position=InfoBarPosition.TOP, duration=3000)

        def failed(exc):
            self.clean_btn.setEnabled(True)
            self.clean_btn.setText(tr("清理"))
            InfoBar.error(tr("清理失败"), str(exc), parent=self,
                          position=InfoBarPosition.TOP, duration=4000)

        self.backend.call_async(self.backend.cleaner_preview, scanned, failed)

    def _export(self):
        from mclauncher.config import CONFIG
        from ..widgets import ComboDialog
        name = CONFIG.get("default_instance") or "default"
        items = [tr("Modrinth 整合包 (.mrpack)"), tr("CurseForge 整合包 (.zip)")]
        dlg = ComboDialog(tr("导出整合包"), tr("选择导出格式"), items=items,
                          current=items[0], parent=self.window())
        if not dlg.exec():
            return
        fmt = "curseforge" if dlg.value() == items[1] else "mrpack"
        self.backend.export_modpack(name, fmt=fmt)
        InfoBar.success(tr("开始导出"), tr("实例 {0} → exports/").format(name), parent=self,
                        position=InfoBarPosition.TOP, duration=3000)

    def _test_ai(self):
        # 只拿页面上当前填的值去试连，不落盘。
        # 原来这里直接 save_settings(collect())，用户只想测一下 AI，
        # 结果整页设置（内存、分辨率、下载源……）全被静默写进了 config.json。
        probe = self.backend.get_settings()
        probe.update(self.collect())
        self.test_ai_btn.setEnabled(False)
        self.test_ai_btn.setText(tr("测试中…"))

        def ok(msg):
            self.test_ai_btn.setEnabled(True)
            self.test_ai_btn.setText(tr("测试 AI 连接"))
            InfoBar.success(tr("AI 连接成功"), str(msg), parent=self,
                            position=InfoBarPosition.TOP, duration=4000)

        def err(exc):
            self.test_ai_btn.setEnabled(True)
            self.test_ai_btn.setText(tr("测试 AI 连接"))
            InfoBar.error(tr("AI 连接失败"), str(exc), parent=self,
                          position=InfoBarPosition.TOP, duration=5000)

        self.backend.call_async(lambda: self.backend.test_ai_connection(probe), ok, err)

    def _save_theme(self):
        from ..widgets import InputDialog
        dlg = InputDialog(tr("保存主题包"), tr("主题包名称"), text=tr("我的主题"), parent=self)
        if not dlg.exec():
            return
        name = dlg.value()
        try:
            self.backend.save_theme(name)
            InfoBar.success(tr("已保存"), tr("主题包「{0}」已保存").format(name), parent=self,
                            position=InfoBarPosition.TOP, duration=2500)
        except Exception as e:
            InfoBar.error(tr("保存失败"), str(e), parent=self,
                          position=InfoBarPosition.TOP, duration=4000)

    def _load_theme(self):
        themes = self.backend.list_themes()
        if not themes:
            InfoBar.info(tr("提示"), tr("没有已保存的主题包"), parent=self,
                         position=InfoBarPosition.TOP, duration=2500)
            return
        from ..widgets import ComboDialog
        items = [t["name"] for t in themes]
        dlg = ComboDialog(tr("加载主题"), tr("选择要加载的主题包"), items, parent=self)
        if not dlg.exec():
            return
        name = dlg.value()
        try:
            self.backend.load_theme(name)
            self.refresh_from_config()
            InfoBar.success(tr("已加载"), tr("主题包「{0}」已应用").format(name), parent=self,
                            position=InfoBarPosition.TOP, duration=2500)
            from ..pcl_chrome import apply_ui_font
            apply_ui_font()
            win = self.window()
            if hasattr(win, "apply_theme"):
                win.apply_theme()
        except Exception as e:
            InfoBar.error(tr("加载失败"), str(e), parent=self,
                          position=InfoBarPosition.TOP, duration=4000)

    def _del_theme(self):
        themes = self.backend.list_themes()
        if not themes:
            InfoBar.info(tr("提示"), tr("没有已保存的主题包"), parent=self,
                         position=InfoBarPosition.TOP, duration=2500)
            return
        from ..widgets import ComboDialog
        items = [t["name"] for t in themes]
        dlg = ComboDialog(tr("删除主题"), tr("选择要删除的主题包"), items, parent=self)
        if not dlg.exec():
            return
        name = dlg.value()
        try:
            self.backend.delete_theme(name)
            InfoBar.success(tr("已删除"), tr("主题包「{0}」已删除").format(name), parent=self,
                            position=InfoBarPosition.TOP, duration=2500)
        except Exception as e:
            InfoBar.error(tr("删除失败"), str(e), parent=self,
                          position=InfoBarPosition.TOP, duration=4000)

    def _migrate_official(self):
        """检测 + 扫描官方启动器目录都要走盘，放后台线程。"""
        self.mig_btn.setEnabled(False)
        self.mig_btn.setText(tr("检测中…"))

        def probe():
            if not self.backend.detect_official_launcher():
                return None
            return {
                "dir": self.backend.official_launcher_dir(),
                "versions": self.backend.scan_official_versions(),
            }

        def scanned(found):
            self.mig_btn.setEnabled(True)
            self.mig_btn.setText(tr("检测并迁移"))
            if not found:
                from qfluentwidgets import MessageBox
                box = MessageBox(
                    tr("未检测到官方启动器"),
                    tr("没有找到官方启动器的 .minecraft。也可以手动选择 PCL / HMCL "
                       "或其他启动器的游戏目录导入。"), self)
                box.yesButton.setText(tr("选目录导入…"))
                box.cancelButton.setText(tr("取消"))
                if box.exec():
                    self._migrate_from_dir()
                return
            from qfluentwidgets import MessageBox
            msg = tr("发现官方启动器目录: {0}\n\n发现 {1} 个版本\n\n要导入吗？").format(
                found['dir'], len(found['versions']))
            if not MessageBox(tr("官方启动器迁移"), msg, self).exec():
                return
            try:
                task_id = self.backend.migrate_official_launcher()
                InfoBar.success(tr("迁移中"), tr("导入任务已启动: {0}").format(task_id), parent=self,
                                position=InfoBarPosition.TOP, duration=4000)
            except Exception as e:
                InfoBar.error(tr("迁移失败"), str(e), parent=self,
                              position=InfoBarPosition.TOP, duration=4000)

        def failed(exc):
            self.mig_btn.setEnabled(True)
            self.mig_btn.setText(tr("检测并迁移"))
            InfoBar.error(tr("检测失败"), str(exc), parent=self,
                          position=InfoBarPosition.TOP, duration=4000)

        self.backend.call_async(probe, scanned, failed)

    def _migrate_from_dir(self):
        """手动选任意游戏目录导入（PCL / HMCL / 旧电脑拷来的 .minecraft）。"""
        path = QFileDialog.getExistingDirectory(self, tr("选择要导入的游戏目录"), "")
        if not path:
            return
        self.mig_pick_btn.setEnabled(False)
        self.mig_pick_btn.setText(tr("扫描中…"))

        def _restore():
            self.mig_pick_btn.setEnabled(True)
            self.mig_pick_btn.setText(tr("选目录导入…"))

        def scanned(found):
            _restore()
            found = found or {}
            versions = found.get("versions") or []
            if not versions:
                InfoBar.warning(tr("没有版本"), tr("该目录里没有可导入的版本。"), parent=self,
                                position=InfoBarPosition.TOP, duration=3500)
                return
            from qfluentwidgets import MessageBox
            msg = (f"游戏目录: {found.get('dir')}\n\n"
                   f"发现 {len(versions)} 个版本:\n"
                   + "\n".join(f"· {v}" for v in versions[:12])
                   + ("\n…" if len(versions) > 12 else "") + "\n\n要导入吗？")
            if not MessageBox(tr("导入游戏目录"), msg, self).exec():
                return
            try:
                task_id = self.backend.migrate_official_launcher(src_dir=found.get("dir") or path)
                InfoBar.success(tr("迁移中"), f"导入任务已启动: {task_id}", parent=self,
                                position=InfoBarPosition.TOP, duration=4000)
            except Exception as e:
                InfoBar.error(tr("迁移失败"), str(e), parent=self,
                              position=InfoBarPosition.TOP, duration=4000)

        def failed(exc):
            _restore()
            InfoBar.error(tr("无法导入"), str(exc), parent=self,
                          position=InfoBarPosition.TOP, duration=4500)

        self.backend.call_async(lambda p=path: self.backend.scan_game_dir(p),
                                scanned, failed)

    def _show_recommendation(self):
        """取硬件信息会调 WMI / PowerShell，首次几秒起步，放后台线程。"""
        self.rec_btn.setEnabled(False)
        self.rec_btn.setText(tr("检测中…"))

        def shown(rec):
            self.rec_btn.setEnabled(True)
            self.rec_btn.setText(tr("查看推荐"))
            rec = rec or {}
            mem = rec.get("memory_mb", 4096)
            from qfluentwidgets import MessageBox
            msg = tr("你的系统: {0} GB 内存 / {1} 核 CPU\n\n推荐内存: {2} MB\n\n可以到「性能」设置区调整。").format(
                rec.get('total_ram_gb', '?'), rec.get('cpu_count', '?'), mem)
            box = MessageBox(tr("智能推荐"), msg, self)
            box.yesButton.setText(tr("应用推荐"))
            box.cancelButton.setText(tr("关闭"))
            if box.exec():
                self.memory_spin.setValue(mem)
                InfoBar.success(tr("已应用"), tr("内存已设为 {0} MB，保存设置后生效").format(mem), parent=self,
                                position=InfoBarPosition.TOP, duration=3000)

        def failed(exc):
            self.rec_btn.setEnabled(True)
            self.rec_btn.setText(tr("查看推荐"))
            InfoBar.error(tr("检测失败"), str(exc), parent=self,
                          position=InfoBarPosition.TOP, duration=4000)

        self.backend.call_async(self.backend.get_smart_recommendation, shown, failed)

    def collect(self) -> dict:
        # 语言：只在保存时写入
        lang_map = {v: k for k, v in self.backend.available_languages().items()}
        lang = lang_map.get(self.lang_box.currentText(), "zh_CN")
        return {
            "share_libraries": self.share_libs.isChecked(),
            "share_assets": self.share_assets.isChecked(),
            "download_threads": self.threads_spin.value(),
            "download_source": self._src_keys.get(self.src_box.currentText(), "auto"),
            "community_source": self._comm_keys.get(self.comm_box.currentText(), "auto"),
            **self._collect_proxy(),
            "default_memory_mb": self.memory_spin.value(),
            "auto_memory": self.auto_mem_sw.isChecked(),
            "default_resolution": [self.width_spin.value(), self.height_spin.value()],
            "ms_client_id": self.ms_client_edit.text().strip(),
            "curseforge_api_key": self.curse_key_edit.text().strip(),
            "ai_mode": "custom" if self.ai_mode.currentText() == tr("自定义 NewAPI") else "public",
            "ai_gateway_url": self.ai_gateway.text().strip(),
            "ai_base_url": self.ai_base.text().strip(),
            "ai_api_key": self.ai_key.text().strip(),
            "ai_model": self.ai_model.text().strip() or "deepseek-v4-flash",
            "feedback_url": self.fb_url.text().strip(),
            "feedback_heartbeat": self.fb_hb.isChecked(),
            "feedback_consent": self.fb_consent.isChecked(),
            "ui_fly_animation": self.fly_sw.isChecked(),
            "ui_fly_duration_ms": int(self.fly_dur_spin.value()),
            "ui_dark": self.dark_sw.isChecked(),
            "theme_color": self.color_edit.text().strip() or "#2E9B6B",
            "ui_background": self.bg_edit.text().strip(),
            "default_isolation": self._iso_keys.get(self.iso_box.currentText(), "none"),
            "default_jvm_args": self.jvm_edit.text().strip(),
            "update_url": self.upd_url.text().strip(),
            "launcher_visibility": self._vis_keys.get(self.vis_box.currentText(), "keep"),
            "show_log_window": self.show_log_sw.isChecked(),
            "gc_preset": self._gc_keys.get(self.gc_box.currentText(), "auto"),
            "gpu_mode": self._gpu_keys.get(self.gpu_box.currentText(), "auto"),
            "renderer": self._rnd_keys.get(self.rnd_box.currentText(), "auto"),
            "download_limit_kbps": self.limit_spin.value(),
            "auto_check_update": self.auto_upd.isChecked(),
            "ui_motion": self.motion_sw.isChecked(),
            "homepage_mode": self._home_keys.get(self.home_box.currentText(), "news"),
            "custom_homepage": self.hp_edit.text().strip(),
            "window_mode": self._win_keys.get(self.win_box.currentText(), "window"),
            "game_lang": self._glang_keys.get(self.glang_box.currentText(), "auto"),
            "allow_multi_instance": self.multi_sw.isChecked(),
            "language": lang,
        }
