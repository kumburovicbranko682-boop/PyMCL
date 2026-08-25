# -*- coding: utf-8 -*-
"""Qt-free BackendAPI，行为对齐 app/backend.py。"""

from __future__ import annotations

import itertools
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from mclauncher import utils
from mclauncher.auth import AccountManager, MicrosoftAuthenticator
from mclauncher.catalog import CBC_CF_ID, CBC_CF_SLUG, CDC_CF_ID, CDC_CF_SLUG, POPULAR_MODPACKS, POPULAR_MODS
from mclauncher.config import CONFIG
from mclauncher.downloader import DownloadManager
from mclauncher.instances import Instance, InstanceError, list_instances, JAVA_AUTO
from mclauncher.installer import Installer, InstallError
from mclauncher import java as java_mod
from mclauncher import manifest as manifest_mod
from mclauncher import modpack as modpack_mod
from mclauncher import mods as mods_mod
from mclauncher.crash import GameCrashError, analyze_launch, export_report, open_path
from mclauncher.launcher import LaunchError, build_launch_command, GameProcess
from mclauncher import terracotta as terracotta_mod

_tls = threading.local()


class TaskCancelled(Exception):
    """用户取消任务时由 progress 回调抛出。"""


class EventBus:
    def __init__(self):
        self._lock = threading.Lock()
        self._subs: list = []

    def emit(self, event: str, data: dict):
        payload = {"event": event, "data": data}
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(payload)
            except Exception:
                pass

    def subscribe(self):
        import queue
        q = queue.Queue(maxsize=800)
        with self._lock:
            self._subs.append(q)
        return q

    def unsubscribe(self, q):
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)


class BackendWorker(threading.Thread):
    def __init__(self, task_id: str, target, args=(), kwargs=None, emit=None):
        super().__init__(daemon=True, name=task_id)
        self.task_id = task_id
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}
        self._cancelled = False
        self._emit = emit or (lambda *_a, **_k: None)

    def cancel(self):
        self._cancelled = True

    def _progress(self, current, total, message=""):
        if self._cancelled:
            raise TaskCancelled()
        self._emit("progress", {
            "task_id": self.task_id,
            "current": int(current or 0),
            "total": int(total or 0),
            "message": str(message or ""),
        })

    def _log(self, text):
        self._emit("log", {"task_id": self.task_id, "text": str(text)})

    def login_code(self, code, uri):
        self._emit("login_code", {"code": str(code), "uri": str(uri)})

    def login_status(self, text):
        self._emit("login_status", {"text": str(text)})

    def run(self):
        _tls.worker = self
        try:
            result = self._target(self._progress, self._log, *self._args, **self._kwargs)
            msg = result if isinstance(result, str) and result else "任务完成"
            self._emit("finished", {"task_id": self.task_id, "success": True, "message": msg})
        except TaskCancelled:
            self._emit("finished", {"task_id": self.task_id, "success": False, "message": "已取消"})
        except GameCrashError as exc:
            self._log(f"[错误] {exc}")
            payload = dict(exc.report or {})
            payload["task_id"] = self.task_id
            self._emit("crash", payload)
            self._emit("finished", {
                "task_id": self.task_id, "success": False, "message": str(exc), "crash": True,
            })
        except Exception as exc:  # noqa: BLE001
            self._log(f"[错误] {exc}")
            self._emit("finished", {"task_id": self.task_id, "success": False, "message": str(exc)})
        finally:
            _tls.worker = None


class BackendAPI:
    """后端门面。行为对齐 app.backend.BackendAPI。"""

    def __init__(self, bus: EventBus):
        self._bus = bus
        self._counter = itertools.count(1)
        self._workers: dict[str, BackendWorker] = {}
        self._titles: dict[str, str] = {}
        self._lock = threading.Lock()
        self.accounts = AccountManager()
        self._game_proc = None
        self._game_lock = threading.Lock()
        self._launch_task_id = None
        self._pack_cache: list[dict] = []
        self._mod_cache: list[dict] = []
        self._crashes: dict[str, dict] = {}
        self._task_results: dict[str, tuple] = {}
        self._ai_lock = threading.Lock()
        self._ai_cancel = False
        self._ai_http = None
        self._ai_confirm_ev = threading.Event()
        self._ai_confirm_ok = False
        self._ai_ask_ev = threading.Event()
        self._ai_ask_result = None
        self._ai_busy = False
        self._ui_launch = {}
        self._ensure_default_instance()

    def _emit(self, event: str, data: dict):
        if event == "crash":
            tid = (data or {}).get("task_id")
            if tid:
                self._crashes[tid] = data or {}
                if len(self._crashes) > 40:
                    extra = list(self._crashes)[:-20]
                    for k in extra:
                        self._crashes.pop(k, None)
            self._bus.emit("crash", data)
            return
        if event == "finished":
            tid = data.get("task_id")
            self._task_results[tid] = (bool(data.get("success")), str(data.get("message") or ""))
            if len(self._task_results) > 80:
                extra = list(self._task_results)[:-40]
                for k in extra:
                    self._task_results.pop(k, None)
            with self._lock:
                self._workers.pop(tid, None)
                count = len(self._workers)
            self._bus.emit("finished", data)
            self._bus.emit("task_count_changed", {"count": count})
            if data.get("success"):
                self._bus.emit("ui_changed", {})
            return
        self._bus.emit(event, data)

    def _ensure_default_instance(self):
        names = list_instances()
        if names:
            return
        name = CONFIG.get("default_instance", "default") or "default"
        try:
            Instance(name).create()
        except InstanceError:
            pass

    def start_task(self, title: str, fn, *args, **kwargs) -> str:
        task_id = f"task-{next(self._counter)}"
        worker = BackendWorker(task_id, fn, args, kwargs, self._emit)
        with self._lock:
            self._workers[task_id] = worker
            self._titles[task_id] = title
            count = len(self._workers)
        worker.start()
        self._emit("task_added", {"task_id": task_id, "title": title})
        self._emit("task_count_changed", {"count": count})
        return task_id

    def cancel_task(self, task_id: str):
        with self._lock:
            worker = self._workers.get(task_id)
        if worker:
            worker.cancel()
        if task_id != self._launch_task_id:
            return
        with self._game_lock:
            proc = self._game_proc
        if proc:
            try:
                proc.kill()
            except Exception:
                pass

    def task_title(self, task_id: str) -> str:
        return self._titles.get(task_id, task_id)

    def get_crash(self, task_id: str = "") -> dict:
        if task_id and task_id in self._crashes:
            return self._crashes[task_id]
        if self._crashes:
            return self._crashes[next(reversed(self._crashes))]
        return {}

    def export_crash_report(self, task_id: str = "", dest: str = "") -> str:
        report = self.get_crash(task_id)
        if not report:
            raise LaunchError("没有可导出的错误报告")
        return export_report(report, dest or None)

    def open_crash_file(self, path: str = "", task_id: str = "") -> str:
        target = path or (self.get_crash(task_id).get("direct_file") or "")
        if not target:
            raise LaunchError("没有可打开的日志文件")
        if not open_path(target):
            raise LaunchError(f"无法打开: {target}")
        return target

    def _dm(self, progress, log) -> DownloadManager:
        worker = getattr(_tls, "worker", None)
        last_key = [""]

        def on_progress(message, done, total):
            text = message or ""
            progress(done or 0, total or 0, text)
            if "  |  " in text:
                key = text.split("  |  ", 1)[0].strip(" ·")
                if key and key != last_key[0]:
                    last_key[0] = key
                    log(text)
                return
            stripped = text.strip()
            if stripped and stripped != last_key[0]:
                last_key[0] = stripped
                log(stripped)

        def cancelled():
            return bool(getattr(worker, "_cancelled", False))

        return DownloadManager(
            threads=CONFIG.get("download_threads", 8),
            on_progress=on_progress,
            cancel=cancelled,
        )

    def _instance(self, name=None) -> Instance:
        name = name or CONFIG.get("default_instance", "default")
        inst = Instance(name)
        if not inst.path.is_dir():
            inst.create()
        else:
            inst.ensure_standard_dirs()
        return inst

    def _lookup_pack(self, name: str, source: str) -> dict:
        q = (name or "").lower().strip()
        for hit in self._pack_cache:
            name_l = (hit.get("name") or "").lower()
            slug_l = (hit.get("slug") or "").lower()
            id_l = str(hit.get("id") or "").lower()
            if q and q in (name_l, slug_l, id_l):
                return hit
        src = "curseforge" if source.lower().startswith("curse") else "modrinth"
        for title, pack_src, key, slug in POPULAR_MODPACKS:
            if title.lower() == q or str(key).lower() == q:
                return {
                    "name": title,
                    "source": pack_src,
                    "id": key if pack_src == "curseforge" else None,
                    "slug": slug if pack_src == "curseforge" else key,
                }
        return {"name": name, "source": src, "slug": name}

    def _lookup_mod(self, name: str, source: str) -> dict:
        q = (name or "").lower().strip()
        for hit in self._mod_cache:
            name_l = (hit.get("name") or "").lower()
            slug_l = (hit.get("slug") or "").lower()
            id_l = str(hit.get("id") or "").lower()
            if q and q in (name_l, slug_l, id_l):
                return hit
        for title, mod_src, key, *_rest in POPULAR_MODS:
            if title.lower() == q or str(key).lower() == q:
                return {
                    "name": title,
                    "source": mod_src,
                    "id": key if mod_src == "curseforge" else None,
                    "slug": key if mod_src != "curseforge" else None,
                }
        src = "curseforge" if source.lower().startswith("curse") else "modrinth"
        return {"name": name, "source": src, "slug": name}

    def install_game(self, version: str, loader: str = "无", loader_version: str = "",
                     instance: str = "", extra: dict | None = None) -> str:
        inst = instance or CONFIG.get("default_instance", "default")
        extra = extra or {}
        bits = [version]
        if loader and loader not in ("", "无"):
            bits.append(loader)
        if extra.get("optifine"):
            bits.append("OptiFine")
        if extra.get("liteloader"):
            bits.append("LiteLoader")
        title = "安装游戏 " + " + ".join(bits)
        return self.start_task(title, self._install_game_impl, version, loader, loader_version, inst, extra)

    def install_modpack(self, name: str, source: str = "Modrinth", extra: dict | None = None) -> str:
        return self.start_task(f"安装整合包 {Path(name).name}", self._install_modpack_impl,
                               name, source, extra or {})

    def install_mod(self, name: str, instance: str = "default", extra: dict | None = None) -> str:
        return self.start_task(f"安装模组 {Path(str(name)).name}", self._install_mod_impl,
                               name, instance, extra or {})

    def install_shader(self, name: str, instance: str = "default", extra: dict | None = None) -> str:
        return self.start_task(f"安装光影 {Path(str(name)).name}", self._install_content_impl,
                               "shader", name, instance, extra or {})

    def install_resourcepack(self, name: str, instance: str = "default", extra: dict | None = None) -> str:
        return self.start_task(f"安装资源包 {Path(str(name)).name}", self._install_content_impl,
                               "resourcepack", name, instance, extra or {})

    def install_datapack(self, name: str, instance: str = "default", extra: dict | None = None) -> str:
        return self.start_task(f"安装数据包 {Path(str(name)).name}", self._install_content_impl,
                               "datapack", name, instance, extra or {})

    def install_world(self, name: str, instance: str = "default", extra: dict | None = None) -> str:
        return self.start_task(f"安装世界 {Path(str(name)).name}", self._install_world_impl,
                               name, instance, extra or {})

    def list_catalog_files(self, extra: dict | None = None) -> list[dict]:
        from mclauncher.catalog_files import list_project_files
        return list_project_files(DownloadManager(threads=2), extra or {})

    def get_file_changelog(self, extra: dict | None = None) -> str:
        """拉取目录文件/版本的完整更新日志（Modrinth 原文 / CF HTML 转文本）。"""
        from mclauncher.catalog_files import fetch_changelog
        return fetch_changelog(DownloadManager(threads=2), extra or {})

    def list_loader_versions(self, mc_version: str, loader: str) -> list[dict]:
        from mclauncher.loader_meta import list_loader_versions
        return list_loader_versions(DownloadManager(threads=2), mc_version, loader)

    def search_worlds(self, query: str, source: str = "CurseForge", extra: dict | None = None) -> list[dict]:
        from mclauncher import worlds as worlds_mod
        extra = dict(extra or {})
        extra.setdefault("source", source)
        return worlds_mod.search_worlds(DownloadManager(threads=2), query, extra)

    def rename_version(self, instance: str, version: str, new_id: str) -> str:
        from mclauncher import version_ops as vops
        return vops.rename_version(self._instance(instance), version, new_id)

    def copy_version(self, instance: str, version: str, new_id: str) -> str:
        from mclauncher import version_ops as vops
        return vops.copy_version(self._instance(instance), version, new_id)

    def hide_version(self, instance: str, version: str, hidden: bool = True) -> dict:
        from mclauncher import version_ops as vops
        return vops.set_hidden(self._instance(instance), version, hidden)

    def open_version_folder(self, instance: str, version: str = "", which: str = "root") -> str:
        from mclauncher import version_ops as vops
        return vops.open_folder(self._instance(instance), version, which)

    def export_launch_script(self, instance: str, version: str, dest: str = "") -> str:
        return self.start_task(f"导出启动脚本 {version}", self._export_bat_impl, instance, version, dest)

    def create_desktop_shortcut(self, instance: str, version: str, username: str = "",
                                account: str = "", name: str = "") -> str:
        from mclauncher import shortcut
        return shortcut.create_launch_shortcut(instance, version, username, account, name)

    def backup_save(self, instance: str, name: str, version: str = "") -> str:
        return self.start_task(f"备份存档 {name}", self._backup_save_impl, instance, name, version)

    def _backup_save_impl(self, progress, log, instance, name, version):
        from mclauncher import saves as saves_mod
        info = saves_mod.backup_save(
            self._instance(instance), name, version,
            on_progress=lambda text, cur, total: progress(cur, total, text))
        log(f"备份完成: {info['path']}")
        self._emit("ui_changed", {})
        return f"已备份到 {info['name']}"

    def list_save_backups(self, instance: str, name: str = "", version: str = "") -> list[dict]:
        from mclauncher import saves as saves_mod
        return saves_mod.list_backups(self._instance(instance), name, version)

    def restore_save_backup(self, instance: str, backup_name: str, version: str = "",
                            overwrite: bool = False) -> dict:
        from mclauncher import saves as saves_mod
        out = saves_mod.restore_backup(
            self._instance(instance), backup_name, version, overwrite=overwrite)
        self._emit("ui_changed", {})
        return out

    def delete_save_backup(self, instance: str, backup_name: str, version: str = ""):
        from mclauncher import saves as saves_mod
        saves_mod.delete_backup(self._instance(instance), backup_name, version)
        self._emit("ui_changed", {})

    def export_save(self, instance: str, name: str, dest: str, version: str = "") -> str:
        from mclauncher import saves as saves_mod
        return saves_mod.export_save(self._instance(instance), name, dest, version)

    def list_saves(self, instance: str, version: str = "") -> list[dict]:
        from mclauncher import saves as saves_mod
        return saves_mod.list_saves(self._instance(instance), version)

    def delete_save(self, instance: str, name: str, version: str = ""):
        from mclauncher import saves as saves_mod
        saves_mod.delete_save(self._instance(instance), name, version)

    def open_save(self, instance: str, name: str, version: str = "") -> str:
        from mclauncher import saves as saves_mod
        return saves_mod.open_save(self._instance(instance), name, version)

    def install_datapack_into_save(self, instance: str, filename: str, save_name: str,
                                   version: str = "") -> str:
        from mclauncher import saves as saves_mod
        return saves_mod.install_datapack_into_save(self._instance(instance), filename, save_name, version)

    def list_media(self, instance: str, kind: str, version: str = "") -> list[dict]:
        from mclauncher import saves as saves_mod
        return saves_mod.list_media(self._instance(instance), kind, version)

    def delete_modpack(self, instance: str, filename: str = ""):
        inst = self._instance(instance)
        meta = inst.meta() or {}
        pack = meta.get("modpack")
        if not isinstance(pack, dict) or not pack.get("name"):
            raise InstanceError("该实例没有已安装整合包")
        inst.delete()

    def list_global_mods(self) -> list[dict]:
        from mclauncher import global_mods as gm
        return gm.list_entries()

    def set_global_mod_enabled(self, filename: str, enabled: bool) -> str:
        from mclauncher import global_mods as gm
        return gm.set_enabled(filename, enabled)

    def start_nide8_login(self, server_id: str, username: str, password: str) -> str:
        return self.start_task("统一通行证登录", self._nide8_login_impl, server_id, username, password)

    def catalog_favorites(self) -> list:
        return list(CONFIG.get("catalog_favorites") or [])

    def toggle_favorite(self, item: dict) -> list:
        rows = list(CONFIG.get("catalog_favorites") or [])
        key = (str(item.get("source") or ""), str(item.get("slug") or item.get("id") or item.get("name") or ""))
        kept, found = [], False
        for r in rows:
            rk = (str(r.get("source") or ""), str(r.get("slug") or r.get("id") or r.get("name") or ""))
            if rk == key:
                found = True
                continue
            kept.append(r)
        if not found:
            kept.append({"name": item.get("name"), "source": item.get("source"),
                         "slug": item.get("slug"), "id": item.get("id")})
        CONFIG.set("catalog_favorites", kept)
        CONFIG.save()
        return kept

    def download_java(self, major: str, vendor: str = "adoptium") -> str:
        vendor = (vendor or "adoptium").strip() or "adoptium"
        if vendor != "adoptium":
            try:
                return self.install_java(int(major), vendor=vendor)
            except Exception:
                pass
        return self.start_task(f"下载 Java {major}", self._download_java_impl, major)

    def terracotta_player(self) -> str:
        acc = self.accounts.get_active()
        if acc and acc.get("name"):
            return str(acc["name"])
        return "Player"

    def terracotta_snapshot(self) -> dict:
        game_on = bool(self._game_proc and getattr(self._game_proc, "poll", lambda: 0)() is None)
        return terracotta_mod.snapshot(self.terracotta_player(), game_running=game_on)

    def terracotta_prepare(self) -> str:
        return self.start_task("准备陶瓦联机", self._terracotta_prepare_impl)

    def terracotta_host(self):
        terracotta_mod.set_scanning(self.terracotta_player())

    def terracotta_join(self, room: str):
        terracotta_mod.set_guesting(room, self.terracotta_player())

    def terracotta_idle(self):
        terracotta_mod.set_waiting()

    def terracotta_allow_firewall(self) -> str:
        return terracotta_mod.allow_firewall()

    def terracotta_open_firewall_settings(self):
        terracotta_mod.open_firewall_settings()

    def terracotta_shutdown(self):
        terracotta_mod.stop()

    def launch_game(self, instance: str, version: str, account: str,
                    username: str, memory_mb: int, width: int, height: int,
                    java: str = "自动选择", extra_game_args=None) -> str:
        task_id = self.start_task(
            f"启动游戏 {version}", self._launch_game_impl,
            instance, version, account, username, memory_mb, width, height, java,
            extra_game_args,
        )
        self._launch_task_id = task_id
        return task_id

    def build_launch_command(self, instance: str, version: str, account: str,
                              username: str, memory_mb: int, width: int, height: int,
                              java: str = "自动选择") -> str:
        """生成启动命令文本（不实际启动）。"""
        inst = self._instance(instance)
        if not version:
            raise LaunchError("请先选择版本")
        if account == "离线模式" or not account:
            acc = self.accounts.offline_account(
                username or "Player", skin=CONFIG.get("offline_skin") or "default")
        else:
            acc = self.accounts.get_account(account)
            if not acc:
                raise LaunchError(f"账号不存在: {account}")
            acc = self.accounts.ensure_valid(acc)
        props = self.accounts.launch_props(acc)
        from mclauncher import launcher
        java_exe = "自动选择" if java in ("自动选择", "") else java
        cmd, _natives, _vdir, _gdir = launcher.build_launch_command(
            inst, version, props, java_exe, memory_mb=memory_mb,
            width=width, height=height)
        from mclauncher import launch_flow, version_settings as _vs
        wrapper = str(_vs.load(inst, version).get("wrapper") or "").strip()
        if wrapper:
            cmd = launch_flow.apply_wrapper(cmd, wrapper)
        return cmd

    def start_microsoft_login(self) -> str:
        return self.start_task("微软登录", self._microsoft_login_impl)

    def uninstall_version(self, spec: str):
        if " / " in spec:
            inst_name, vid = spec.split(" / ", 1)
        else:
            inst_name, vid = CONFIG.get("default_instance", "default"), spec
        Installer(self._instance(inst_name)).uninstall_version(vid.strip())
        self._emit("ui_changed", {})

    def create_instance(self, name: str):
        Instance(name).create()
        self._emit("ui_changed", {})

    def delete_instance(self, name: str):
        Instance(name).delete()
        self._emit("ui_changed", {})

    def rename_instance(self, name: str, new_name: str):
        Instance(name).rename(new_name)
        self._emit("ui_changed", {})

    def duplicate_instance(self, name: str, new_name: str = "") -> str:
        """复制整个实例（版本、mods、config、存档）为试验副本。"""
        return self.start_task(f"复制实例 {name}", self._duplicate_instance_impl,
                               name, new_name)

    def _duplicate_instance_impl(self, progress, log, name, new_name):
        from mclauncher.instances import duplicate_instance
        log(f"复制实例 {name} …")
        out = duplicate_instance(
            name, new_name,
            on_progress=lambda done, total: progress(done, total, "复制实例文件"))
        self._emit("ui_changed", {})
        log(f"实例已复制: {name} -> {out}")
        return f"已复制为实例：{out}"

    def open_instance_folder(self, name: str):
        path = self._instance(name).path
        if os.name == "nt":
            os.startfile(path)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])

    def delete_mod(self, instance: str, filename: str, version: str = ""):
        inst = self._instance(instance)
        folder = self._mods_folder(inst, version)
        mods_mod.delete_mod(inst, filename, mods_dir=folder)
        self._emit("ui_changed", {})

    def disable_mod(self, instance: str, filename: str, version: str = "") -> str:
        inst = self._instance(instance)
        name = mods_mod.set_mod_enabled(inst, filename, False, mods_dir=self._mods_folder(inst, version))
        self._emit("ui_changed", {})
        return name

    def enable_mod(self, instance: str, filename: str, version: str = "") -> str:
        inst = self._instance(instance)
        name = mods_mod.set_mod_enabled(inst, filename, True, mods_dir=self._mods_folder(inst, version))
        self._emit("ui_changed", {})
        return name

    def _mods_folder(self, inst, version: str = ""):
        if version:
            from mclauncher import version_settings as vs
            return vs.mods_dir(inst, version)
        return inst.path / "mods"

    def get_installed_mods(self, instance: str) -> list[str]:
        return [p.name for p in mods_mod.list_instance_mods(self._instance(instance))]

    def scan_mod_conflicts(self, instance: str, version: str = "") -> dict:
        """扫描 mods：重复安装、加载器不匹配、缺依赖、互不兼容声明。"""
        from mclauncher.ai.conflict import scan_conflicts
        inst = self._instance(instance)
        mods_dir = self._mods_folder(inst, version) if version else None
        return scan_conflicts(inst, mods_dir=mods_dir)

    def get_installed_shaders(self, instance: str) -> list[str]:
        return [p.name for p in mods_mod.list_content_files(self._instance(instance), "shaderpacks")]

    def get_installed_resourcepacks(self, instance: str) -> list[str]:
        return [p.name for p in mods_mod.list_content_files(self._instance(instance), "resourcepacks")]

    def get_installed_datapacks(self, instance: str) -> list[str]:
        return [p.name for p in mods_mod.list_content_files(self._instance(instance), "datapacks")]

    def delete_shader(self, instance: str, filename: str):
        mods_mod.delete_content_file(self._instance(instance), "shaderpacks", filename)
        self._emit("ui_changed", {})

    def delete_resourcepack(self, instance: str, filename: str):
        mods_mod.delete_content_file(self._instance(instance), "resourcepacks", filename)
        self._emit("ui_changed", {})

    def delete_datapack(self, instance: str, filename: str):
        mods_mod.delete_content_file(self._instance(instance), "datapacks", filename)
        self._emit("ui_changed", {})

    def get_setting(self, key: str, default=None):
        settings = self.get_settings()
        return settings.get(key, default)

    def update_settings(self, settings: dict):
        self.save_settings(settings)

    def wait_task(self, task_id: str, timeout: float = 1800, cancelled=None) -> dict:
        import time
        start = time.time()
        while True:
            if task_id in self._task_results:
                ok, msg = self._task_results[task_id]
                return {"ok": ok, "message": msg, "task_id": task_id}
            if cancelled and cancelled():
                self.cancel_task(task_id)
                return {"ok": False, "message": "已停止", "task_id": task_id}
            if time.time() - start > timeout:
                return {"ok": False, "message": "等待任务超时", "task_id": task_id}
            time.sleep(0.3)

    def get_settings(self) -> dict:
        from mclauncher.ai.defaults import DEFAULT_GATEWAY_URL, DEFAULT_MODEL
        from mclauncher.feedback_defaults import DEFAULT_FEEDBACK_URL
        return {
            "share_libraries": bool(CONFIG.get("shared_libraries", False)),
            "share_assets": bool(CONFIG.get("shared_assets", False)),
            "download_threads": int(CONFIG.get("download_threads", 8)),
            "default_memory_mb": int(CONFIG.get("memory_mb", 4096)),
            "auto_memory": bool(CONFIG.get("auto_memory", False)),
            "default_resolution": [int(CONFIG.get("width", 854)), int(CONFIG.get("height", 480))],
            "ms_client_id": CONFIG.get("microsoft_client_id") or "",
            "curseforge_api_key": CONFIG.get("curseforge_api_key") or "",
            "ai_mode": CONFIG.get("ai_mode") or "public",
            "ai_gateway_url": CONFIG.get("ai_gateway_url") or DEFAULT_GATEWAY_URL or "",
            "ai_base_url": CONFIG.get("ai_base_url") or "",
            "ai_api_key": CONFIG.get("ai_api_key") or "",
            "ai_model": CONFIG.get("ai_model") or DEFAULT_MODEL,
            # AI 权限：run_agent 的确认分流读这两个键，桥接端也要带出去
            "ai_confirm_writes": bool(CONFIG.get("ai_confirm_writes", True)),
            "ai_permission_mode": CONFIG.get("ai_permission_mode") or "standard",
            "root": str(utils.ROOT),
            "feedback_url": CONFIG.get("feedback_url") or DEFAULT_FEEDBACK_URL or "",
            "feedback_heartbeat": bool(CONFIG.get("feedback_heartbeat", True)),
            "feedback_consent": CONFIG.get("feedback_consent") is True,
            "default_isolation": CONFIG.get("default_isolation") or "none",
            "default_jvm_args": CONFIG.get("default_jvm_args") or "",
            "update_url": CONFIG.get("update_url") or "",
            "download_source": CONFIG.get("download_source") or "auto",
            "community_source": CONFIG.get("community_source") or "auto",
            "use_system_proxy": bool(CONFIG.get("use_system_proxy", True)),
            "launcher_visibility": CONFIG.get("launcher_visibility") or "keep",
            "gc_preset": CONFIG.get("gc_preset") or "auto",
            "download_limit_kbps": int(CONFIG.get("download_limit_kbps") or 0),
            "auto_check_update": bool(CONFIG.get("auto_check_update", True)),
            "custom_homepage": CONFIG.get("custom_homepage") or "",
            "homepage_mode": CONFIG.get("homepage_mode") or "news",
            "window_mode": CONFIG.get("window_mode") or "window",
            "game_dir": str(CONFIG.instances_dir),
            "offline_skin": CONFIG.get("offline_skin") or "default",
            "default_java": CONFIG.get("default_java") or "",
            "game_lang": CONFIG.get("game_lang") or "auto",
            "ui_dark": bool(CONFIG.get("ui_dark", False)),
        }

    def save_settings(self, data: dict):
        # 严格的局部更新：只写 `data` 里真正带来的键。前端（eziapp 设置页只提交 11 个键）
        # 提交部分设置时，未提交的键必须原样保留，否则等于静默清空用户配置。
        patch = {}
        if "default_resolution" in data:
            res = data.get("default_resolution") or [854, 480]
            patch["width"] = int(res[0])
            patch["height"] = int(res[1])
        if "share_libraries" in data:
            patch["shared_libraries"] = bool(data.get("share_libraries"))
        if "share_assets" in data:
            patch["shared_assets"] = bool(data.get("share_assets"))
        if "download_threads" in data:
            patch["download_threads"] = int(data.get("download_threads") or 8)
        if "default_memory_mb" in data:
            patch["memory_mb"] = int(data.get("default_memory_mb") or 4096)
        if "auto_memory" in data:
            patch["auto_memory"] = bool(data.get("auto_memory"))
        if "ms_client_id" in data:
            patch["microsoft_client_id"] = ((data.get("ms_client_id") or "").strip()
                                            or CONFIG.get("microsoft_client_id"))
        if "curseforge_api_key" in data:
            patch["curseforge_api_key"] = (data.get("curseforge_api_key") or "").strip()
        if "ai_mode" in data:
            patch["ai_mode"] = data.get("ai_mode") or "public"
        # 地址类键各自独立判定：以前它们挂在 `"ai_mode" in data` 下面，
        # 只要前端提交了 ai_mode 就会被 data 里不存在的值覆写成空串，
        # 自定义模式随即抛「请在设置里填写自定义 NewAPI 地址」，AI 助手整个不可用。
        if "ai_gateway_url" in data:
            patch["ai_gateway_url"] = (data.get("ai_gateway_url") or "").strip()
        if "ai_base_url" in data:
            patch["ai_base_url"] = (data.get("ai_base_url") or "").strip()
        if "ai_api_key" in data:
            patch["ai_api_key"] = data.get("ai_api_key") or ""
        if "ai_model" in data:
            patch["ai_model"] = (data.get("ai_model") or CONFIG.get("ai_model") or "deepseek-v4-flash")
        if "feedback_url" in data:
            patch["feedback_url"] = (data.get("feedback_url") or "").strip()
        if "feedback_heartbeat" in data:
            patch["feedback_heartbeat"] = bool(data.get("feedback_heartbeat"))
        if "feedback_consent" in data:
            patch["feedback_consent"] = bool(data.get("feedback_consent"))
        if "default_isolation" in data:
            patch["default_isolation"] = data.get("default_isolation") or "none"
        if "default_jvm_args" in data:
            patch["default_jvm_args"] = data.get("default_jvm_args") or ""
        if "update_url" in data:
            patch["update_url"] = data.get("update_url") or ""
        if "download_source" in data:
            patch["download_source"] = data.get("download_source") or "auto"
        if "community_source" in data:
            patch["community_source"] = data.get("community_source") or "auto"
        if "use_system_proxy" in data:
            patch["use_system_proxy"] = bool(data.get("use_system_proxy"))
        for key in ("launcher_visibility", "gc_preset", "custom_homepage", "homepage_mode",
                    "window_mode", "offline_skin", "instances_dir", "default_java",
                    "game_lang"):
            if key in data:
                patch[key] = data.get(key)
        if "download_limit_kbps" in data:
            patch["download_limit_kbps"] = int(data.get("download_limit_kbps") or 0)
        if "auto_check_update" in data:
            patch["auto_check_update"] = bool(data.get("auto_check_update"))
        if "skip_assets" in data:
            patch["skip_assets"] = bool(data.get("skip_assets"))
        if "ui_dark" in data:
            patch["ui_dark"] = bool(data.get("ui_dark"))
        CONFIG.update(patch)
        CONFIG.save()

    def collect_sysinfo(self, force: bool = False, scan_system_java: bool = False) -> dict:
        from mclauncher import sysinfo as sysinfo_mod
        return sysinfo_mod.collect(force=force, scan_system_java=scan_system_java)

    def sysinfo_text(self, info=None) -> str:
        from mclauncher import sysinfo as sysinfo_mod
        return sysinfo_mod.format_text(info)

    def submit_feedback(self, category: str, title: str, body: str, contact: str = "",
                        include_sysinfo: bool = True) -> dict:
        from mclauncher import feedback as fb
        return fb.submit(
            category=category, title=title, body=body, contact=contact,
            include_sysinfo=include_sysinfo)

    def submit_crash_feedback(self, report: dict, extra: str = "") -> dict:
        from mclauncher import feedback as fb
        return fb.submit_crash(report, extra)

    def feedback_history(self) -> list:
        from mclauncher import feedback as fb
        return fb.history()

    def help_articles(self, query: str = "") -> list:
        from mclauncher import help_content as hc
        return hc.search_articles(query)

    def help_article(self, article_id: str) -> dict:
        from mclauncher import help_content as hc
        return hc.get_article(article_id) or {}

    def get_accounts(self) -> list[str]:
        names = ["离线模式"]
        for acc in self.accounts.accounts:
            name = acc.get("name")
            if name and name not in names:
                names.append(name)
        return names

    def get_account_rows(self) -> list[dict]:
        from mclauncher import skin as skin_mod
        rows = []
        for acc in self.accounts.accounts:
            rows.append({
                "name": acc.get("name") or "",
                "type": acc.get("type") or "offline",
                "uuid": acc.get("uuid") or "",
                "api": acc.get("api") or "",
                "avatar": skin_mod.avatar_url(acc),
                "body": skin_mod.body_url(acc),
                "active": acc.get("name") == self.accounts.active,
            })
        return rows

    # ---- 皮肤管理（与 app.backend.BackendAPI 对齐）

    def _ms_account(self, account_name: str) -> dict:
        from mclauncher.auth import AuthError
        acc = self.accounts.get_account(account_name)
        if not acc:
            raise AuthError(f"账号不存在: {account_name}")
        if acc.get("type") != "microsoft":
            raise AuthError("只有微软正版账号支持在启动器内更换皮肤。")
        return self.accounts.ensure_valid(acc)

    def get_skin_profile(self, account_name: str) -> dict:
        from mclauncher import skin as skin_mod
        acc = self._ms_account(account_name)
        return skin_mod.summarize_profile(skin_mod.fetch_profile(acc["access_token"]))

    def upload_skin(self, account_name: str, file_path: str, variant: str = "classic") -> dict:
        from mclauncher import skin as skin_mod
        acc = self._ms_account(account_name)
        return skin_mod.summarize_profile(
            skin_mod.upload_skin(acc["access_token"], file_path, variant))

    def reset_skin(self, account_name: str) -> dict:
        from mclauncher import skin as skin_mod
        acc = self._ms_account(account_name)
        return skin_mod.summarize_profile(skin_mod.reset_skin(acc["access_token"]))

    def set_cape(self, account_name: str, cape_id: str = "") -> dict:
        from mclauncher import skin as skin_mod
        acc = self._ms_account(account_name)
        return skin_mod.summarize_profile(
            skin_mod.set_cape(acc["access_token"], cape_id))

    def skin_site_url(self, account_name: str) -> str:
        from mclauncher import skin as skin_mod
        return skin_mod.skin_site_url(self.accounts.get_account(account_name))

    # ---- 离线账户皮肤（本地皮肤服务 + authlib-injector，进游戏可见）

    def _offline_account(self, account_name: str) -> dict:
        from mclauncher.auth import AuthError
        acc = self.accounts.get_account(account_name)
        if not acc:
            raise AuthError(f"账号不存在: {account_name}")
        if (acc.get("type") or "offline") != "offline":
            raise AuthError("只有离线账号支持本地皮肤。")
        return acc

    def get_offline_skin(self, account_name: str) -> dict:
        acc = self._offline_account(account_name)
        return {
            "model": acc.get("skin_model") or "default",
            "skin_file": acc.get("skin_file") or "",
            "cape_file": acc.get("cape_file") or "",
            "has_skin": bool(acc.get("skin_file") or acc.get("cape_file")),
        }

    def set_offline_skin(self, account_name: str, skin_path: str = "",
                         model: str = "", cape_path: str = "") -> dict:
        from mclauncher import offline_skin
        acc = self._offline_account(account_name)
        uid = (acc.get("uuid") or "").replace("-", "") or acc.get("name") or "player"
        if skin_path:
            acc["skin_file"] = offline_skin.store_skin_file(skin_path, uid, "skin")
        if cape_path:
            acc["cape_file"] = offline_skin.store_skin_file(cape_path, uid, "cape")
        if model:
            acc["skin_model"] = "slim" if str(model).lower() in ("slim", "alex") else "default"
        self.accounts.save()
        return self.get_offline_skin(account_name)

    def clear_offline_skin(self, account_name: str) -> dict:
        acc = self._offline_account(account_name)
        for key in ("skin_file", "cape_file"):
            path = acc.pop(key, "") or ""
            if path:
                try:
                    Path(path).unlink(missing_ok=True)
                except OSError:
                    pass
        acc.pop("skin_model", None)
        self.accounts.save()
        return self.get_offline_skin(account_name)

    def fetch_offline_skin_premium(self, account_name: str, player_name: str) -> dict:
        from mclauncher import offline_skin
        acc = self._offline_account(account_name)
        uid = (acc.get("uuid") or "").replace("-", "") or acc.get("name") or "player"
        data = offline_skin.fetch_premium_skin(player_name, uid)
        acc["skin_file"] = data["skin_file"]
        acc["skin_model"] = data["skin_model"]
        if data.get("cape_file"):
            acc["cape_file"] = data["cape_file"]
        else:
            acc.pop("cape_file", None)
        self.accounts.save()
        return self.get_offline_skin(account_name)

    def ping_server(self, address: str, port: int = 0) -> dict:
        from mclauncher import server_ping
        return server_ping.ping_address(address, port=port)

    def join_server(self, instance: str, ip: str, port: int = 25565) -> str:
        """一键启动并直连服务器（1.20+ 走 Quick Play，老版本回退 --server）。"""
        ip = (ip or "").strip()
        if not ip:
            raise LaunchError("服务器地址为空")
        inst = self._instance(instance)
        installed = list(self.get_installed_versions(inst.name) or [])
        if not installed:
            raise LaunchError("该实例还没有安装版本，请先到「版本」页安装")
        from mclauncher import playtime as playtime_mod
        version = ""
        for sess in reversed(playtime_mod.get_playtime(inst.name).get("sessions") or []):
            if sess.get("version") in installed:
                version = sess["version"]
                break
        version = version or installed[0]
        account = self.accounts.active or "离线模式"
        return self.launch_game(
            instance=inst.name, version=version, account=account,
            username="", memory_mb=int(CONFIG.get("memory_mb") or 4096),
            width=int(CONFIG.get("width") or 854),
            height=int(CONFIG.get("height") or 480),
            extra_game_args=["--server", ip, "--port", str(int(port or 25565))],
        )

    def remove_account(self, name: str):
        self.accounts.remove_account(name)
        self._emit("ui_changed", {})

    def set_active_account(self, name: str):
        self.accounts.set_active(name)
        self._emit("ui_changed", {})
        return self.accounts.active

    def add_offline_account(self, username: str):
        acc = self.accounts.offline_account(username)
        self.accounts.add_account({**acc, "type": "offline"})
        self._emit("ui_changed", {})
        return acc["name"]

    def start_authlib_login(self, api: str, username: str, password: str) -> str:
        return self.start_task("皮肤站登录", self._authlib_login_impl, api, username, password)

    def get_version_settings(self, instance: str, version: str) -> dict:
        from mclauncher import version_settings as vs
        return vs.load(self._instance(instance), version)

    def save_version_settings(self, instance: str, version: str, data: dict) -> dict:
        from mclauncher import version_settings as vs
        out = vs.save(self._instance(instance), version, data or {})
        self._emit("ui_changed", {})
        return out

    def repair_version(self, instance: str, version: str) -> str:
        return self.start_task(f"修复 {version}", self._repair_impl, instance, version)

    def preflight_launch(self, instance: str = "", version: str = "",
                         memory_mb: int = 0, java: str = "") -> dict:
        from mclauncher import preflight as preflight_mod
        from mclauncher.instances import JAVA_AUTO
        java_exe = ""
        if java and java not in (JAVA_AUTO, "auto", "default", ""):
            java_exe = str(java)
        return preflight_mod.check_launch(
            self._instance(instance or ""), version or "",
            memory_mb=int(memory_mb or 0), java_exe=java_exe,
        )

    def apply_crash_action(self, action: dict | None = None, report: dict | None = None) -> dict:
        action = action or {}
        report = report or {}
        aid = (action.get("id") or "").strip()
        instance = (action.get("instance") or report.get("instance")
                    or CONFIG.get("default_instance") or "default")
        version = (action.get("version") or report.get("version") or "")

        if aid == "disable_mods":
            mods = list(action.get("mods") or [])
            done, failed = [], []
            for name in mods:
                try:
                    self.disable_mod(instance, name, version)
                    done.append(name)
                except Exception as exc:
                    failed.append(f"{name}: {exc}")
            if not done and failed:
                return {"ok": False, "message": "未能禁用：" + "; ".join(failed)}
            msg = f"已禁用 {len(done)} 个 Mod"
            if failed:
                msg += "；部分失败：" + "; ".join(failed)
            return {"ok": True, "message": msg}

        if aid == "repair_version":
            if not version:
                return {"ok": False, "message": "报告里没有版本号，无法修复"}
            tid = self.repair_version(instance, version)
            return {"ok": True, "message": f"已开始修复 {version}", "task_id": tid}

        if aid == "need_java":
            major = int(action.get("major") or 17)
            tid = self.download_java(str(major), vendor="adoptium")
            return {"ok": True, "message": f"已开始下载 Java {major}", "task_id": tid}

        if aid == "bump_memory":
            mb = int(action.get("memory_mb") or 6144)
            mb = max(1024, min(32768, mb))
            CONFIG.set("memory_mb", mb)
            CONFIG.save()
            self._emit("ui_changed", {})
            return {"ok": True, "message": f"默认内存已设为 {mb} MB"}

        if aid == "open_mods_folder":
            from mclauncher.crash import open_path
            inst = self._instance(instance)
            folder = getattr(self, "_mods_folder", None)
            if callable(folder):
                path = folder(inst, version)
            else:
                path = inst.path / "mods"
            path.mkdir(parents=True, exist_ok=True)
            open_path(path)
            return {"ok": True, "message": "已打开 Mods 文件夹"}

        if aid == "open_crash_file":
            from pathlib import Path as _P
            from mclauncher.crash import open_path
            target = (action.get("path") or report.get("direct_file") or "").strip()
            if not target or not _P(target).is_file():
                return {"ok": False, "message": "没有可打开的崩溃文件"}
            open_path(target)
            return {"ok": True, "message": "已打开崩溃报告"}

        if aid == "open_gpu_hint":
            return {
                "ok": True,
                "message": (
                    "显卡/OpenGL 相关崩溃：请更新显卡驱动，关闭独显强制、"
                    "超采样/滤镜，并确认不是远程桌面/虚拟机缺 OpenGL。"
                ),
            }

        if aid == "reset_jvm_args":
            CONFIG.set("default_jvm_args", "")
            CONFIG.save()
            try:
                from mclauncher import version_settings as vs
                inst = self._instance(instance)
                if version:
                    data = vs.load(inst, version)
                    data["jvm_args"] = ""
                    vs.save(inst, version, data)
            except Exception:
                pass
            self._emit("ui_changed", {})
            return {"ok": True, "message": "已清空自定义 JVM 参数"}

        return {"ok": False, "message": f"未知动作: {aid}"}

    def export_modpack(self, instance: str, dest: str = "", fmt: str = "mrpack") -> str:
        """导出整合包。fmt: "mrpack"（Modrinth）或 "curseforge"（manifest.json zip）。"""
        return self.start_task(
            f"导出整合包 {instance}", self._export_pack_impl, instance, dest, fmt)

    def check_modpack_update(self, instance: str) -> dict:
        """检查实例整合包是否有新版本（Modrinth / CurseForge）。"""
        dm = DownloadManager(threads=2)
        return modpack_mod.check_modpack_update(
            dm, self._instance(instance), api_key=CONFIG.get("curseforge_api_key"))

    def update_modpack(self, instance: str) -> str:
        """把实例整合包升级到最新版本（重装文件并清理旧版残留 mods）。"""
        return self.start_task(f"更新整合包 {instance}", self._update_modpack_impl, instance)

    def start_mod_updates(self, instance: str) -> str:
        return self.start_task(f"检查模组更新 {instance}", self._mod_update_impl, instance)

    def cleaner_preview(self) -> dict:
        from mclauncher import cleaner as cleaner_mod
        return cleaner_mod.preview()

    def cleaner_apply(self, kinds=None) -> dict:
        from mclauncher import cleaner as cleaner_mod
        return cleaner_mod.apply(kinds)

    def check_update(self) -> dict:
        from mclauncher import updater as updater_mod
        return updater_mod.check()

    def fetch_news(self) -> list:
        from mclauncher import news as news_mod
        return news_mod.fetch()

    def cached_news(self) -> list:
        from mclauncher import news as news_mod
        return news_mod.load_cached()

    def lan_hint(self, port: int = 25565) -> str:
        from mclauncher import lan as lan_mod
        return lan_mod.lan_hint(port)

    def authlib_presets(self) -> list:
        from mclauncher.authlib import PRESETS
        return [{"name": a, "api": b} for a, b in PRESETS]

    def get_installed_mod_entries(self, instance: str, version: str = "") -> list:
        inst = self._instance(instance)
        if version:
            return mods_mod.list_mod_entries_at(self._mods_folder(inst, version))
        return mods_mod.list_instance_mod_entries(inst)

    def open_global_mods(self):
        from mclauncher import global_mods as gm
        path = gm.root()
        utils.ensure_dir(path)
        if os.name == "nt":
            os.startfile(path)
        else:
            subprocess.Popen(["xdg-open", str(path)])

    def start_self_update(self) -> str:
        return self.start_task("更新启动器", self._self_update_impl)

    def get_version_list(self) -> list[dict]:
        cached = utils.read_json(utils.ROOT / "cache" / "version_manifest.json", None) or {}
        versions = {
            v["id"]: v for v in cached.get("versions", [])
            if isinstance(v, dict) and v.get("id")
        }
        return self._version_rows(versions)

    def fetch_version_list(self) -> list[dict]:
        dm = DownloadManager(threads=2)
        versions = manifest_mod.list_remote_versions(dm) or {}
        return self._version_rows(versions)

    def _version_rows(self, versions) -> list[dict]:
        rows = []
        for vid, v in (versions or {}).items():
            raw = v.get("type") or "snapshot"
            if raw == "release":
                vtype = "release"
            elif raw in ("old_alpha", "old_beta"):
                vtype = raw
            else:
                vtype = "snapshot"
            rows.append({
                "version": vid,
                "type": vtype,
                "date": str(v.get("releaseTime") or v.get("time") or "")[:10],
            })
        rows.sort(key=lambda r: r["date"], reverse=True)
        return rows

    def get_installed_versions(self, instance: str, include_hidden: bool = False) -> list[str]:
        from mclauncher import version_settings as vs
        if instance:
            inst = self._instance(instance)
            ids = inst.installed_ids()
            if include_hidden or CONFIG.get("show_hidden_versions"):
                return ids
            return [vid for vid in ids if not vs.load(inst, vid).get("hidden")]
        out = []
        for name in list_instances():
            for vid in Instance(name).installed_ids():
                out.append(f"{name} / {vid}")
        return out

    def get_instances(self) -> list[dict]:
        self._ensure_default_instance()
        rows = []
        for name in list_instances():
            inst = Instance(name)
            ids = inst.installed_ids()
            meta = inst.meta() or {}
            pack = meta.get("modpack") if isinstance(meta.get("modpack"), dict) else {}
            pack_name = pack.get("name") if pack else None
            mc = pack_name or meta.get("mc_version") or (ids[0] if ids else "未安装版本")
            rows.append({
                "name": name,
                "versions": len(ids),
                "mc": str(mc),
                "pack": pack_name or "",
                "pack_version": (pack.get("version") if pack else "") or "",
                "mc_version": (pack.get("mc_version") if pack else None) or meta.get("mc_version") or "",
                "java": inst.java_pref(),
                "java_label": self.instance_java_label(name),
            })
        return rows

    def _modpack_row(self, hit: dict, default_source: str = "") -> dict:
        src = (hit.get("source") or default_source or "").lower()
        return {
            "name": hit.get("title") or hit.get("name") or "?",
            "author": hit.get("author") or "?",
            "downloads": int(hit.get("downloads") or 0),
            "id": hit.get("id"),
            "slug": hit.get("slug"),
            "source": src or default_source,
            "description": hit.get("description") or "",
        }

    def search_modpacks(self, query: str, source: str) -> list[dict]:
        src = "curseforge" if (source or "").lower().startswith("curse") else "modrinth"
        q = (query or "").strip()
        if not q:
            rows = []
            seen = set()
            for title, pack_src, key, slug in POPULAR_MODPACKS:
                if pack_src != src and pack_src == "modrinth":
                    continue
                if pack_src != src and key != CBC_CF_ID:
                    continue
                row = {
                    "name": title,
                    "author": "CurseForge" if pack_src == "curseforge" else "Modrinth",
                    "downloads": 0,
                    "id": key if pack_src == "curseforge" else None,
                    "slug": slug if pack_src == "curseforge" else key,
                    "source": pack_src,
                    "description": "Forge 1.20.1 黄铜协奏曲，不是 Create+/CDC" if key == CBC_CF_ID else "",
                }
                mark = (row["source"], row["id"] or row["slug"])
                if mark in seen:
                    continue
                seen.add(mark)
                if key == CBC_CF_ID:
                    rows.insert(0, row)
                else:
                    rows.append(row)
            self._pack_cache = rows
            return rows
        dm = DownloadManager(threads=2)
        key = CONFIG.get("curseforge_api_key")
        hits = []
        try:
            hits = modpack_mod.search_modpacks_chinese(dm, q, limit=25, api_key=key)
        except Exception:
            hits = []
        if hits and any(h.get("matched_alias") for h in hits):
            rows = [self._modpack_row(h, src) for h in hits]
            self._pack_cache = rows
            return rows
        if not hits:
            try:
                if src == "curseforge":
                    hits = modpack_mod.search_cf_modpacks(dm, q, limit=25, api_key=key)
                else:
                    hits = modpack_mod.modrinth_search(dm, q, limit=25)
            except Exception:
                hits = []
        else:
            hits = sorted(
                hits,
                key=lambda h: 0 if (h.get("source") or src) == src else 1,
            )
        rows = [self._modpack_row(h, src) for h in hits]
        self._pack_cache = rows
        return rows

    def search_mods(self, query: str, source: str, extra: dict | None = None) -> list[dict]:
        src = "curseforge" if (source or "").lower().startswith("curse") else "modrinth"
        q = (query or "").strip()
        if not q:
            rows = []
            for title, mod_src, key, *_rest in POPULAR_MODS:
                if mod_src != src:
                    continue
                rows.append({
                    "name": title,
                    "author": "CurseForge" if mod_src == "curseforge" else "Modrinth",
                    "downloads": 0,
                    "id": key if mod_src == "curseforge" else None,
                    "slug": None if mod_src == "curseforge" else key,
                    "source": mod_src,
                })
            self._mod_cache = rows
            return rows
        dm = DownloadManager(threads=2)
        extra = extra or {}
        gv = extra.get("game_version") or extra.get("version") or ""
        if isinstance(gv, str) and gv.startswith("全部"):
            gv = ""
        from mclauncher.catalog_files import category_facets
        cats = category_facets(extra.get("category") or extra.get("type") or "")
        try:
            if src == "curseforge":
                hits = mods_mod.search_curseforge(
                    dm, q, limit=30, api_key=CONFIG.get("curseforge_api_key"),
                    class_id=mods_mod.CF_CLASS_MOD, game_version=gv or None)
            else:
                hits = mods_mod.search_mods(dm, q, limit=30, game_version=gv or None, categories=cats)
        except Exception:
            hits = []
        rows = []
        for h in hits:
            rows.append({
                "name": h.get("title") or h.get("name") or "?",
                "author": h.get("author") or "?",
                "downloads": int(h.get("downloads") or 0),
                "id": h.get("id"),
                "slug": h.get("slug"),
                "source": h.get("source") or src,
                "description": h.get("description") or h.get("summary") or "",
                "tags": h.get("tags") or [],
                "updated": h.get("updated") or "",
            })
        self._mod_cache = rows
        return rows

    def _content_row(self, hit: dict, default_source: str = "") -> dict:
        src = hit.get("source") or default_source
        return {
            "name": hit.get("title") or hit.get("name") or "?",
            "author": hit.get("author") or "?",
            "downloads": int(hit.get("downloads") or 0),
            "id": hit.get("id"),
            "slug": hit.get("slug"),
            "source": src,
            "description": hit.get("description") or hit.get("summary") or "",
            "tags": hit.get("tags") or [],
            "updated": hit.get("updated") or "",
        }

    def _search_content(self, kind: str, query: str, source: str, extra: dict | None = None) -> list[dict]:
        spec = mods_mod.CONTENT_KINDS[kind]
        src = (source or "").lower()
        extra = extra or {}
        want_mr = src in ("", "全部", "all", "modrinth")
        want_cf = src in ("", "全部", "all") or src.startswith("curse")
        if src.startswith("modrinth"):
            want_cf = False
        if src.startswith("curse"):
            want_mr = False
        dm = DownloadManager(threads=2)
        rows = []
        q = (query or "").strip()
        gv = extra.get("game_version") or extra.get("version") or ""
        if isinstance(gv, str) and gv.startswith("全部"):
            gv = ""
        from mclauncher.catalog_files import category_facets
        cats = category_facets(extra.get("category") or extra.get("type") or "")
        if want_mr:
            try:
                hits = mods_mod.search_modrinth_projects(
                    dm, q, spec["mr"], limit=30, game_version=gv or None, categories=cats)
                rows.extend(self._content_row(h, "modrinth") for h in hits)
            except Exception:
                pass
        if want_cf:
            try:
                hits = mods_mod.search_curseforge(
                    dm, q or None, limit=30,
                    api_key=CONFIG.get("curseforge_api_key"),
                    class_id=spec["cf"],
                    game_version=gv or None,
                )
                for h in hits:
                    row = self._content_row(h, "curseforge")
                    row["description"] = h.get("summary") or row["description"]
                    rows.append(row)
            except Exception:
                pass
        return rows

    def search_shaders(self, query: str, source: str, extra: dict | None = None) -> list[dict]:
        return self._search_content("shader", query, source, extra)

    def search_resourcepacks(self, query: str, source: str, extra: dict | None = None) -> list[dict]:
        return self._search_content("resourcepack", query, source, extra)

    def search_datapacks(self, query: str, source: str, extra: dict | None = None) -> list[dict]:
        return self._search_content("datapack", query, source, extra)

    def get_java_list(self, scan_system: bool = False) -> list[dict]:
        javas = java_mod.all_javas() if scan_system else (
            java_mod.list_installed_javas() + java_mod.custom_javas())
        rows = []
        for j in javas:
            rows.append({
                "name": j.get("name") or f"Java {j.get('major')}",
                "major": str(j.get("major") or "?"),
                "path": j.get("exe") or j.get("path") or "",
                "custom": bool(j.get("custom")),
            })
        return rows

    def add_java_path(self, path: str) -> dict:
        entry = java_mod.add_custom_java(path)
        self._emit("ui_changed", {})
        return entry

    def remove_java_path(self, path: str) -> bool:
        out = java_mod.remove_custom_java(path)
        if out:
            self._emit("ui_changed", {})
        return out

    def normalize_java_pref(self, java: str) -> str:
        if not java or java in (JAVA_AUTO, "auto", "default"):
            return JAVA_AUTO
        for j in java_mod.all_javas():
            if j.get("name") == java or j.get("exe") == java:
                return j.get("exe") or java
        p = Path(java)
        if p.is_file():
            return str(p)
        return java

    def get_instance_java(self, name: str) -> str:
        return self._instance(name).java_pref()

    def set_instance_java(self, name: str, java: str):
        self._instance(name).set_java_pref(self.normalize_java_pref(java))

    def java_combo_options(self, instance: str, scan_system: bool = False) -> list[dict]:
        opts = [{"label": JAVA_AUTO, "value": JAVA_AUTO}]
        seen = set()
        for j in self.get_java_list(scan_system=scan_system):
            exe = j.get("path") or ""
            if not exe or exe in seen:
                continue
            seen.add(exe)
            opts.append({"label": j.get("name") or exe, "value": exe})
        stored = self.get_instance_java(instance)
        if stored != JAVA_AUTO and stored not in seen:
            opts.append({"label": f"已保存 ({stored})", "value": stored})
        return opts

    def java_combo_label_for(self, instance: str, options=None) -> str:
        stored = self.get_instance_java(instance)
        for o in options or self.java_combo_options(instance):
            if o["value"] == stored:
                return o["label"]
        return JAVA_AUTO

    def instance_java_label(self, name: str) -> str:
        stored = self.get_instance_java(name)
        if stored == JAVA_AUTO:
            return JAVA_AUTO
        for j in java_mod.all_javas():
            if j.get("exe") == stored:
                return f"Java {j.get('major') or '?'}"
        return Path(stored).name

    def _install_game_impl(self, progress, log, version, loader="无", loader_version="", instance="", extra=None):
        extra = dict(extra or {})
        extra.setdefault("skip_assets", bool(CONFIG.get("skip_assets")))
        inst = self._instance(instance)
        dm = self._dm(progress, log)
        installer = Installer(
            inst, dm,
            on_progress=dm.on_progress,
            cancel=dm.cancel,
        )
        log(f"安装到实例 {inst.name}")
        from mclauncher.game_install import install_game
        vid = install_game(installer, version, loader, loader_version, extra)
        log(f"版本安装完成: {vid}")
        iso = CONFIG.get("default_isolation") or "none"
        if iso and iso != "none":
            from mclauncher import version_settings as vs
            vs.save(inst, vid, {"isolation": iso})
            log(f"已套用默认隔离: {iso}")
        return f"已安装 {vid}"

    def _install_modpack_impl(self, progress, log, name, source, extra=None):
        extra = extra or {}
        inst = self._instance(extra.get("instance"))
        dm = self._dm(progress, log)
        path = extra.get("path") or name
        on_progress = dm.on_progress
        src_l = (source or "").lower()
        log("整合包安装引擎：按声明的 Forge/Fabric 版本直装（不依赖残缺的 Maven 列表）")

        if src_l.startswith("本地") or Path(str(path)).is_file():
            p = Path(path)
            log(f"从本地文件安装: {p}")
            log(f"实例: {inst.name}  路径: {inst.path}")
            if p.suffix.lower() == ".mrpack":
                meta = modpack_mod.install_mrpack(dm, str(p), inst, on_progress=on_progress, cancel=dm.cancel)
            else:
                meta = modpack_mod.install_cf_zip(dm, str(p), inst, on_progress=on_progress, cancel=dm.cancel)
        elif src_l.startswith("curse"):
            hit = extra if extra.get("id") or extra.get("slug") else self._lookup_pack(name, source)
            addon_id = hit.get("id")
            slug = hit.get("slug")
            if not addon_id and not slug:
                raise RuntimeError(f"无法解析整合包: {name}")
            log(f"从 CurseForge 安装 {hit.get('name') or name} (id={addon_id} slug={slug})")
            log(f"实例: {inst.name}  路径: {inst.path}")
            if str(addon_id) == str(CBC_CF_ID) or (slug or "") == CBC_CF_SLUG:
                log("目标包：机械动力：黄铜协奏曲（CBC），Minecraft 1.20.1 Forge。这不是 Create+ / CDC。")
            elif str(addon_id) == str(CDC_CF_ID) or (slug or "") == CDC_CF_SLUG:
                log("目标包：机械动力：齿轮盛宴（CDC），Minecraft 1.20.1 Forge。")
            existing = (inst.meta() or {}).get("modpack")
            if isinstance(existing, dict) and existing.get("name"):
                log(f"注意：实例 {inst.name} 当前已是 {existing.get('name')} "
                    f"{existing.get('version') or ''} / {existing.get('mc_version') or ''}。"
                    "覆盖安装会混入旧模组，建议先新建实例再装。")
            meta = modpack_mod.install_cf_modpack(
                dm, addon_id, inst,
                api_key=CONFIG.get("curseforge_api_key"),
                on_progress=on_progress, cancel=dm.cancel, cf_slug=slug,
                file_id=extra.get("file_id") or extra.get("version_id"),
            )
        else:
            hit = extra if extra.get("slug") else self._lookup_pack(name, source)
            slug = hit.get("slug") or name
            log(f"从 Modrinth 安装 {hit.get('name') or slug} ({slug})")
            log(f"实例: {inst.name}  路径: {inst.path}")
            meta = modpack_mod.install_mrpack_by_slug(
                dm, slug, inst, on_progress=on_progress, cancel=dm.cancel,
                version_id=extra.get("version_id"))
        if isinstance(meta, dict) and meta.get("instance"):
            CONFIG.set("default_instance", meta["instance"])
            CONFIG.save()
        log(f"整合包安装完成: {(meta or {}).get('name') or name}")
        manual = (meta or {}).get("manual_mods") or []
        if manual:
            return f"整合包已安装，但 {len(manual)} 个 Mod 因作者限制需手动下载，链接见任务日志"

    def _install_mod_impl(self, progress, log, name, instance, extra=None):
        extra = extra or {}
        inst = self._instance(instance or extra.get("instance"))
        dm = self._dm(progress, log)
        on_progress = dm.on_progress
        src_kind = (extra.get("source") or "").lower()
        vid = extra.get("version_id")
        fid = extra.get("file_id")
        gv = extra.get("game_version") or extra.get("mc_version")
        if extra.get("path") or extra.get("url"):
            source = extra.get("path") or extra.get("url")
            log(f"安装模组: {source}")
            mods_mod.install_mod_from_source(dm, str(source), inst, on_progress=on_progress,
                                             version_id=vid)
        elif src_kind.startswith("curse") and extra.get("id"):
            log(f"从 CurseForge 安装模组 id={extra.get('id')}")
            mods_mod.install_curseforge_mod(
                dm, extra["id"], inst, mc_version=gv, on_progress=on_progress, file_id=fid)
        else:
            hit = extra if extra.get("slug") else self._lookup_mod(str(name), extra.get("source") or "Modrinth")
            if hit.get("id") and str(hit.get("source") or src_kind).lower().startswith("curse"):
                log(f"从 CurseForge 安装模组 id={hit.get('id')}")
                mods_mod.install_curseforge_mod(
                    dm, hit["id"], inst, mc_version=gv, on_progress=on_progress,
                    file_id=fid or extra.get("version_id"))
            else:
                slug = hit.get("slug") or name
                log(f"从 Modrinth 安装模组 {slug}")
                mods_mod.install_mod_from_source(
                    dm, str(slug), inst, mc_version=gv, on_progress=on_progress, version_id=vid)
        log("模组安装完成")

    def _install_content_impl(self, progress, log, kind, name, instance, extra=None):
        extra = dict(extra or {})
        extra.setdefault("name", name)
        extra.setdefault("slug", extra.get("slug") or name)
        inst = self._instance(instance or extra.get("instance"))
        spec = mods_mod.CONTENT_KINDS[kind]
        dm = self._dm(progress, log)
        log(f"安装到 {inst.name}/{spec['subdir']}")
        result = mods_mod.install_content_from_source(
            dm, inst, spec["subdir"], extra=extra, on_progress=dm.on_progress)
        files = (result or {}).get("files") or []
        log(f"完成: {', '.join(files) or name}")
        if kind == "datapack":
            log("数据包已放到实例 datapacks 目录，请复制到对应存档的 datapacks 文件夹后进入世界。")

    def _download_java_impl(self, progress, log, major):
        dm = self._dm(progress, log)
        log(f"下载 Adoptium Java {major}")
        exe = java_mod.install_adoptium(
            dm, int(major),
            on_progress=dm.on_progress,
        )
        log(f"Java {major} 就绪: {exe}")

    def _terracotta_prepare_impl(self, progress, log):
        dm = self._dm(progress, log)
        terracotta_mod.install(dm, log=log)
        progress(1, 1, "启动内核")
        terracotta_mod.start(log=log)
        return "陶瓦联机已就绪"

    def _launch_game_impl(self, progress, log, instance, version, account,
                          username, memory_mb, width, height, java="自动选择",
                          extra_game_args=None):
        if not version:
            raise LaunchError("请先选择版本（到「版本」页安装）")
        # 多开检查
        allow_multi = bool(CONFIG.get("allow_multi_instance", False))
        if not allow_multi and self.is_game_running():
            raise LaunchError("游戏正在运行中\n若要同时运行多个游戏，请到设置开启「允许多开」")

        from mclauncher import preflight as preflight_mod
        java_exe_hint = ""
        if java and java != JAVA_AUTO:
            java_exe_hint = self.normalize_java_pref(java) if hasattr(self, "normalize_java_pref") else str(java)
            if java_exe_hint == JAVA_AUTO:
                java_exe_hint = ""
        pf = preflight_mod.check_launch(
            self._instance(instance), version,
            memory_mb=int(memory_mb or 0), java_exe=java_exe_hint or "",
        )
        for it in pf.get("items") or []:
            lvl = it.get("level")
            if lvl in ("error", "warn"):
                log(f"[预检:{lvl}] {it.get('title')}: {it.get('detail')}")
        if not pf.get("ok", True):
            errs = [it for it in (pf.get("items") or []) if it.get("level") == "error"]
            msg = "\n\n".join(f"· {e.get('title')}\n{e.get('detail')}" for e in errs) or "启动预检未通过"
            raise LaunchError("启动预检未通过\n\n" + msg)

        inst = self._instance(instance)
        log(f"实例: {inst.name} | 版本: {version}")
        log(f"实例 Java 设置: {inst.java_pref()}")
        CONFIG.set("default_instance", inst.name)
        CONFIG.save()
        from mclauncher import version_settings as vs
        bound = vs.load(inst, version).get("login_account") or ""
        if bound:
            account = bound
            log(f"该版本绑定账号: {bound}")
        if account == "离线模式" or not account:
            acc = self.accounts.offline_account(
                username or "Player", skin=CONFIG.get("offline_skin") or "default")
            # 同名离线账号配过本地皮肤时，快速启动路径也带上
            stored = self.accounts.get_account(acc.get("name"))
            if stored and (stored.get("type") or "offline") == "offline":
                for key in ("skin_file", "skin_model", "cape_file"):
                    if stored.get(key):
                        acc[key] = stored[key]
        else:
            acc = self.accounts.get_account(account)
            if not acc:
                raise LaunchError(f"账号不存在: {account}")
            acc, auth_fallback = self.accounts.ensure_valid_or_fallback(acc)
            if auth_fallback:
                log(f"账号令牌刷新失败：{auth_fallback}")
                log("已改用离线身份启动（保留原用户名与 UUID，单机可正常游玩；"
                    "进正版验证服务器会被拒绝，网络恢复后重新启动即可恢复正版登录）。")
        props = self.accounts.launch_props(acc)
        kind = "正版" if props.get("user_type") == "msa" else (
            "皮肤站" if props.get("authlib_api") else (
                "统一通行证" if props.get("nide8_id") else "离线"))
        log(f"账号: {props.get('name')} ({kind})")
        log(f"内存: {memory_mb} MB | 分辨率: {width}x{height}")

        from mclauncher import launch_flow
        prep = launch_flow.prepare(inst, version, extra_game_args=extra_game_args, memory_mb=memory_mb)
        memory_mb = prep["memory_mb"] or memory_mb
        if prep.get("memory_source") == "auto":
            log(f"自动分配内存: {memory_mb} MB（按当前可用物理内存计算）")
        elif prep.get("memory_source") == "version":
            log(f"版本设置内存: {memory_mb} MB")
        extra_game_args = prep["extra_game_args"]
        game_dir = prep["game_dir"]
        if prep.get("game_lang"):
            log(f"首次启动：游戏语言已自动设为 {prep['game_lang']}（可在设置或游戏内修改）")
        launch_flow.run_hook(
            prep["settings"].get("pre_launch") or "", game_dir, log=log,
            wait=bool(prep.get("pre_launch_wait", True)))

        progress(1, 4, "检查 Java")
        vjson = inst.version_json(version) or {}
        try:
            resolved = manifest_mod.resolve_inherits(vjson, lambda pid: inst.version_json(pid))
        except Exception:
            resolved = vjson
        prefer = None
        java_choice = java
        if prep["settings"].get("java") and prep["settings"]["java"] != JAVA_AUTO:
            java_choice = prep["settings"]["java"]
        if not java_choice or java_choice == JAVA_AUTO:
            java_choice = inst.java_pref()
        if not java_choice or java_choice == JAVA_AUTO:
            # 全局默认 Java：版本设置与实例偏好都是「自动」时才生效。
            java_choice = CONFIG.get("default_java") or ""
        if java_choice and java_choice != JAVA_AUTO:
            for j in java_mod.all_javas():
                if j.get("name") == java_choice or j.get("exe") == java_choice:
                    prefer = j.get("exe")
                    break
            if not prefer and Path(java_choice).is_file():
                prefer = java_choice
        need = java_mod.required_java_major(resolved)
        java_exe = java_mod.resolve_launch_java(resolved, prefer=prefer, on_note=log)
        if not java_mod.java_usable_for(resolved, java_exe):
            log(f"未找到 Java {need}，自动下载中…")
            dm = self._dm(progress, log)
            java_exe = java_mod.resolve_launch_java(
                resolved, prefer=None, dm=dm,
                on_progress=dm.on_progress, on_note=log,
            )
        ver_line = next((ln.strip() for ln in (java_mod.java_version_output(java_exe) or "").splitlines() if ln.strip()), "?")
        log(f"Java -version: {ver_line}")
        log(f"使用 Java {java_mod.get_java_major(java_exe) or '?'}: {java_exe}")
        progress(2, 4, "构建启动参数")
        if not props.get("authlib_api") and (acc.get("type") or "offline") == "offline":
            from mclauncher import offline_skin
            skin_api = offline_skin.prepare_injection(acc)
            if skin_api:
                props = dict(props)
                props["authlib_api"] = skin_api
                log(f"离线皮肤：本地皮肤服务已就绪 {skin_api}")
        if props.get("authlib_api"):
            from mclauncher import authlib as authlib_mod
            authlib_mod.ensure_injector(self._dm(progress, log), on_note=log)
        if props.get("nide8_id") or prep.get("nide8_id"):
            from mclauncher import nide8 as nide8_mod
            nide8_mod.ensure_jar(self._dm(progress, log), on_note=log)
            if prep.get("nide8_id") and not props.get("nide8_id"):
                props = dict(props)
                props["nide8_id"] = prep["nide8_id"]
        width, height = launch_flow.resolve_resolution(prep, width, height)
        cmd, _natives, _vdir, game_dir = build_launch_command(
            inst, version, props, java_exe,
            memory_mb=memory_mb, width=width, height=height,
            extra_game_args=extra_game_args,
            extra_jvm_args=prep["jvm_args"],
            game_directory=game_dir,
            authlib_api=props.get("authlib_api"),
        )
        if prep.get("wrapper"):
            cmd = launch_flow.apply_wrapper(cmd, prep["wrapper"])
            log(f"包装器命令: {prep['wrapper']}")
        log(f"实际启动: {cmd[0]}")
        log("正在启动游戏进程…")
        progress(3, 4, "游戏启动中")
        worker = getattr(_tls, "worker", None)
        proc = GameProcess(cmd, cwd=game_dir, on_line=log, priority=prep["priority"],
                           window_title=prep.get("window_title") or "")
        with self._game_lock:
            self._game_proc = proc
        self._emit("game_started", {})
        code = None
        # 游戏时长统计
        try:
            from mclauncher import playtime as playtime_mod
            tracker = playtime_mod.PlaytimeTracker(inst.name, version)
        except Exception:
            tracker = None
        if tracker is not None:
            tracker.start()
        try:
            code = proc.wait()
        finally:
            if tracker is not None:
                try:
                    dur = tracker.stop()
                    if dur:
                        log(f"本次游玩 {playtime_mod.format_duration(dur)}")
                except Exception:
                    pass
            with self._game_lock:
                if self._game_proc is proc:
                    self._game_proc = None
            self._emit("game_exited", {"code": code})
        if getattr(worker, "_cancelled", False):
            log("已停止游戏")
            return
        log(f"游戏已退出，退出码 {code}")
        launch_flow.run_hook(prep["settings"].get("post_launch") or "", game_dir, log=log)
        report = analyze_launch(
            inst, exit_code=code, output_lines=proc.last_lines(),
            started_at=getattr(proc, "started_at", None),
            cancelled=False, version=version,
            extra_roots=[game_dir],
        )
        if report.get("is_crash"):
            log(f"[崩溃分析] {report.get('summary') or report.get('headline')}")
            raise GameCrashError(report)
        return "游戏已退出"

    def _microsoft_login_impl(self, progress, log):
        client_id = CONFIG.get("microsoft_client_id") or "00000000402b5328"
        auth = MicrosoftAuthenticator(client_id=client_id)
        worker = getattr(_tls, "worker", None)

        def on_code(code, uri, exp):
            if worker:
                worker.login_code(code, uri)
            log(f"请打开 {uri} 并输入代码 {code}（{exp // 60} 分钟内有效）")

        def on_status(s):
            if worker:
                worker.login_status(str(s))
            log(str(s))
            progress(0, 0, str(s))

        account = auth.login(on_code=on_code, on_status=on_status, open_browser=True)
        self.accounts.add_account(account)
        log(f"登录成功：{account.get('name')}")
        return f"已登录 {account.get('name')}"

    def _authlib_login_impl(self, progress, log, api, username, password):
        from mclauncher import authlib as authlib_mod
        authlib_mod.ensure_injector(self._dm(progress, log), on_note=log)
        account = authlib_mod.login(api, username, password)
        self.accounts.add_account(account)
        log(f"皮肤站登录成功：{account.get('name')}")
        return f"已登录 {account.get('name')}"

    def _repair_impl(self, progress, log, instance, version):
        from mclauncher.repair import repair
        inst = self._instance(instance)
        dm = self._dm(progress, log)
        installer = Installer(inst, dm, on_progress=dm.on_progress, cancel=dm.cancel)
        return repair(installer, version)

    def _export_pack_impl(self, progress, log, instance, dest, fmt="mrpack"):
        from mclauncher.export_pack import export_cf_zip, export_mrpack
        inst = self._instance(instance)
        dm = self._dm(progress, log)
        if str(fmt or "mrpack").lower() in ("curseforge", "cf", "zip"):
            if not dest:
                dest = str(utils.ROOT / "exports" / f"{inst.name}-curseforge.zip")
            return export_cf_zip(inst, dest, dm=dm, on_note=lambda m, a, b: progress(a, b, m))
        if not dest:
            dest = str(utils.ROOT / "exports" / f"{inst.name}.mrpack")
        return export_mrpack(inst, dest, dm=dm, on_note=lambda m, a, b: progress(a, b, m))

    def _mod_update_impl(self, progress, log, instance):
        from mclauncher.mod_update import apply_update, check_updates
        inst = self._instance(instance)
        dm = self._dm(progress, log)
        rows = check_updates(inst, dm=dm)
        if not rows:
            return "没有可更新的模组"
        for i, row in enumerate(rows):
            apply_update(inst, row, dm=dm)
            progress(i + 1, len(rows), row.get("name") or "")
        return f"已更新 {len(rows)} 个模组"

    def _update_modpack_impl(self, progress, log, instance):
        inst = self._instance(instance)
        dm = self._dm(progress, log)
        result = modpack_mod.update_modpack(
            dm, inst, on_progress=dm.on_progress, cancel=dm.cancel,
            api_key=CONFIG.get("curseforge_api_key"))
        if not result.get("updated"):
            return f"已是最新版本：{result.get('current') or '?'}"
        removed = result.get("removed") or []
        if removed:
            log(f"已清理旧版本残留 {len(removed)} 个文件")
            for r in removed[:20]:
                log(f"  - {r}")
            if len(removed) > 20:
                log(f"  … 共 {len(removed)} 个")
        self._emit("ui_changed", {})
        return f"整合包已更新：{result.get('from') or '?'} → {result.get('to') or '?'}"

    def _self_update_impl(self, progress, log):
        from mclauncher import updater as updater_mod
        info = updater_mod.check()
        if not info.get("has_update"):
            return info.get("message") or "已是最新"
        log(info.get("message") or "下载更新")
        path = updater_mod.download(info)
        log(updater_mod.apply_exe(path))
        return "更新包已就绪，重启后生效"

    def _nide8_login_impl(self, progress, log, server_id, username, password):
        from mclauncher import nide8 as nide8_mod
        nide8_mod.ensure_jar(self._dm(progress, log), on_note=log)
        account = nide8_mod.login(server_id, username, password)
        self.accounts.add_account(account)
        log(f"统一通行证登录成功：{account.get('name')}")
        return f"已登录 {account.get('name')}"

    def _install_world_impl(self, progress, log, name, instance, extra=None):
        from mclauncher import worlds as worlds_mod
        extra = dict(extra or {})
        extra.setdefault("name", name)
        inst = self._instance(instance or extra.get("instance"))
        dm = self._dm(progress, log)
        result = worlds_mod.install_world(dm, extra, inst, on_progress=dm.on_progress)
        files = (result or {}).get("files") or []
        log(f"完成: {', '.join(files) or name}")
        return f"已安装世界 {', '.join(files) or name}"

    def _export_bat_impl(self, progress, log, instance, version, dest):
        from mclauncher import launch_flow, version_ops as vops
        inst = self._instance(instance)
        acc = self.accounts.get_account(self.accounts.active) if self.accounts.active else None
        if not acc:
            acc = self.accounts.offline_account("Player")
        props = self.accounts.launch_props(acc)
        prep = launch_flow.prepare(inst, version, memory_mb=int(CONFIG.get("memory_mb") or 4096))
        java_exe = java_mod.resolve_launch_java(inst.version_json(version) or {}, on_note=log)
        cmd, _n, _v, gdir = build_launch_command(
            inst, version, props, java_exe,
            memory_mb=prep["memory_mb"] or 4096,
            extra_game_args=prep["extra_game_args"],
            extra_jvm_args=prep["jvm_args"],
            game_directory=prep["game_dir"],
            authlib_api=props.get("authlib_api"),
        )
        if prep.get("wrapper"):
            cmd = launch_flow.apply_wrapper(cmd, prep["wrapper"])
        if not dest:
            dest = str(utils.ROOT / "exports" / f"launch-{inst.name}-{version}.bat")
        path = vops.export_launch_bat(Path(dest), cmd, gdir)
        log(f"已写出 {path}")
        return path

    # ==================================================================
    # 新增 API：服务器管理
    # ==================================================================

    def list_servers(self, instance: str = "") -> list[dict]:
        from mclauncher import servers as servers_mod
        inst = self._instance(instance)
        return servers_mod.list_servers(inst)

    def add_server(self, instance: str, name: str, ip: str, port: int = 25565,
                   description: str = "") -> dict:
        from mclauncher import servers as servers_mod
        inst = self._instance(instance)
        return servers_mod.add_server(inst, name, ip, port, description)

    def update_server(self, instance: str, index: int, **kwargs) -> dict:
        from mclauncher import servers as servers_mod
        inst = self._instance(instance)
        return servers_mod.update_server(inst, index, **kwargs)

    def delete_server(self, instance: str, index: int):
        from mclauncher import servers as servers_mod
        inst = self._instance(instance)
        servers_mod.delete_server(inst, index)

    def import_servers(self, instance: str, text: str) -> int:
        from mclauncher import servers as servers_mod
        inst = self._instance(instance)
        return servers_mod.import_servers_txt(inst, text)

    def export_servers(self, instance: str) -> str:
        from mclauncher import servers as servers_mod
        inst = self._instance(instance)
        return servers_mod.export_servers_txt(inst)

    # ==================================================================
    # 新增 API：游玩时长
    # ==================================================================

    def get_playtime(self, instance: str = "") -> dict:
        from mclauncher import playtime as playtime_mod
        inst_name = instance or CONFIG.get("default_instance", "default")
        return playtime_mod.get_playtime(inst_name)

    def get_all_playtime(self) -> dict:
        from mclauncher import playtime as playtime_mod
        return playtime_mod.get_all_playtime()

    def get_total_playtime(self) -> int:
        from mclauncher import playtime as playtime_mod
        return playtime_mod.get_total_playtime()

    def format_playtime(self, seconds: int) -> str:
        from mclauncher import playtime as playtime_mod
        return playtime_mod.format_duration(seconds)

    def clear_playtime(self, instance: str = "", version: str = ""):
        from mclauncher import playtime as playtime_mod
        playtime_mod.clear_playtime(instance, version)

    # ==================================================================
    # 新增 API：缩略图
    # ==================================================================

    def thumb_path(self, url: str) -> str:
        from mclauncher import thumbnails as thumb_mod
        return thumb_mod.thumb_path(url)

    def ensure_thumb(self, url: str) -> str:
        from mclauncher import thumbnails as thumb_mod
        return thumb_mod.ensure_thumb(url)

    # ==================================================================
    # 新增 API：Java 多发行版
    # ==================================================================

    def java_vendor_list(self) -> list[str]:
        from mclauncher import java as java_mod
        return java_mod.java_vendor_list()

    def java_vendor_label(self, vendor: str) -> str:
        from mclauncher import java as java_mod
        return java_mod.java_vendor_label(vendor)

    def install_java(self, major: int, vendor: str = "adoptium") -> str:
        return self.start_task(
            f"下载 {vendor} Java {major}",
            self._install_java_impl, major, vendor,
        )

    def _install_java_impl(self, progress, log, major, vendor):
        from mclauncher import java as java_mod
        dm = self._dm(progress, log)
        exe = java_mod.install_java_vendor(dm, major, vendor=vendor, on_progress=dm.on_progress)
        log(f"Java 已安装: {exe}")
        return f"Java {major} ({vendor}) 安装完成"

    # ==================================================================
    # 新增 API：多语言
    # ==================================================================

    def get_language(self) -> str:
        from mclauncher import i18n
        return i18n.current_language()

    def set_language(self, lang: str):
        from mclauncher import i18n
        i18n.set_language(lang)

    def available_languages(self) -> dict[str, str]:
        from mclauncher import i18n
        return i18n.available_languages()

    def translate(self, key: str, lang: str = "") -> str:
        from mclauncher import i18n
        return i18n._(key, lang or None)

    # ==================================================================
    # 新增 API：主题包
    # ==================================================================

    def list_themes(self) -> list[dict]:
        from mclauncher import themes as themes_mod
        return themes_mod.list_themes()

    def save_theme(self, name: str) -> dict:
        from mclauncher import themes as themes_mod
        return themes_mod.save_theme(name)

    def load_theme(self, name: str) -> dict:
        from mclauncher import themes as themes_mod
        return themes_mod.load_theme(name)

    def delete_theme(self, name: str):
        from mclauncher import themes as themes_mod
        themes_mod.delete_theme(name)

    def import_theme(self, path: str) -> str:
        from mclauncher import themes as themes_mod
        return themes_mod.import_theme(path)

    def export_theme(self, name: str, dest: str) -> str:
        from mclauncher import themes as themes_mod
        return themes_mod.export_theme(name, dest)

    # ==================================================================
    # 新增 API：官方启动器迁移
    # ==================================================================

    def detect_official_launcher(self) -> bool:
        from mclauncher import official_migrate as om
        return om.detect_official()

    def official_launcher_dir(self) -> str:
        from mclauncher import official_migrate as om
        d = om.official_dir()
        return str(d) if d else ""

    def scan_official_versions(self) -> list[str]:
        from mclauncher import official_migrate as om
        d = om.official_dir()
        if not d:
            return []
        return om.scan_versions(d)

    def scan_game_dir(self, path: str) -> dict:
        """校验任意目录是不是可导入的游戏目录（PCL / HMCL / 官方均可），
        返回 {dir, versions}；不是则抛错。"""
        from mclauncher import official_migrate as om
        resolved = om.resolve_game_dir(path)
        if resolved is None:
            raise ValueError("该目录里没有 versions 文件夹，不是 Minecraft 游戏目录")
        return {"dir": str(resolved), "versions": om.scan_versions(resolved)}

    def migrate_official_launcher(self, instance: str = "default", src_dir: str = "") -> str:
        return self.start_task(
            "导入游戏目录" if src_dir else "导入官方启动器",
            self._migrate_official_impl, instance, src_dir,
        )

    def _migrate_official_impl(self, progress, log, instance, src_dir=""):
        from mclauncher import official_migrate as om
        if src_dir:
            src = om.resolve_game_dir(src_dir)
            if src is None:
                raise FileNotFoundError(
                    f"该目录里没有 versions 文件夹，不是 Minecraft 游戏目录: {src_dir}")
        else:
            src = om.official_dir()
            if not src:
                raise FileNotFoundError("未找到官方启动器目录")
        log(f"正在从 {src} 迁移…")
        progress(1, 3, "扫描版本")
        versions = om.scan_versions(src)
        if not versions:
            log("未发现版本")
            return "无版本可导入"
        log(f"发现 {len(versions)} 个版本")
        progress(2, 3, f"导入 {len(versions)} 个版本")
        result = om.migrate(str(src), instance)
        log(f"已导入 {len(result.get('versions', []))} 个版本")
        return f"已导入 {len(result.get('versions', []))} 个版本"

    # ==================================================================
    # 新增 API：多开
    # ==================================================================

    def is_game_running(self) -> bool:
        with self._game_lock:
            proc = self._game_proc
        return proc is not None and getattr(proc, "poll", lambda: 0)() is None

    def allow_multi_instance(self) -> bool:
        return bool(CONFIG.get("allow_multi_instance", False))

    def set_multi_instance(self, allow: bool):
        CONFIG.set("allow_multi_instance", bool(allow))
        CONFIG.save()

    # ==================================================================
    # 新增 API：崩溃报告上传
    # ==================================================================

    def submit_crash_report(self, task_id: str = "") -> str:
        report = self.get_crash(task_id)
        if not report:
            raise LaunchError("没有可上传的崩溃报告")
        from mclauncher import feedback as fb
        result = fb.submit_crash(report)
        return result.get("message") or "已上传"

    # ==================================================================
    # 新增 API：启动命令展示
    # ==================================================================

    def get_launch_command(self, instance: str, version: str, account: str = "",
                           username: str = "", memory_mb: int = 0) -> str:
        from mclauncher import launch_flow, version_ops as vops
        from mclauncher.launcher import build_launch_command
        inst = self._instance(instance)
        if not version:
            raise LaunchError("请先选择版本")
        vjson = inst.version_json(version) or {}
        from mclauncher import manifest as manifest_mod
        try:
            resolved = manifest_mod.resolve_inherits(vjson, lambda pid: inst.version_json(pid))
        except Exception:
            resolved = vjson
        java_exe = java_mod.resolve_launch_java(resolved, on_note=None)
        if not java_exe:
            java_exe = java_mod.resolve_launch_java(resolved, dm=DownloadManager(threads=2))
        if not java_exe:
            raise LaunchError("无法确定 Java 路径")
        if not account:
            acc = self.accounts.get_account(self.accounts.active) if self.accounts.active else None
            if not acc:
                acc = self.accounts.offline_account(username or "Player")
        else:
            acc = self.accounts.get_account(account)
            if acc:
                acc = self.accounts.ensure_valid(acc)
            else:
                acc = self.accounts.offline_account(username or "Player")
        props = self.accounts.launch_props(acc)
        prep = launch_flow.prepare(inst, version, memory_mb=memory_mb or int(CONFIG.get("memory_mb") or 4096))
        cmd, _n, _v, _g = build_launch_command(
            inst, version, props, java_exe,
            memory_mb=prep["memory_mb"],
            extra_game_args=prep["extra_game_args"],
            extra_jvm_args=prep["jvm_args"],
            game_directory=prep["game_dir"],
        )
        return " ".join(cmd)

    # ==================================================================
    # 新增 API：首次运行智能推荐
    # ==================================================================

    def get_smart_recommendation(self) -> dict:
        from mclauncher.sysinfo import get_smart_recommendation
        return get_smart_recommendation()

    def test_ai_connection(self) -> str:
        from mclauncher.ai.client import test_connection
        return test_connection(self.get_settings())

    def ai_list_chats(self) -> dict:
        from mclauncher.ai import store as chat_store
        return chat_store.load()

    def ai_new_chat(self) -> dict:
        from mclauncher.ai import store as chat_store
        data = chat_store.load()
        chat_store.new_chat(data)
        return data

    def ai_delete_chat(self, chat_id: str) -> dict:
        from mclauncher.ai import store as chat_store
        data = chat_store.load()
        chat_store.delete_chat(data, chat_id)
        return data

    def ai_set_active(self, chat_id: str) -> dict:
        from mclauncher.ai import store as chat_store
        data = chat_store.load()
        chat_store.set_active(data, chat_id)
        return data

    def ai_stop(self) -> dict:
        self._ai_cancel = True
        http = self._ai_http
        if http:
            try:
                http.abort()
            except Exception:
                pass
        self.ai_confirm(False)
        self.ai_answer(None)
        return {"ok": True}

    def ai_confirm(self, ok: bool = False) -> dict:
        self._ai_confirm_ok = bool(ok)
        self._ai_confirm_ev.set()
        return {"ok": True}

    def ai_answer(self, result=None) -> dict:
        self._ai_ask_result = result
        self._ai_ask_ev.set()
        return {"ok": True}

    def ai_send(self, text: str, chat_id: str = "", launch: dict | None = None) -> dict:
        if self._ai_busy:
            return {"ok": False, "message": "上一条还在处理"}
        self._ai_busy = True
        self._ai_cancel = False
        self._ui_launch = dict(launch or {})
        t = threading.Thread(
            target=self._ai_run, args=(text, chat_id), daemon=True, name="ai-send")
        t.start()
        return {"ok": True, "started": True}

    def _ai_run(self, text: str, chat_id: str):
        from mclauncher.ai import store as chat_store
        from mclauncher.ai.agent import AgentCancelled, run_agent
        from mclauncher.ai.client import AIClientError, HttpCancel

        data = chat_store.load()
        if chat_id:
            chat_store.set_active(data, chat_id)
        chat = chat_store.get_chat(data, data.get("active_id") or "") or {}
        history = chat_store.api_messages(chat.get("messages") or [])
        http = HttpCancel()
        self._ai_http = http
        notes = []
        buf = []
        last = [0.0]
        delayed = [None]

        def flush_delta(force=False):
            if not buf:
                return
            now = time.monotonic()
            if not force and now - last[0] < 0.033:
                if delayed[0] is None:
                    t = threading.Timer(0.033, lambda: flush_delta(True))
                    t.daemon = True
                    delayed[0] = t
                    t.start()
                return
            if delayed[0] is not None:
                delayed[0].cancel()
                delayed[0] = None
            last[0] = now
            self._bus.emit("ai.delta", {"text": "".join(buf)})
            buf.clear()

        def on_delta(piece):
            if piece:
                buf.append(piece)
                flush_delta()

        def on_status(kind, payload):
            payload = payload or {}
            if kind == "tool_done" and payload.get("label"):
                notes.append(payload.get("label"))
            self._bus.emit("ai.status", {"kind": kind, **payload})

        def confirm_fn(name, args, label):
            self._ai_confirm_ev.clear()
            self._bus.emit("ai.confirm", {"name": name, "args": args or {}, "label": label})
            self._ai_confirm_ev.wait()
            return self._ai_confirm_ok

        def ask_fn(questions, title):
            self._ai_ask_ev.clear()
            self._ai_ask_result = None
            self._bus.emit("ai.ask", {"questions": questions or [], "title": title or ""})
            self._ai_ask_ev.wait()
            return self._ai_ask_result

        def cancelled():
            return self._ai_cancel

        try:
            reply = run_agent(
                self, self.get_settings(), history, text,
                on_delta=on_delta, on_status=on_status,
                confirm_fn=confirm_fn, ask_fn=ask_fn, cancelled=cancelled,
                http_cancel=http,
            )
            flush_delta(True)
            if self._ai_cancel:
                raise AgentCancelled()
            if notes:
                extra = "（本轮：" + "；".join(notes[:8]) + "）"
                if extra not in (reply or ""):
                    reply = ((reply or "") + "\n\n" + extra).strip()
            history.append({"role": "user", "content": text})
            history.append({"role": "assistant", "content": reply or ""})
            chat_store.upsert_messages(data, data.get("active_id") or "", history[-24:])
            self._bus.emit("ai.done", {"text": reply or "", "store": data})
        except AgentCancelled:
            flush_delta(True)
            self._bus.emit("ai.fail", {"text": "已停止", "stopped": True})
        except AIClientError as exc:
            flush_delta(True)
            self._bus.emit("ai.fail", {"text": str(exc), "stopped": False})
        except Exception as exc:  # noqa: BLE001
            flush_delta(True)
            self._bus.emit("ai.fail", {"text": str(exc), "stopped": False})
        finally:
            if delayed[0] is not None:
                delayed[0].cancel()
                delayed[0] = None
            self._ai_busy = False
            self._ai_http = None
