# -*- coding: utf-8 -*-
"""启动页：自由布局画布（横幅/配置/日志/新闻/便签等卡片，可任意拖拽缩放）。"""

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtWidgets import QTextBrowser, QVBoxLayout, QWidget
from qfluentwidgets import (
    CaptionLabel, InfoBar, InfoBarPosition, StrongBodyLabel,
)

from mclauncher.config import CONFIG
from mclauncher.instances import JAVA_AUTO
from .crash_dialog import CrashDialog
from ..widgets import DeviceCodeDialog
from .. import layout_model
from ..dashboard import DashboardCanvas
from .home_cards import (
    BannerBody, ConfigBody, LogBody, NewsBody, build_registry,
)
from mclauncher.i18n import tr


class LaunchPage(QWidget):
    def __init__(self, backend, parent=None):
        super().__init__(parent)
        self.setObjectName("launchPage")
        self.backend = backend
        self._task_id = None
        self._login_dlg = None
        self._login_task_id = None
        self._java_opts = []
        self._syncing_java = False
        self._crash_shown = False
        self._body_cache = {}   # 单例卡片正文缓存：移除再添加时复用控件状态

        # 四个单例正文先于画布构造：页面逻辑（reload/启动/日志）始终能
        # 稳定引用 instance_box / log_edit 等控件，即使卡片被用户移除。
        for BodyCls in (BannerBody, ConfigBody, LogBody, NewsBody):
            if BodyCls.key not in self._body_cache:
                self._body_cache[BodyCls.key] = BodyCls(self, None, None)
        self.launch_btn.clicked.connect(self._on_launch)
        self.stop_btn.clicked.connect(self._on_stop)
        self.instance_box.currentTextChanged.connect(self._on_instance_changed)
        self.java_box.currentTextChanged.connect(self._on_java_changed)
        self.version_box.currentTextChanged.connect(self._sync_banner)
        # 记住「上次从 CONFIG 同步过来的值」，reload() 靠它区分
        # 「用户在本页手改过」和「一直是配置里的默认值」。
        self._cfg_snapshot = (
            int(CONFIG.get("memory_mb", 4096)),
            int(CONFIG.get("width", 854)),
            int(CONFIG.get("height", 480)),
        )

        self.registry = build_registry(self)
        self.canvas = DashboardCanvas(self.registry, self)
        self.canvas.layout_changed.connect(self._on_layout_changed)
        self.canvas.build_from_doc(layout_model.load_active_doc())

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(0)
        root.addWidget(self.canvas)

        self._layout_persist = QTimer(self)
        self._layout_persist.setSingleShot(True)
        self._layout_persist.setInterval(400)
        self._layout_persist.timeout.connect(self._persist_layout_now)

        backend.progress.connect(self._on_progress)
        backend.log.connect(self._on_log)
        backend.finished.connect(self._on_finished)
        backend.crash.connect(self._on_crash)
        backend.login_code.connect(self._on_login_code)
        backend.login_status.connect(self._on_login_status)

        # 扫盘（实例/账号/版本）延后到事件循环空转：首帧先出壳，
        # MainWindow._boot_reload 的合并刷新会覆盖这次 reload。
        QTimer.singleShot(0, self._boot_load)

    # ------------------------------------------------------------------
    # 布局：持久化 / 方案应用 / 编辑入口
    # ------------------------------------------------------------------
    def _on_layout_changed(self):
        self._layout_persist.start()

    def persist_layout_soon(self):
        """卡片内容（便签文字、快捷入口配置）变化时的落盘入口。"""
        self._layout_persist.start()

    def _persist_layout_now(self):
        doc = self.canvas.current_doc()
        name = layout_model.active_profile()
        layout_model.save_active_doc(doc)
        if name:
            # 命名方案被就地编辑：同步回方案表，切换回来不丢改动。
            layout_model.save_profile(name, doc)

    def apply_doc(self, doc):
        """外部（设置页切方案）应用一份新布局，不触发落盘回环。"""
        self.canvas.build_from_doc(doc)

    def enter_edit_mode(self):
        self.canvas.set_edit_mode(True)

    def nav_to(self, key: str):
        win = self.window()
        if win is not None and hasattr(win, "switchTo"):
            win.switchTo(key)

    def restyle(self):
        self.canvas.restyle()

    def _boot_load(self):
        if getattr(self, "_boot_loaded", False):
            return
        self._boot_loaded = True
        self.reload()
        self._load_news()

    def _version_setup(self):
        from .version_setup import VersionSetupDialog
        inst = self.instance_box.currentText() or "default"
        ver = self.version_box.currentText()
        if not ver:
            InfoBar.info(tr("未选择版本"), tr("请先安装并选择一个版本"), parent=self,
                         position=InfoBarPosition.TOP, duration=2500)
            return
        dlg = VersionSetupDialog(self.backend, inst, ver, self)
        if dlg.exec():
            dlg.save()
            InfoBar.success(tr("已保存"), tr("版本设置已写入"), parent=self,
                            position=InfoBarPosition.TOP, duration=2000)

    def _load_news(self):
        if getattr(self, "news_body", None) is None:
            return
        while self.news_host.count():
            item = self.news_host.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        mode = CONFIG.get("homepage_mode") or "news"
        if mode == "blank":
            self.news_body.set_title(tr("主页"))
            self.news_host.addWidget(CaptionLabel(tr("主页已设为空白")))
            return
        if mode == "custom":
            from pathlib import Path
            self.news_body.set_title(tr("自定义主页"))
            path = CONFIG.get("custom_homepage") or ""
            browser = QTextBrowser()
            browser.setOpenExternalLinks(True)
            p = Path(path) if path else None
            if p and p.is_file():
                if p.suffix.lower() in (".html", ".htm"):
                    try:
                        browser.setSource(QUrl.fromLocalFile(str(p.resolve())))
                    except Exception as exc:
                        browser.setPlainText(tr("无法加载自定义主页：{0}").format(exc))
                else:
                    try:
                        browser.setPlainText(p.read_text(encoding="utf-8", errors="replace"))
                    except OSError as exc:
                        browser.setPlainText(tr("无法读取自定义主页：{0}").format(exc))
            else:
                browser.setPlainText(tr("未设置自定义主页。到设置 → 启动页主页 填写本地 HTML 路径。"))
            self.news_host.addWidget(browser)
            return
        self.news_body.set_title(tr("Minecraft 新闻"))
        cached = self.backend.cached_news()
        self._fill_news(cached)

        def ok(rows):
            if not getattr(self, "news_host", None):
                return
            if (CONFIG.get("homepage_mode") or "news") != "news":
                return
            while self.news_host.count():
                item = self.news_host.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            self._fill_news(rows or [])

        def err(exc):
            if not getattr(self, "news_host", None):
                return
            InfoBar.warning(
                tr("新闻刷新失败"),
                str(exc or tr("将继续显示缓存")),
                parent=self, position=InfoBarPosition.TOP, duration=3500,
            )

        self.backend.call_async(self.backend.fetch_news, ok, err)

    def _fill_news(self, rows):
        if not rows:
            self.news_host.addWidget(CaptionLabel(tr("暂无新闻")))
            return
        for row in rows[:6]:
            t = StrongBodyLabel(row.get("title") or "")
            d = CaptionLabel((row.get("body") or row.get("version") or "")[:80])
            d.setWordWrap(True)
            self.news_host.addWidget(t)
            self.news_host.addWidget(d)

    def reload(self):
        if self._task_id and not self.launch_btn.isEnabled():
            return
        self.canvas.refresh_cards()
        cur_inst = self.instance_box.currentText()
        self.instance_box.blockSignals(True)
        self.instance_box.clear()
        names = [i["name"] for i in self.backend.get_instances()]
        self.instance_box.addItems(names)
        if cur_inst in names:
            self.instance_box.setCurrentText(cur_inst)
        elif CONFIG.get("default_instance") in names:
            self.instance_box.setCurrentText(CONFIG.get("default_instance"))
        self.instance_box.blockSignals(False)

        cur_acc = self.account_box.currentText()
        accounts = self.backend.get_accounts()
        self.account_box.clear()
        self.account_box.addItems(accounts)
        active = None
        for row in self.backend.get_account_rows():
            if row.get("active"):
                active = row.get("name")
                break
        if cur_acc in accounts:
            self.account_box.setCurrentText(cur_acc)
        elif active in accounts:
            self.account_box.setCurrentText(active)

        self._sync_from_config()
        self._reload_versions()
        self._reload_java_box()

    def _on_memory_changed(self, value: int):
        self.memory_label.setText(f"{value} MB")
        self._persist_launch_defaults()

    def _persist_launch_defaults(self, *_args):
        """启动页改的内存 / 分辨率写回 CONFIG（防抖入口）。

        滑条每拖一格、SpinBox 每点一次箭头都会触发 valueChanged，
        直接落盘等于每次都原子写 + fsync config.json，拖动时磁盘
        和 UI 一起卡。这里 400ms 合并；点「启动游戏」时立即冲刷。
        """
        if not hasattr(self, "_defaults_persist"):
            self._defaults_persist = QTimer(self)
            self._defaults_persist.setSingleShot(True)
            self._defaults_persist.setInterval(400)
            self._defaults_persist.timeout.connect(self._persist_launch_defaults_now)
        self._defaults_persist.start()

    def _flush_launch_defaults(self):
        """立刻落盘待写的默认值（启动游戏 / 关窗前调用）。"""
        timer = getattr(self, "_defaults_persist", None)
        if timer is not None and timer.isActive():
            timer.stop()
            self._persist_launch_defaults_now()

    def _persist_launch_defaults_now(self):
        mem = int(self.memory_slider.value())
        w = int(self.width_spin.value())
        h = int(self.height_spin.value())
        CONFIG.set("memory_mb", mem)
        CONFIG.set("width", w)
        CONFIG.set("height", h)
        CONFIG.save()
        self._cfg_snapshot = (mem, w, h)

    def _sync_from_config(self):
        """把设置页刚保存的内存 / 分辨率同步到本页。

        这三个控件原来只在构造时读一次 CONFIG，`reload()` 完全不管它们，
        于是「设置里改了默认内存 → 回启动页 → 直接启动」用的还是旧值，得重开启动器才对得上。
        只覆盖用户没在本页动过的控件，避免把他这次临时调的参数冲掉。
        """
        mem, w, h = self._cfg_snapshot
        new_mem = int(CONFIG.get("memory_mb", 4096))
        new_w = int(CONFIG.get("width", 854))
        new_h = int(CONFIG.get("height", 480))
        if self.memory_slider.value() == mem:
            self.memory_slider.setValue(new_mem)
        if self.width_spin.value() == w:
            self.width_spin.setValue(new_w)
        if self.height_spin.value() == h:
            self.height_spin.setValue(new_h)
        self._cfg_snapshot = (new_mem, new_w, new_h)

    def _on_instance_changed(self):
        self._reload_versions()
        self._reload_java_box()

    def _reload_java_box(self):
        instance = self.instance_box.currentText() or "default"
        self._apply_java_opts(instance, self.backend.java_combo_options(instance, scan_system=False))
        call_async = getattr(self.backend, "call_async", None)
        if callable(call_async):
            call_async(
                lambda inst=instance: self.backend.java_combo_options(inst, True),
                lambda opts, inst=instance: self._on_java_opts(inst, opts),
            )

    def _on_java_opts(self, instance, opts):
        if (self.instance_box.currentText() or "default") != instance:
            return
        self._apply_java_opts(instance, opts or [])

    def _apply_java_opts(self, instance, opts):
        self._syncing_java = True
        try:
            self._java_opts = opts or []
            labels = [o["label"] for o in self._java_opts]
            self.java_box.blockSignals(True)
            self.java_box.clear()
            self.java_box.addItems(labels)
            want = self.backend.java_combo_label_for(instance, self._java_opts)
            self.java_box.setCurrentText(want if want in labels else JAVA_AUTO)
            self.java_box.blockSignals(False)
        finally:
            self._syncing_java = False

    def _on_java_changed(self, _text=""):
        if self._syncing_java:
            return
        instance = self.instance_box.currentText()
        if not instance:
            return
        self.backend.set_instance_java(instance, self._selected_java())

    def _selected_java(self) -> str:
        text = self.java_box.currentText() or JAVA_AUTO
        for o in self._java_opts:
            if o["label"] == text:
                return o["value"]
        return text

    def _reload_versions(self):
        cur = self.version_box.currentText()
        self.version_box.blockSignals(True)
        self.version_box.clear()
        instance = self.instance_box.currentText() or "default"
        ids = self.backend.get_installed_versions(instance)
        self.version_box.addItems(ids)
        if cur in ids:
            self.version_box.setCurrentText(cur)
        self.version_box.blockSignals(False)
        self._sync_banner()

    def _sync_banner(self):
        version = self.version_box.currentText() or "—"
        instance = self.instance_box.currentText() or "default"
        pack_name = ""
        pack_ver = ""
        pack_mc = ""
        for row in self.backend.get_instances():
            if row.get("name") == instance:
                pack_name = row.get("pack") or ""
                pack_ver = row.get("pack_version") or ""
                pack_mc = row.get("mc_version") or ""
                break
        if pack_name:
            bits = [b for b in (pack_ver, f"Minecraft {pack_mc}" if pack_mc else "", f"实例 {instance}") if b]
            self.banner.set_info(pack_name, " · ".join(bits) or version)
        else:
            self.banner.set_info(version, f"实例 {instance} · 点击「启动游戏」进入世界")

    def _on_launch(self):
        from qfluentwidgets import MessageBox

        self._flush_launch_defaults()
        instance = self.instance_box.currentText() or "default"
        version = self.version_box.currentText()
        memory_mb = self.memory_slider.value()
        java = self._selected_java()
        try:
            pf = self.backend.preflight_launch(
                instance=instance, version=version,
                memory_mb=memory_mb, java=java,
            )
        except Exception as exc:
            MessageBox(tr("启动预检失败"), str(exc), self).exec()
            return

        items = list((pf or {}).get("items") or [])
        errors = [i for i in items if i.get("level") == "error"]
        warns = [i for i in items if i.get("level") == "warn"]
        if errors:
            body = "\n\n".join(
                f"· {e.get('title')}\n{e.get('detail')}" for e in errors)
            MessageBox(tr("启动预检未通过"), body, self).exec()
            return
        if warns:
            body = "\n\n".join(
                f"· {w.get('title')}\n{w.get('detail')}" for w in warns)
            box = MessageBox(
                tr("启动预检有警告"),
                body + "\n\n" + tr("是否仍要继续启动？"),
                self,
            )
            box.yesButton.setText(tr("继续启动"))
            box.cancelButton.setText(tr("取消"))
            if not box.exec():
                return

        self.log_edit.clear()
        for w in warns:
            self.log_edit.appendPlainText(
                f"[预检:warn] {w.get('title')}: {w.get('detail')}")
        self.progress.setValue(0)
        self.status_label.setText(tr("准备启动…"))
        self.launch_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self._crash_shown = False
        extra = []
        server = self.server_edit.text().strip()
        if server:
            if ":" in server:
                host, port = server.rsplit(":", 1)
                extra = ["--server", host, "--port", port]
            else:
                extra = ["--server", server, "--port", "25565"]
        self._task_id = self.backend.launch_game(
            instance=instance,
            version=version,
            account=self.account_box.currentText(),
            username=self.username_edit.text().strip(),
            memory_mb=memory_mb,
            width=self.width_spin.value(),
            height=self.height_spin.value(),
            java=java,
            extra_game_args=extra or None,
        )

    def _on_stop(self):
        if self._task_id:
            self.backend.cancel_task(self._task_id)

    def _copy_cmd(self):
        try:
            cmd = self.backend.build_launch_command(
                instance=self.instance_box.currentText() or "default",
                version=self.version_box.currentText(),
                account=self.account_box.currentText(),
                username=self.username_edit.text().strip(),
                memory_mb=self.memory_slider.value(),
                width=self.width_spin.value(),
                height=self.height_spin.value(),
                java=self._selected_java(),
            )
            from PySide6.QtGui import QGuiApplication
            QGuiApplication.clipboard().setText(cmd)
            from qfluentwidgets import InfoBar, InfoBarPosition
            InfoBar.success(tr("已复制"), tr("启动命令已复制到剪贴板"), parent=self,
                            position=InfoBarPosition.TOP_RIGHT, duration=2500)
        except Exception as e:
            from qfluentwidgets import InfoBar, InfoBarPosition
            InfoBar.error(tr("复制失败"), str(e), parent=self,
                          position=InfoBarPosition.TOP_RIGHT, duration=3500)

    def _login(self):
        if self._login_dlg:
            return
        self._login_dlg = DeviceCodeDialog(self.window())
        self._login_task_id = self.backend.start_microsoft_login()
        accepted = self._login_dlg.exec()
        self._login_dlg = None
        # 用户关掉设备码框就是放弃登录：以前不取消后台任务、也不清 task_id，
        # 那个轮询会一直问微软要令牌直到超时，期间再点一次登录还会撞上旧任务的回调。
        if not accepted and self._login_task_id:
            cancel = getattr(self.backend, "cancel_task", None)
            if callable(cancel):
                try:
                    cancel(self._login_task_id)
                except Exception:
                    pass
            self._login_task_id = None
        self.reload()

    def _on_login_code(self, code, uri):
        if self._login_dlg:
            self._login_dlg.show_code(code, uri)

    def _on_login_status(self, text):
        if self._login_dlg:
            self._login_dlg.show_status(text)

    def _on_progress(self, task_id, current, total, message):
        if task_id != self._task_id:
            return
        self.progress.setValue(min(100, max(0, int(current * 100 / total))) if total else 0)
        status, speed = (message or "").split("  |  ", 1) if "  |  " in (message or "") else (message, "")
        self.status_label.setText((status or tr("处理中…")) + (f"    {speed}" if speed else ""))

    def _on_log(self, task_id, text):
        if task_id == self._task_id:
            self.log_edit.appendPlainText(text)

    def _on_crash(self, task_id, report):
        if task_id != self._task_id:
            return
        self._crash_shown = True
        win = self.window()
        dlg = CrashDialog(
            report or {}, win, backend=getattr(win, "backend", None)
        )
        dlg.exec()
        if getattr(dlg, "want_relaunch", False):
            # 用报告里的实例/版本对齐选择框后再启动
            rep = report or {}
            inst = rep.get("instance") or ""
            ver = rep.get("version") or ""
            if inst:
                idx = self.instance_box.findText(inst)
                if idx >= 0:
                    self.instance_box.setCurrentIndex(idx)
            if ver:
                idx = self.version_box.findText(ver)
                if idx >= 0:
                    self.version_box.setCurrentIndex(idx)
            self._on_launch()

    def _on_finished(self, task_id, success, message):
        if task_id == self._login_task_id:
            if self._login_dlg:
                if success:
                    self._login_dlg.accept()
                else:
                    self._login_dlg.show_status(message)
            if success:
                self.reload()
        if task_id != self._task_id:
            return
        self.launch_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.status_label.setText(message)
        if success:
            self.progress.setValue(100)
            InfoBar.success(tr("游戏已结束"), message or tr("已正常退出"), parent=self,
                             position=InfoBarPosition.TOP, duration=3000)
            return
        if self._crash_shown or message == tr("已取消"):
            if message == tr("已取消"):
                InfoBar.info(tr("已停止"), message, parent=self,
                             position=InfoBarPosition.TOP, duration=2500)
            return
        win = self.window()
        dlg = CrashDialog({
            "title": tr("启动失败"),
            "headline": tr("启动中止"),
            "detail": message or tr("启动失败"),
            "help": tr("这是启动器在拉起游戏之前捕获的错误，还没有游戏崩溃报告。"),
            "instance": self.instance_box.currentText() or "default",
            "version": self.version_box.currentText() or "",
        }, win, backend=getattr(win, "backend", None))
        dlg.exec()
        if getattr(dlg, "want_relaunch", False):
            self._on_launch()
