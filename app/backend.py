# -*- coding: utf-8 -*-
"""backend.py — 把 Fluent UI 接到 mclauncher 真实后端。"""

from __future__ import annotations

import itertools
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Qt, Signal

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
from mclauncher.i18n import tr


class SilentWorker(QThread):
    """不进任务栏的后台调用，避免搜索卡死 UI。"""

    ok = Signal(object)
    err = Signal(str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn

    def run(self):
        try:
            self.ok.emit(self._fn())
        except Exception as exc:  # noqa: BLE001
            self.err.emit(str(exc))


class TaskCancelled(Exception):
    """用户取消任务时由 progress 回调抛出。"""


_QT_INT_SAFE = 2_000_000_000


def _qt_progress(current, total):
    """Qt Signal(int) 在 Windows 是 32 位，大整合包按万分比上报。"""
    try:
        cur = int(current or 0)
        tot = int(total or 0)
    except (TypeError, ValueError):
        return 0, 0
    if cur < 0:
        cur = 0
    if tot < 0:
        tot = 0
    if tot > _QT_INT_SAFE or cur > _QT_INT_SAFE:
        if tot > 0:
            return min(10000, int(cur * 10000 / tot)), 10000
        return 0, 0
    return cur, tot


class BackendWorker(QThread):
    """通用后台任务线程。target 的第一个参数必须是 progress 回调，第二个是 log 回调。

    注意信号名叫 `task_finished` 而不是 `finished`：后者是 `QThread` 自带的，
    覆盖掉就再也拿不到「线程真正退出」的时机，也就没法挂 `deleteLater`，
    于是每个 worker 作为 `BackendAPI` 的子对象一直活到进程结束。
    """

    progress = Signal(str, int, int, str)
    log = Signal(str, str)
    task_finished = Signal(str, bool, str)
    crash = Signal(str, object)
    login_code = Signal(str, str)
    login_status = Signal(str)

    def __init__(self, task_id: str, target, args=(), kwargs=None, parent=None):
        super().__init__(parent)
        self.task_id = task_id
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}
        self._cancelled = False
        # 进度节流：下载器每个分块都会回调，全量跨线程 emit 会把 UI
        # 事件循环淹没（界面卡、CPU 高）。80ms 或 0.5% 变化才发一条，
        # 取消检查不受节流影响，每回调必查。
        self._last_emit_t = 0.0
        self._last_emit_ratio = -1.0
        self._last_emit_msg = None

    def cancel(self):
        self._cancelled = True

    def _progress(self, current, total, message=""):
        if self._cancelled:
            raise TaskCancelled()
        cur, tot = _qt_progress(current, total)
        msg = str(message or "")
        now = time.monotonic()
        done = tot > 0 and cur >= tot
        ratio = (cur / tot) if tot > 0 else 0.0
        if not done:
            if now - self._last_emit_t < 0.08:
                return
            if self._last_emit_ratio >= 0 and abs(ratio - self._last_emit_ratio) < 0.005 \
                    and msg == self._last_emit_msg:
                return
        self._last_emit_t = now
        self._last_emit_ratio = ratio
        self._last_emit_msg = msg
        self.progress.emit(self.task_id, cur, tot, msg)

    def _log(self, text):
        self.log.emit(self.task_id, str(text))

    def run(self):
        try:
            result = self._target(self._progress, self._log, *self._args, **self._kwargs)
            msg = result if isinstance(result, str) and result else tr("任务完成")
            self.task_finished.emit(self.task_id, True, msg)
        except TaskCancelled:
            self.task_finished.emit(self.task_id, False, tr("已取消"))
        except GameCrashError as exc:
            self._log(f"[错误] {exc}")
            self.crash.emit(self.task_id, exc.report)
            self.task_finished.emit(self.task_id, False, str(exc))
        except Exception as exc:  # noqa: BLE001
            self._log(f"[错误] {exc}")
            self.task_finished.emit(self.task_id, False, str(exc))


class BackendAPI(QObject):
    """后端门面。UI 层只跟它打交道。"""

    task_added = Signal(str, str)
    progress = Signal(str, int, int, str)
    log = Signal(str, str)
    finished = Signal(str, bool, str)
    crash = Signal(str, object)
    login_code = Signal(str, str)
    login_status = Signal(str)
    ui_changed = Signal()
    theme_changed = Signal()
    task_count_changed = Signal(int)
    game_started = Signal()
    game_exited = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._counter = itertools.count(1)
        self._workers: dict[str, BackendWorker] = {}
        self._titles: dict[str, str] = {}
        self.accounts = AccountManager()
        self._game_proc = None
        self._game_lock = threading.Lock()
        self._launch_task_id = None
        self._pack_cache: list[dict] = []
        self._mod_cache: list[dict] = []
        self._last_installed: dict = {}
        self._bg_threads: list[QThread] = []
        self._task_results: dict[str, tuple] = {}
        self._crashes: dict[str, dict] = {}
        self._settings_cache: dict | None = None
        self._settings_rev = -1
        # 实例快照缓存：get_instances() 每次都要 iterdir 实例根目录、
        # 逐实例读 meta / 扫 versions，启动页一次 reload 还会调它两遍
        # （reload + _sync_banner）。UI 线程同步扫，这是导航卡顿的大头之一。
        self._inst_cache: list[dict] | None = None
        self._inst_cache_at: float = 0.0
        self._ensure_default_instance()
        try:
            from mclauncher.source import warmup_async
            warmup_async()
        except Exception:
            pass
        # 系统 Java 扫描冷启动要 glob Program Files + 逐个跑
        # `java -version` 子进程，秒级起步。提前在后台把缓存灌满，
        # UI 上的 Java 标签 / 下拉框此后都走 cached_* 只读路径。
        try:
            java_mod.warm_system_javas_async()
        except Exception:
            pass

    def _emit_ui_changed(self):
        """数据变了：失效实例快照再广播。改数据的入口一律走这个。"""
        self._inst_cache = None
        # 必须从实例访问：类级 BackendAPI.ui_changed 是未绑定 Signal，
        # 没有 .emit（任务完成回调里就这么崩过）。信号无参，别传 self。
        self.ui_changed.emit()

    def invalidate_instances(self):
        self._inst_cache = None

    def _ensure_default_instance(self):
        names = list_instances()
        if names:
            return
        name = CONFIG.get("default_instance", "default") or "default"
        try:
            Instance(name).create()
        except InstanceError:
            pass

    # ------------------------------------------------------------------
    # 任务管理
    # ------------------------------------------------------------------
    def start_task(self, title: str, fn, *args, **kwargs) -> str:
        task_id = f"task-{next(self._counter)}"
        worker = BackendWorker(task_id, fn, args, kwargs, self)
        worker.progress.connect(self.progress, Qt.QueuedConnection)
        worker.log.connect(self.log, Qt.QueuedConnection)
        worker.crash.connect(self._on_worker_crash, Qt.QueuedConnection)
        worker.login_code.connect(self.login_code, Qt.QueuedConnection)
        worker.login_status.connect(self.login_status, Qt.QueuedConnection)
        worker.task_finished.connect(self._on_worker_finished, Qt.QueuedConnection)
        # QThread.finished 现在没被遮蔽了，可以在线程真正退出后回收对象。
        worker.finished.connect(worker.deleteLater)
        self._workers[task_id] = worker
        self._titles[task_id] = title
        worker.start()
        self.task_added.emit(task_id, title)
        self.task_count_changed.emit(self._download_task_count())
        return task_id

    @staticmethod
    def is_download_title(title: str) -> bool:
        t = str(title or "")
        return not (t.startswith(tr("启动游戏")) or t.startswith(tr("微软登录")) or t.startswith(tr("皮肤站登录")))

    def _download_task_count(self) -> int:
        n = 0
        for tid in self._workers:
            if self.is_download_title(self._titles.get(tid, "")):
                n += 1
        return n

    def _on_worker_crash(self, task_id, report):
        self._crashes[task_id] = report or {}
        if len(self._crashes) > 40:
            extra = list(self._crashes)[:-20]
            for k in extra:
                self._crashes.pop(k, None)
        self.crash.emit(task_id, report)

    def get_crash(self, task_id: str = "") -> dict:
        if task_id and task_id in self._crashes:
            return self._crashes[task_id]
        if self._crashes:
            return self._crashes[next(reversed(self._crashes))]
        return {}

    def export_crash_report(self, task_id: str = "", dest: str = "") -> str:
        report = self.get_crash(task_id)
        if not report:
            raise LaunchError(tr("没有可导出的错误报告"))
        return export_report(report, dest or None)

    def open_crash_file(self, path: str = "", task_id: str = "") -> str:
        target = path or (self.get_crash(task_id).get("direct_file") or "")
        if not target:
            raise LaunchError(tr("没有可打开的日志文件"))
        if not open_path(target):
            raise LaunchError(f"无法打开: {target}")
        return target

    def _on_worker_finished(self, task_id, success, message):
        self._workers.pop(task_id, None)
        self._task_results[task_id] = (bool(success), str(message))
        if len(self._task_results) > 80:
            extra = list(self._task_results)[:-40]
            for k in extra:
                self._task_results.pop(k, None)
        self.finished.emit(task_id, success, message)
        self.task_count_changed.emit(self._download_task_count())
        if success:
            self._emit_ui_changed()

    def wait_task(self, task_id: str, timeout: float = 1800, cancelled=None) -> dict:
        """后台线程里等任务结束。启动游戏不要用这个（会等到退出）。"""
        if task_id in self._task_results:
            ok, msg = self._task_results[task_id]
            return {"ok": ok, "message": msg, "task_id": task_id}
        done = threading.Event()
        box = {}

        def on_finished(tid, ok, msg):
            if tid != task_id:
                return
            box["ok"] = bool(ok)
            box["msg"] = str(msg)
            done.set()

        self.finished.connect(on_finished, Qt.QueuedConnection)
        try:
            if task_id in self._task_results:
                ok, msg = self._task_results[task_id]
                return {"ok": ok, "message": msg, "task_id": task_id}
            while not done.wait(0.4):
                if cancelled and cancelled():
                    self.cancel_task(task_id)
                    return {"ok": False, "message": tr("已停止"), "task_id": task_id}
                timeout -= 0.4
                if timeout <= 0:
                    return {"ok": False, "message": tr("等待任务超时"), "task_id": task_id}
        finally:
            try:
                self.finished.disconnect(on_finished)
            except Exception:
                pass
        return {"ok": box.get("ok"), "message": box.get("msg"), "task_id": task_id}

    def cancel_task(self, task_id: str):
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

    def call_async(self, fn, on_ok, on_err=None):
        worker = SilentWorker(fn, self)
        self._bg_threads.append(worker)

        def _cleanup():
            try:
                self._bg_threads.remove(worker)
            except ValueError:
                pass

        def _log_err(message):
            name = getattr(fn, "__name__", None) or repr(fn)
            utils.log.warning(tr("后台调用 %s 失败: %s"), name, message)

        worker.ok.connect(on_ok, Qt.QueuedConnection)
        worker.err.connect(on_err or _log_err, Qt.QueuedConnection)
        worker.finished.connect(_cleanup)
        worker.finished.connect(worker.deleteLater)
        worker.start()
        return worker

    def shutdown(self, timeout_ms: int = 800):
        """关闭前收拢后台线程。

        以前退出时谁也不等，QThread 还在跑就被销毁，Qt 会打
        「QThread: Destroyed while thread is still running」。

        预算只给 800ms：关窗是用户动作，不能为了等一个可能几秒才返回的网络请求
        把界面卡住。等不到就放手交给 Qt/Python 的正常析构（实测正常退出路径是干净的）。

        启动游戏那个 worker 不动：它阻塞在 `proc.wait()` 上，等它等于要等玩家退游戏，
        而「关掉启动器但游戏继续跑」本来就是既定行为。
        """
        import time as _time
        deadline = _time.monotonic() + max(0, timeout_ms) / 1000.0

        for task_id, worker in list(self._workers.items()):
            if task_id == self._launch_task_id:
                continue
            try:
                worker.cancel()
                worker.requestInterruption()
            except RuntimeError:
                pass

        pending = [w for tid, w in self._workers.items() if tid != self._launch_task_id]
        pending += list(self._bg_threads)
        for worker in pending:
            remain = int(max(0.0, deadline - _time.monotonic()) * 1000)
            if remain <= 0:
                break
            try:
                if worker.isRunning():
                    worker.wait(remain)
            except RuntimeError:
                pass

    def task_title(self, task_id: str) -> str:
        return self._titles.get(task_id, task_id)

    def _dm(self, progress, log) -> DownloadManager:
        worker = QThread.currentThread()
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

    # ==================================================================
    # 对外 API（异步任务）
    # ==================================================================
    def install_game(self, version: str, loader: str = tr("无"), loader_version: str = "",
                     instance: str = "", extra: dict | None = None) -> str:
        inst = instance or CONFIG.get("default_instance", "default")
        extra = extra or {}
        bits = [version]
        if loader and loader not in ("", tr("无")):
            bits.append(loader)
        if extra.get("optifine"):
            bits.append("OptiFine")
        if extra.get("liteloader"):
            bits.append("LiteLoader")
        title = tr("安装游戏 ") + " + ".join(bits)
        return self.start_task(title, self._install_game_impl, version, loader, loader_version, inst, extra)

    def install_modpack(self, name: str, source: str = "Modrinth", extra: dict | None = None) -> str:
        return self.start_task(tr("安装整合包 {0}").format(Path(name).name), self._install_modpack_impl,
                               name, source, extra or {})

    def install_mod(self, name: str, instance: str = "default", extra: dict | None = None) -> str:
        return self.start_task(tr("安装模组 {0}").format(Path(str(name)).name), self._install_mod_impl,
                               name, instance, extra or {})

    def install_shader(self, name: str, instance: str = "default", extra: dict | None = None) -> str:
        return self.start_task(tr("安装光影 {0}").format(Path(str(name)).name), self._install_content_impl,
                               "shader", name, instance, extra or {})

    def install_resourcepack(self, name: str, instance: str = "default", extra: dict | None = None) -> str:
        return self.start_task(tr("安装资源包 {0}").format(Path(str(name)).name), self._install_content_impl,
                               "resourcepack", name, instance, extra or {})

    def install_datapack(self, name: str, instance: str = "default", extra: dict | None = None) -> str:
        return self.start_task(tr("安装数据包 {0}").format(Path(str(name)).name), self._install_content_impl,
                               "datapack", name, instance, extra or {})

    def install_world(self, name: str, instance: str = "default", extra: dict | None = None) -> str:
        return self.start_task(tr("安装世界 {0}").format(Path(str(name)).name), self._install_world_impl,
                               name, instance, extra or {})

    def list_catalog_files(self, extra: dict | None = None) -> list[dict]:
        from mclauncher.catalog_files import list_project_files
        return list_project_files(DownloadManager(threads=2), extra or {})

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
        out = vops.rename_version(self._instance(instance), version, new_id)
        self._emit_ui_changed()
        return out

    def copy_version(self, instance: str, version: str, new_id: str) -> str:
        from mclauncher import version_ops as vops
        out = vops.copy_version(self._instance(instance), version, new_id)
        self._emit_ui_changed()
        return out

    def hide_version(self, instance: str, version: str, hidden: bool = True) -> dict:
        from mclauncher import version_ops as vops
        out = vops.set_hidden(self._instance(instance), version, hidden)
        self._emit_ui_changed()
        return out

    def open_version_folder(self, instance: str, version: str = "", which: str = "root") -> str:
        from mclauncher import version_ops as vops
        return vops.open_folder(self._instance(instance), version, which)

    def export_launch_script(self, instance: str, version: str, dest: str = "") -> str:
        return self.start_task(tr("导出启动脚本 {0}").format(version), self._export_bat_impl, instance, version, dest)

    def create_desktop_shortcut(self, instance: str, version: str, username: str = "",
                                account: str = "", name: str = "") -> str:
        from mclauncher import shortcut
        return shortcut.create_launch_shortcut(instance, version, username, account, name)

    def list_saves(self, instance: str, version: str = "") -> list[dict]:
        from mclauncher import saves as saves_mod
        return saves_mod.list_saves(self._instance(instance), version)

    def delete_save(self, instance: str, name: str, version: str = ""):
        from mclauncher import saves as saves_mod
        saves_mod.delete_save(self._instance(instance), name, version)
        self._emit_ui_changed()

    def backup_save(self, instance: str, name: str, version: str = "") -> str:
        return self.start_task(tr("备份存档 {0}").format(name), self._backup_save_impl, instance, name, version)

    def _backup_save_impl(self, progress, log, instance, name, version):
        from mclauncher import saves as saves_mod
        info = saves_mod.backup_save(
            self._instance(instance), name, version,
            on_progress=lambda text, cur, total: progress(cur, total, text))
        log(f"备份完成: {info['path']}")
        self._emit_ui_changed()
        return f"已备份到 {info['name']}"

    def list_save_backups(self, instance: str, name: str = "", version: str = "") -> list[dict]:
        from mclauncher import saves as saves_mod
        return saves_mod.list_backups(self._instance(instance), name, version)

    def restore_save_backup(self, instance: str, backup_name: str, version: str = "",
                            overwrite: bool = False) -> dict:
        from mclauncher import saves as saves_mod
        out = saves_mod.restore_backup(
            self._instance(instance), backup_name, version, overwrite=overwrite)
        self._emit_ui_changed()
        return out

    def delete_save_backup(self, instance: str, backup_name: str, version: str = ""):
        from mclauncher import saves as saves_mod
        saves_mod.delete_backup(self._instance(instance), backup_name, version)
        self._emit_ui_changed()

    def export_save(self, instance: str, name: str, dest: str, version: str = "") -> str:
        from mclauncher import saves as saves_mod
        return saves_mod.export_save(self._instance(instance), name, dest, version)

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

    def open_media(self, path: str) -> bool:
        return bool(open_path(path))

    def delete_modpack(self, instance: str, filename: str = ""):
        inst = self._instance(instance)
        meta = inst.meta() or {}
        pack = meta.get("modpack")
        if not isinstance(pack, dict) or not pack.get("name"):
            raise InstanceError(tr("该实例没有已安装整合包"))
        inst.delete()
        self._emit_ui_changed()

    def list_global_mods(self) -> list[dict]:
        from mclauncher import global_mods as gm
        return gm.list_entries()

    def set_global_mod_enabled(self, filename: str, enabled: bool) -> str:
        from mclauncher import global_mods as gm
        name = gm.set_enabled(filename, enabled)
        self._emit_ui_changed()
        return name

    def start_nide8_login(self, server_id: str, username: str, password: str) -> str:
        return self.start_task(tr("统一通行证登录"), self._nide8_login_impl, server_id, username, password)

    def catalog_favorites(self) -> list:
        return list(CONFIG.get("catalog_favorites") or [])

    def toggle_favorite(self, item: dict) -> list:
        rows = list(CONFIG.get("catalog_favorites") or [])
        key = (str(item.get("source") or ""), str(item.get("slug") or item.get("id") or item.get("name") or ""))
        kept = []
        found = False
        for r in rows:
            rk = (str(r.get("source") or ""), str(r.get("slug") or r.get("id") or r.get("name") or ""))
            if rk == key:
                found = True
                continue
            kept.append(r)
        if not found:
            kept.append({
                "name": item.get("name"), "source": item.get("source"),
                "slug": item.get("slug"), "id": item.get("id"),
            })
        CONFIG.set("catalog_favorites", kept)
        CONFIG.save()
        return kept

    def set_game_dir(self, path: str):
        p = Path(path).expanduser()
        CONFIG.set("instances_dir", str(p) if p.is_absolute() else path)
        CONFIG.save()
        self._emit_ui_changed()
        return str(CONFIG.instances_dir)

    def download_java(self, major: str, vendor: str = "adoptium") -> str:
        vendor = (vendor or "adoptium").strip() or "adoptium"
        if vendor == "adoptium":
            return self.start_task(tr("下载 Java {0}").format(major), self._download_java_impl, major)
        return self.install_java(int(major), vendor=vendor)

    def terracotta_player(self) -> str:
        acc = self.accounts.get_active()
        if acc and acc.get("name"):
            return str(acc["name"])
        return "Player"

    def terracotta_snapshot(self) -> dict:
        game_on = bool(self._game_proc and getattr(self._game_proc, "poll", lambda: 0)() is None)
        return terracotta_mod.snapshot(self.terracotta_player(), game_running=game_on)

    def terracotta_prepare(self) -> str:
        return self.start_task(tr("准备陶瓦联机"), self._terracotta_prepare_impl)

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

    def terracotta_enter_world(self):
        info = self.terracotta_snapshot()
        url = str(info.get("url") or "")
        if info.get("state") != "guest-ok" or not url:
            raise terracotta_mod.TerracottaError(tr("还没连上房间。请先输入邀请码加入。"))
        return self._launch_into_server(url, tr("请到游戏「多人游戏」双击「陶瓦联机大厅」。"))

    def terracotta_direct_connect(self, address: str):
        host, port = terracotta_mod.split_join_url(address)
        if not host or host in ("127.0.0.1", "localhost"):
            raise terracotta_mod.TerracottaError(tr("请输入房主的公网地址，例如 1.2.3.4:25565"))
        return self._launch_into_server(f"{host}:{port}", tr("请到游戏「多人游戏」双击「陶瓦联机大厅」。"))

    def _launch_into_server(self, url: str, already_msg: str):
        inst = self._instance()
        terracotta_mod.remember_lobby(url, inst.path)
        info = self.terracotta_snapshot()
        if info.get("game_running"):
            return already_msg
        ids = inst.installed_ids()
        if not ids:
            raise LaunchError(tr("还没有安装任何版本。请先到「下载 → 原版游戏」安装一个版本。"))
        version = max(ids, key=lambda vid: (inst.versions_dir() / vid).stat().st_mtime)
        host, port = terracotta_mod.split_join_url(url)
        acc = self.accounts.get_active()
        if acc and acc.get("type") == "microsoft":
            account = acc.get("name") or tr("离线模式")
            username = acc.get("name") or "Player"
        else:
            account = tr("离线模式")
            username = (acc or {}).get("name") or self.terracotta_player()
        return self.launch_game(
            instance=inst.name,
            version=version,
            account=account,
            username=username,
            memory_mb=int(CONFIG.get("memory_mb") or 4096),
            width=int(CONFIG.get("width") or 854),
            height=int(CONFIG.get("height") or 480),
            extra_game_args=["--server", host, "--port", str(port)],
        )

    def launch_game(self, instance: str, version: str, account: str,
                    username: str, memory_mb: int, width: int, height: int,
                    java: str = tr("自动选择"), extra_game_args=None) -> str:
        task_id = self.start_task(
            tr("启动游戏 {0}").format(version), self._launch_game_impl,
            instance, version, account, username, memory_mb, width, height, java,
            extra_game_args,
        )
        self._launch_task_id = task_id
        return task_id

    def build_launch_command(self, instance: str, version: str, account: str,
                              username: str, memory_mb: int, width: int, height: int,
                              java: str = tr("自动选择")) -> str:
        """生成启动命令文本（不实际启动）。"""
        inst = self._instance(instance)
        if not version:
            raise LaunchError(tr("请先选择版本"))
        if account == tr("离线模式") or not account:
            acc = self.accounts.offline_account(
                username or "Player", skin=CONFIG.get("offline_skin") or "default")
        else:
            acc = self.accounts.get_account(account)
            if not acc:
                raise LaunchError(f"账号不存在: {account}")
            acc = self.accounts.ensure_valid(acc)
        props = self.accounts.launch_props(acc)
        from mclauncher import launcher
        from mclauncher import version_settings as _vs
        auth_server = str(_vs.load(inst, version).get("auth_server") or "").strip()
        if auth_server and not props.get("authlib_api"):
            props = dict(props)
            props["authlib_api"] = auth_server
        java_exe = tr("自动选择") if java in (tr("自动选择"), "") else java
        cmd, _natives, _vdir, _gdir = launcher.build_launch_command(
            inst, version, props, java_exe, memory_mb=memory_mb,
            width=width, height=height, authlib_api=props.get("authlib_api"))
        return cmd

    def start_microsoft_login(self) -> str:
        return self.start_task(tr("微软登录"), self._microsoft_login_impl)

    def uninstall_version(self, spec: str):
        if " / " in spec:
            inst_name, vid = spec.split(" / ", 1)
        else:
            inst_name, vid = CONFIG.get("default_instance", "default"), spec
        Installer(self._instance(inst_name)).uninstall_version(vid.strip())
        self._emit_ui_changed()

    def create_instance(self, name: str):
        Instance(name).create()
        self._emit_ui_changed()

    def delete_instance(self, name: str):
        Instance(name).delete()
        self._emit_ui_changed()

    def rename_instance(self, name: str, new_name: str):
        Instance(name).rename(new_name)
        self._emit_ui_changed()

    def open_instance_folder(self, name: str):
        path = self._instance(name).path
        if os.name == "nt":
            os.startfile(path)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])

    def open_mods_folder(self, instance: str, version: str = "") -> str:
        folder = self._mods_folder(self._instance(instance), version)
        utils.ensure_dir(folder)
        if not open_path(folder):
            raise LaunchError(f"无法打开: {folder}")
        return str(folder)

    def delete_mod(self, instance: str, filename: str, version: str = ""):
        inst = self._instance(instance)
        folder = self._mods_folder(inst, version)
        mods_mod.delete_mod(inst, filename, mods_dir=folder)
        self._emit_ui_changed()

    def disable_mod(self, instance: str, filename: str, version: str = "") -> str:
        inst = self._instance(instance)
        name = mods_mod.set_mod_enabled(inst, filename, False, mods_dir=self._mods_folder(inst, version))
        self._emit_ui_changed()
        return name

    def enable_mod(self, instance: str, filename: str, version: str = "") -> str:
        inst = self._instance(instance)
        name = mods_mod.set_mod_enabled(inst, filename, True, mods_dir=self._mods_folder(inst, version))
        self._emit_ui_changed()
        return name

    def _mods_folder(self, inst, version: str = ""):
        if version:
            from mclauncher import version_settings as vs
            return vs.mods_dir(inst, version)
        return inst.path / "mods"

    def get_installed_mods(self, instance: str, version: str = "") -> list[str]:
        inst = self._instance(instance)
        return [r["filename"] for r in mods_mod.list_mod_entries_at(self._mods_folder(inst, version)) if r.get("enabled")]

    def get_installed_mod_entries(self, instance: str, version: str = "") -> list[dict]:
        inst = self._instance(instance)
        if version:
            return mods_mod.list_mod_entries_at(self._mods_folder(inst, version))
        return mods_mod.list_instance_mod_entries(inst)

    def get_mods_targets(self, instance: str) -> list[dict]:
        """Mod 安装目标列表：实例共享 mods + 开了版本隔离的版本各自目录。"""
        from mclauncher import version_settings as vs
        inst = self._instance(instance)
        rows = [{"label": tr("实例共享 mods 目录"), "value": ""}]
        for vid in inst.installed_ids():
            iso = vs.load(inst, vid).get("isolation")
            if iso in (vs.ISOLATION_MODS, vs.ISOLATION_ALL):
                rows.append({"label": f"{vid} · {tr('独立 mods')}", "value": vid})
        return rows

    def get_installed_shaders(self, instance: str) -> list[str]:
        return [p.name for p in mods_mod.list_content_files(self._instance(instance), "shaderpacks")]

    def get_installed_resourcepacks(self, instance: str) -> list[str]:
        return [p.name for p in mods_mod.list_content_files(self._instance(instance), "resourcepacks")]

    def get_installed_datapacks(self, instance: str) -> list[str]:
        return [p.name for p in mods_mod.list_content_files(self._instance(instance), "datapacks")]

    def get_installed_modpacks(self, instance: str) -> list[str]:
        meta = self._instance(instance).meta() or {}
        pack = meta.get("modpack")
        if isinstance(pack, dict) and pack.get("name"):
            label = pack.get("name")
            if pack.get("version"):
                label = f"{label} {pack.get('version')}"
            return [label]
        return []

    def delete_shader(self, instance: str, filename: str):
        mods_mod.delete_content_file(self._instance(instance), "shaderpacks", filename)
        self._emit_ui_changed()

    def delete_resourcepack(self, instance: str, filename: str):
        mods_mod.delete_content_file(self._instance(instance), "resourcepacks", filename)
        self._emit_ui_changed()

    def delete_datapack(self, instance: str, filename: str):
        mods_mod.delete_content_file(self._instance(instance), "datapacks", filename)
        self._emit_ui_changed()

    def get_setting(self, key: str, default=None):
        # get_settings() 会重建整份字典（含两次模块 import 和几次 Path→str），
        # 而 main_window 里光是切一次主题就要连取 9 个键。按 CONFIG.revision 缓存，
        # 配置一改缓存自动失效，取值语义和以前完全一致。
        rev = CONFIG.revision
        if self._settings_rev != rev or self._settings_cache is None:
            self._settings_cache = self.get_settings()
            self._settings_rev = rev
        return self._settings_cache.get(key, default)

    def update_settings(self, settings: dict):
        self.save_settings(settings)

    def get_settings(self) -> dict:
        from mclauncher.ai.defaults import DEFAULT_GATEWAY_URL, DEFAULT_MODEL
        from mclauncher.feedback_defaults import DEFAULT_FEEDBACK_URL
        return {
            "share_libraries": bool(CONFIG.get("shared_libraries", False)),
            "share_assets": bool(CONFIG.get("shared_assets", False)),
            "download_threads": int(CONFIG.get("download_threads", 8)),
            "default_memory_mb": int(CONFIG.get("memory_mb", 4096)),
            "default_resolution": [int(CONFIG.get("width", 854)), int(CONFIG.get("height", 480))],
            "ms_client_id": CONFIG.get("microsoft_client_id") or "",
            "curseforge_api_key": CONFIG.get("curseforge_api_key") or "",
            "ai_mode": CONFIG.get("ai_mode") or "public",
            "ai_gateway_url": CONFIG.get("ai_gateway_url") or DEFAULT_GATEWAY_URL or "",
            "ai_base_url": CONFIG.get("ai_base_url") or "",
            "ai_api_key": CONFIG.get("ai_api_key") or "",
            "ai_model": CONFIG.get("ai_model") or DEFAULT_MODEL,
            "ai_confirm_writes": bool(CONFIG.get("ai_confirm_writes", True)),
            "ai_permission_mode": CONFIG.get("ai_permission_mode") or "standard",
            "download_source": CONFIG.get("download_source") or "auto",
            "community_source": CONFIG.get("community_source") or "auto",
            "use_system_proxy": bool(CONFIG.get("use_system_proxy", True)),
            "feedback_url": CONFIG.get("feedback_url") or DEFAULT_FEEDBACK_URL or "",
            "feedback_heartbeat": bool(CONFIG.get("feedback_heartbeat", True)),
            "feedback_consent": CONFIG.get("feedback_consent") is True,
            "ui_fly_animation": bool(CONFIG.get("ui_fly_animation", True)),
            "ui_motion": bool(CONFIG.get("ui_motion", True)),
            "ui_fly_duration_ms": int(CONFIG.get("ui_fly_duration_ms", 620)),
            "default_isolation": CONFIG.get("default_isolation") or "none",
            "default_jvm_args": CONFIG.get("default_jvm_args") or "",
            "default_priority": CONFIG.get("default_priority") or "normal",
            "update_url": CONFIG.get("update_url") or "",
            "theme_color": CONFIG.get("theme_color") or "#2E9B6B",
            "ui_dark": bool(CONFIG.get("ui_dark", False)),
            "ui_background": CONFIG.get("ui_background") or "",
            "global_mods_dir": CONFIG.get("global_mods_dir") or "",
            "launcher_visibility": CONFIG.get("launcher_visibility") or "keep",
            "gc_preset": CONFIG.get("gc_preset") or "auto",
            "download_limit_kbps": int(CONFIG.get("download_limit_kbps") or 0),
            "auto_check_update": bool(CONFIG.get("auto_check_update", True)),
            "custom_homepage": CONFIG.get("custom_homepage") or "",
            "homepage_mode": CONFIG.get("homepage_mode") or "news",
            "window_mode": CONFIG.get("window_mode") or "window",
            "skip_assets": bool(CONFIG.get("skip_assets", False)),
            "allow_multi_instance": bool(CONFIG.get("allow_multi_instance", False)),
            "first_run": bool(CONFIG.get("first_run", True)),
            "show_hidden_versions": bool(CONFIG.get("show_hidden_versions", False)),
            "offline_skin": CONFIG.get("offline_skin") or "default",
            "default_java": CONFIG.get("default_java") or "",
            "instances_dir": str(CONFIG.get("instances_dir") or ".minecraft"),
            "game_dir": str(CONFIG.instances_dir),
            "root": str(utils.ROOT),
        }

    def save_settings(self, data: dict):
        # 局部更新语义：`data` 里没有的键一律沿用 CONFIG 现值，绝不回落到硬编码默认。
        # 设置页 collect() 只提交 35 个键，早先几个键写死默认值，导致「点一次保存设置」
        # 就把没提交的键静默重置（ui_fly_duration_ms 就是这么被打回 620 的）。
        res = data.get("default_resolution") or [CONFIG.get("width", 854), CONFIG.get("height", 480)]

        def _keep(key, cfg_key=None, default=""):
            """提交了就用提交值（含清空），没提交就保持 CONFIG 现值。"""
            if key in data:
                return data[key]
            return CONFIG.get(cfg_key or key, default)

        perm_mode = (data.get("ai_permission_mode") if "ai_permission_mode" in data
                     else CONFIG.get("ai_permission_mode") or "standard")
        if perm_mode not in ("standard", "full"):
            perm_mode = "standard"

        CONFIG.update({
            "shared_libraries": bool(data.get("share_libraries", CONFIG.get("shared_libraries", False))),
            "shared_assets": bool(data.get("share_assets", CONFIG.get("shared_assets", False))),
            "download_threads": int(data.get("download_threads") or CONFIG.get("download_threads") or 8),
            "memory_mb": int(data.get("default_memory_mb") or CONFIG.get("memory_mb") or 4096),
            "width": int(res[0]),
            "height": int(res[1]),
            "microsoft_client_id": (data.get("ms_client_id") or "").strip()
            or CONFIG.get("microsoft_client_id"),
            "curseforge_api_key": str(_keep("curseforge_api_key") or "").strip(),
            "ai_mode": (data.get("ai_mode") or CONFIG.get("ai_mode") or "public"),
            "ai_gateway_url": str(_keep("ai_gateway_url") or "").strip(),
            "ai_base_url": str(_keep("ai_base_url") or "").strip(),
            "ai_api_key": (data.get("ai_api_key") if "ai_api_key" in data
                           else CONFIG.get("ai_api_key") or ""),
            "ai_model": (data.get("ai_model") or CONFIG.get("ai_model") or "deepseek-v4-flash"),
            "ai_confirm_writes": bool(data["ai_confirm_writes"]) if "ai_confirm_writes" in data
                                  else bool(CONFIG.get("ai_confirm_writes", True)),
            "ai_permission_mode": perm_mode,
            "download_source": (data.get("download_source") or CONFIG.get("download_source") or "auto"),
            "community_source": (data.get("community_source") or CONFIG.get("community_source") or "auto"),
            "use_system_proxy": bool(data.get("use_system_proxy", CONFIG.get("use_system_proxy", True))),
            "ui_fly_animation": bool(data.get("ui_fly_animation", CONFIG.get("ui_fly_animation", True))),
            "ui_motion": bool(data.get("ui_motion", CONFIG.get("ui_motion", True))),
            "ui_fly_duration_ms": int(data.get("ui_fly_duration_ms")
                                      or CONFIG.get("ui_fly_duration_ms") or 620),
            "default_isolation": (data.get("default_isolation") or CONFIG.get("default_isolation") or "none"),
            "default_jvm_args": (data.get("default_jvm_args") if "default_jvm_args" in data
                                 else CONFIG.get("default_jvm_args") or ""),
            "default_priority": (data.get("default_priority") or CONFIG.get("default_priority") or "normal"),
            "update_url": (data.get("update_url") if "update_url" in data
                           else CONFIG.get("update_url") or ""),
            "theme_color": (data.get("theme_color") or CONFIG.get("theme_color") or "#2E9B6B"),
            "ui_dark": bool(data.get("ui_dark", CONFIG.get("ui_dark", False))),
            "ui_background": (data.get("ui_background") if "ui_background" in data
                              else CONFIG.get("ui_background") or ""),
            "global_mods_dir": (data.get("global_mods_dir") if "global_mods_dir" in data
                                else CONFIG.get("global_mods_dir") or ""),
            "launcher_visibility": data.get("launcher_visibility") or CONFIG.get("launcher_visibility") or "keep",
            "gc_preset": data.get("gc_preset") or CONFIG.get("gc_preset") or "auto",
            "download_limit_kbps": int(_keep("download_limit_kbps", default=0) or 0),
            "auto_check_update": bool(data.get("auto_check_update", CONFIG.get("auto_check_update", True))),
            "custom_homepage": data.get("custom_homepage") if "custom_homepage" in data else CONFIG.get("custom_homepage") or "",
            "homepage_mode": data.get("homepage_mode") or CONFIG.get("homepage_mode") or "news",
            "window_mode": data.get("window_mode") or CONFIG.get("window_mode") or "window",
            "skip_assets": bool(data.get("skip_assets", CONFIG.get("skip_assets", False))),
            "allow_multi_instance": bool(
                data.get("allow_multi_instance", CONFIG.get("allow_multi_instance", False))),
            "first_run": bool(data["first_run"]) if "first_run" in data else bool(CONFIG.get("first_run", False)),
            "offline_skin": data.get("offline_skin") or CONFIG.get("offline_skin") or "default",
            "default_java": _keep("default_java"),
        })
        if "show_hidden_versions" in data:
            CONFIG.set("show_hidden_versions", bool(data.get("show_hidden_versions")))
        if "instances_dir" in data and str(data.get("instances_dir") or "").strip():
            CONFIG.set("instances_dir", str(data.get("instances_dir")).strip())
        if "feedback_url" in data:
            CONFIG.set("feedback_url", (data.get("feedback_url") or "").strip())
        if "feedback_heartbeat" in data:
            CONFIG.set("feedback_heartbeat", bool(data.get("feedback_heartbeat")))
        if "feedback_consent" in data:
            CONFIG.set("feedback_consent", bool(data.get("feedback_consent")))
        CONFIG.save()
        from mclauncher.source import invalidate_probe, warmup_async
        invalidate_probe()
        from mclauncher.net import apply_proxy_policy
        apply_proxy_policy()
        warmup_async()
        # 主题相关键变了就通知主窗口刷 UI（设置页开关也会直接调 apply_theme，双保险）。
        # self 相关收尾必须容忍 self=None：save_settings 只依赖模块级 CONFIG，
        # 测试（test_pcl_quality）按这个契约不构造真 backend 直接以 None 调进来。
        if self is not None:
            if "ui_dark" in data or "theme_color" in data or "ui_background" in data:
                self.theme_changed.emit()
            self._settings_cache = None
            self._settings_rev = -1

    def test_ai_connection(self, settings: dict | None = None) -> str:
        """试连 AI。传 settings 就用它，让设置页能测「还没保存的值」而不必先落盘。"""
        from mclauncher.ai.client import test_connection
        return test_connection(settings if settings is not None else self.get_settings())

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
        names = [tr("离线模式")]
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

    def remove_account(self, name: str):
        self.accounts.remove_account(name)
        self._emit_ui_changed()

    def set_active_account(self, name: str):
        self.accounts.set_active(name)
        self._emit_ui_changed()
        return self.accounts.active

    def add_offline_account(self, username: str, skin: str = ""):
        acc = self.accounts.offline_account(
            username, skin=skin or CONFIG.get("offline_skin") or "default")
        self.accounts.add_account({**acc, "type": "offline"})
        self._emit_ui_changed()
        return acc["name"]

    def start_authlib_login(self, api: str, username: str, password: str) -> str:
        return self.start_task(tr("皮肤站登录"), self._authlib_login_impl, api, username, password)

    def get_version_settings(self, instance: str, version: str) -> dict:
        from mclauncher import version_settings as vs
        return vs.load(self._instance(instance), version)

    def save_version_settings(self, instance: str, version: str, data: dict) -> dict:
        from mclauncher import version_settings as vs
        out = vs.save(self._instance(instance), version, data or {})
        self._emit_ui_changed()
        return out

    def repair_version(self, instance: str, version: str) -> str:
        return self.start_task(tr("修复 {0}").format(version), self._repair_impl, instance, version)

    def preflight_launch(self, instance: str, version: str, memory_mb: int = 0,
                         java: str = "") -> dict:
        from mclauncher import preflight as preflight_mod
        java_exe = ""
        if java and java not in (JAVA_AUTO, "auto", "default"):
            java_exe = self.normalize_java_pref(java)
            if java_exe == JAVA_AUTO:
                java_exe = ""
        return preflight_mod.check_launch(
            self._instance(instance), version,
            memory_mb=int(memory_mb or 0), java_exe=java_exe or "",
        )

    def apply_crash_action(self, action: dict, report: dict | None = None) -> dict:
        """执行崩溃弹窗里的一键修复建议。"""
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
                return {"ok": False,
                        "message": tr("未能禁用：{0}").format("; ".join(failed))}
            msg = tr("已禁用 {0} 个 Mod").format(len(done))
            if failed:
                msg += tr("；部分失败：{0}").format("; ".join(failed))
            return {"ok": True, "message": msg}

        if aid == "repair_version":
            if not version:
                return {"ok": False, "message": tr("报告里没有版本号，无法修复")}
            tid = self.repair_version(instance, version)
            return {"ok": True, "message": tr("已开始修复 {0}").format(version),
                    "task_id": tid}

        if aid == "need_java":
            major = int(action.get("major") or 17)
            tid = self.download_java(str(major), vendor="adoptium")
            return {"ok": True,
                    "message": tr("已开始下载 Java {0}").format(major),
                    "task_id": tid}

        if aid == "bump_memory":
            mb = int(action.get("memory_mb") or 6144)
            mb = max(1024, min(32768, mb))
            CONFIG.set("memory_mb", mb)
            CONFIG.save()
            self._emit_ui_changed()
            return {"ok": True, "message": tr("默认内存已设为 {0} MB").format(mb)}

        if aid == "open_mods_folder":
            inst = self._instance(instance)
            folder = self._mods_folder(inst, version)
            folder.mkdir(parents=True, exist_ok=True)
            open_path(folder)
            return {"ok": True, "message": tr("已打开 Mods 文件夹")}

        if aid == "open_crash_file":
            target = (action.get("path") or report.get("direct_file") or "").strip()
            if not target:
                return {"ok": False, "message": tr("没有可打开的崩溃文件")}
            from pathlib import Path as _P
            if not _P(target).is_file():
                return {"ok": False, "message": tr("文件不存在：{0}").format(target)}
            open_path(target)
            return {"ok": True, "message": tr("已打开崩溃报告")}

        if aid == "open_gpu_hint":
            tip = tr(
                "显卡/OpenGL 相关崩溃：请更新显卡驱动，关闭独显强制、"
                "超采样/滤镜，并确认不是远程桌面/虚拟机缺 OpenGL。"
            )
            return {"ok": True, "message": tip}

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
            self._emit_ui_changed()
            return {"ok": True, "message": tr("已清空自定义 JVM 参数")}

        return {"ok": False, "message": tr("未知动作: {0}").format(aid)}

    def export_modpack(self, instance: str, dest: str = "") -> str:
        return self.start_task(tr("导出整合包 {0}").format(instance), self._export_pack_impl, instance, dest)

    def check_mod_updates(self, instance: str) -> list:
        from mclauncher.mod_update import check_updates
        return check_updates(self._instance(instance))

    def start_mod_updates(self, instance: str) -> str:
        return self.start_task(tr("检查模组更新 {0}").format(instance), self._mod_update_impl, instance)

    def apply_mod_update(self, instance: str, row: dict) -> str:
        from mclauncher.mod_update import apply_update
        name = apply_update(self._instance(instance), row)
        self._emit_ui_changed()
        return name

    def cleaner_preview(self) -> dict:
        from mclauncher import cleaner as cleaner_mod
        return cleaner_mod.preview()

    def cleaner_apply(self, kinds=None) -> dict:
        from mclauncher import cleaner as cleaner_mod
        return cleaner_mod.apply(kinds)

    def check_update(self) -> dict:
        from mclauncher import updater as updater_mod
        return updater_mod.check()

    def start_self_update(self) -> str:
        return self.start_task(tr("更新启动器"), self._self_update_impl)

    def fetch_news(self) -> list:
        from mclauncher import news as news_mod
        return news_mod.fetch()

    def cached_news(self) -> list:
        from mclauncher import news as news_mod
        return news_mod.load_cached()

    def skin_urls(self, account_name: str = "") -> dict:
        from mclauncher import skin as skin_mod
        if not account_name or account_name == tr("离线模式"):
            acc = {"type": "offline", "name": "Steve"}
        else:
            acc = self.accounts.get_account(account_name) or {"type": "offline", "name": account_name}
        return {"avatar": skin_mod.avatar_url(acc), "body": skin_mod.body_url(acc)}

    def lan_hint(self, port: int = 25565) -> str:
        from mclauncher import lan as lan_mod
        return lan_mod.lan_hint(port)

    def local_ips(self) -> list:
        from mclauncher import lan as lan_mod
        return lan_mod.local_ips()

    def authlib_presets(self) -> list:
        from mclauncher.authlib import PRESETS
        return [{"name": a, "api": b} for a, b in PRESETS]

    def open_global_mods(self):
        from mclauncher import global_mods as gm
        path = gm.root()
        utils.ensure_dir(path)
        if os.name == "nt":
            os.startfile(path)
        else:
            subprocess.Popen(["xdg-open", str(path)])

    # ==================================================================
    # 对外 API（同步数据查询）
    # ==================================================================
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
            ids = self._instance(instance).installed_ids()
            if include_hidden or CONFIG.get("show_hidden_versions"):
                return ids
            inst = self._instance(instance)
            return [vid for vid in ids if not vs.load(inst, vid).get("hidden")]
        out = []
        for name in list_instances():
            for vid in Instance(name).installed_ids():
                out.append(f"{name} / {vid}")
        return out

    def get_instances(self) -> list[dict]:
        """实例快照（带 2.5s TTL 缓存）。

        每个使用方（启动页 reload / banner、六个资源页的实例下拉框、
        实例页）以前都是现场 iterdir + 逐实例读 meta/扫 versions，
        同一轮 UI 刷新里会被叫五六次。数据变更走 _emit_ui_changed
        立即失效，所以 TTL 只是把「没人改数据」时的重复扫描合并掉。
        """
        now = time.monotonic()
        if self._inst_cache is not None and now - self._inst_cache_at < 2.5:
            return self._inst_cache
        self._ensure_default_instance()
        rows = []
        for name in list_instances():
            inst = Instance(name)
            ids = inst.installed_ids()
            meta = inst.meta() or {}
            pack = meta.get("modpack") if isinstance(meta.get("modpack"), dict) else {}
            pack_name = pack.get("name") if pack else None
            mc = pack_name or meta.get("mc_version") or (ids[0] if ids else tr("未安装版本"))
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
        self._inst_cache = rows
        self._inst_cache_at = now
        return rows

    @staticmethod
    def _catalog_source(source: str) -> str:
        s = (source or "").strip().lower()
        if s in ("", tr("全部"), "all"):
            return "all"
        if s.startswith("curse"):
            return "curseforge"
        return "modrinth"

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

    def search_modpacks(self, query: str, source: str, extra: dict | None = None) -> list[dict]:
        """搜索整合包。extra 携带 game_version / category（下载页的筛选框）。"""
        extra = extra or {}
        src = self._catalog_source(source)
        q = (query or "").strip()
        from mclauncher.catalog_files import category_facets
        cats = category_facets(extra.get("category") or extra.get("type") or "")
        gv = extra.get("game_version") or extra.get("version") or ""
        if isinstance(gv, str) and gv.startswith(tr("全部")):
            gv = ""
        if not q:
            rows = []
            seen = set()
            for title, pack_src, key, slug in POPULAR_MODPACKS:
                if src != "all":
                    # CBC 始终置顶，避免只开着 Modrinth 页时装成 Create+
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
                    "description": tr("热门推荐") if key != CBC_CF_ID
                    else tr("Forge 1.20.1 黄铜协奏曲，不是 Create+/CDC"),
                    "tags": [tr("热门")],
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
            hits = modpack_mod.search_modpacks_chinese(
                dm, q, limit=25, api_key=key, game_version=gv or None,
                categories=cats or None)
        except Exception:
            hits = []
        if hits and any(h.get("matched_alias") for h in hits):
            rows = [self._modpack_row(h, src) for h in hits]
            self._pack_cache = rows
            return rows
        if not hits:
            try:
                if src == "curseforge":
                    hits = modpack_mod.search_cf_modpacks(
                        dm, q, limit=25, api_key=key, game_version=gv or None,
                        categories=cats or None)
                else:
                    hits = modpack_mod.modrinth_search(
                        dm, q, limit=25, game_version=gv or None,
                        categories=cats or None)
            except Exception:
                hits = []
        else:
            # 中文回退已混搜两端；当前页来源的结果排前面
            hits = sorted(
                hits,
                key=lambda h: 0 if (h.get("source") or src) == src else 1,
            )
        rows = [self._modpack_row(h, src) for h in hits]
        self._pack_cache = rows
        return rows

    def search_mods(self, query: str, source: str, extra: dict | None = None) -> list[dict]:
        src = self._catalog_source(source)
        q = (query or "").strip()
        if not q:
            rows = []
            for title, mod_src, key, *_rest in POPULAR_MODS:
                if src != "all" and mod_src != src:
                    continue
                rows.append({
                    "name": title,
                    "author": "CurseForge" if mod_src == "curseforge" else "Modrinth",
                    "downloads": 0,
                    "id": key if mod_src == "curseforge" else None,
                    "slug": None if mod_src == "curseforge" else key,
                    "source": mod_src,
                    "description": tr("热门推荐"),
                    "tags": [tr("热门")],
                    "icon_url": "",
                })
            self._mod_cache = rows
            return rows
        dm = DownloadManager(threads=2)
        extra = extra or {}
        gv = extra.get("game_version") or extra.get("version") or ""
        if isinstance(gv, str) and gv.startswith(tr("全部")):
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
                "source": h.get("source") or ("modrinth" if src == "all" else src),
                "description": h.get("description") or h.get("summary") or "",
                "tags": h.get("tags") or [],
                "updated": h.get("updated") or "",
                "icon_url": h.get("icon_url") or "",
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
            "icon_url": hit.get("icon_url") or "",
        }

    def _search_content(self, kind: str, query: str, source: str, extra: dict | None = None) -> list[dict]:
        spec = mods_mod.CONTENT_KINDS[kind]
        extra = extra or {}
        src = (source or "").lower()
        want_mr = src in ("", tr("全部"), "all", "modrinth")
        want_cf = src in ("", tr("全部"), "all") or src.startswith("curse")
        if src.startswith("modrinth"):
            want_cf = False
        if src.startswith("curse"):
            want_mr = False
        dm = DownloadManager(threads=2)
        rows = []
        q = (query or "").strip()
        gv = extra.get("game_version") or extra.get("version") or ""
        if isinstance(gv, str) and gv.startswith(tr("全部")):
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
        javas = java_mod.all_javas() if scan_system else java_mod.list_installed_javas()
        rows = []
        for j in javas:
            rows.append({
                "name": j.get("name") or f"Java {j.get('major')}",
                "major": str(j.get("major") or "?"),
                "path": j.get("exe") or j.get("path") or "",
            })
        return rows

    def normalize_java_pref(self, java: str) -> str:
        if not java or java in (JAVA_AUTO, "auto", "default"):
            return JAVA_AUTO
        for j in java_mod.cached_all_javas():
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
        # 只读缓存（后台预热灌满）：绝不在 UI 线程触发系统扫描。
        for j in java_mod.cached_all_javas():
            if j.get("exe") == stored:
                return f"Java {j.get('major') or '?'}"
        return Path(stored).name

    # ==================================================================
    # 真实实现
    # ==================================================================
    def _install_game_impl(self, progress, log, version, loader=tr("无"), loader_version="", instance="", extra=None):
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
        self._last_installed = {"instance": inst.name, "version": vid, "loader": loader or tr("无")}
        iso = CONFIG.get("default_isolation") or "none"
        if iso and iso != "none":
            from mclauncher import version_settings as vs
            vs.save(inst, vid, {"isolation": iso})
            log(f"已套用默认隔离: {iso}")
        log(f"版本 {vid} 安装完成")
        return f"已安装 {vid}"

    def _install_modpack_impl(self, progress, log, name, source, extra=None):
        extra = extra or {}
        inst = self._instance(extra.get("instance"))
        dm = self._dm(progress, log)
        path = extra.get("path") or name
        on_progress = dm.on_progress
        src_l = (source or "").lower()
        log(tr("整合包安装引擎：按声明的 Forge/Fabric 版本直装（不依赖残缺的 Maven 列表）"))

        if src_l.startswith(tr("本地")) or Path(str(path)).is_file():
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
                log(tr("目标包：机械动力：黄铜协奏曲（CBC），Minecraft 1.20.1 Forge。这不是 Create+ / CDC。"))
            elif str(addon_id) == str(CDC_CF_ID) or (slug or "") == CDC_CF_SLUG:
                log(tr("目标包：机械动力：齿轮盛宴（CDC），Minecraft 1.20.1 Forge。"))
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

    def _install_mod_impl(self, progress, log, name, instance, extra=None):
        extra = extra or {}
        inst = self._instance(instance or extra.get("instance"))
        dm = self._dm(progress, log)
        on_progress = dm.on_progress
        src_kind = (extra.get("source") or "").lower()
        vid = extra.get("version_id")
        fid = extra.get("file_id")
        gv = extra.get("game_version") or extra.get("mc_version")
        # extra["version"] 是安装目标版本（版本隔离时装进 versions/<id>/mods）
        target = str(extra.get("version") or "").strip()
        mods_dir = self._mods_folder(inst, target) if target else None
        if target:
            log(f"安装目标: {inst.name} / {target}")
        if extra.get("path") or extra.get("url"):
            source = extra.get("path") or extra.get("url")
            log(f"安装模组: {source}")
            mods_mod.install_mod_from_source(dm, str(source), inst, on_progress=on_progress,
                                             version_id=vid, mods_dir=mods_dir)
        elif src_kind.startswith("curse") and extra.get("id"):
            log(f"从 CurseForge 安装模组 id={extra.get('id')}" + (f" file={fid}" if fid else ""))
            mods_mod.install_curseforge_mod(
                dm, extra["id"], inst, mc_version=gv, on_progress=on_progress, file_id=fid,
                mods_dir=mods_dir)
        else:
            hit = extra if extra.get("slug") else self._lookup_mod(str(name), extra.get("source") or "Modrinth")
            if hit.get("id") and str(hit.get("source") or src_kind).lower().startswith("curse"):
                log(f"从 CurseForge 安装模组 id={hit.get('id')}")
                mods_mod.install_curseforge_mod(
                    dm, hit["id"], inst, mc_version=gv, on_progress=on_progress,
                    file_id=fid or extra.get("version_id"), mods_dir=mods_dir)
            else:
                slug = hit.get("slug") or name
                log(f"从 Modrinth 安装模组 {slug}" + (f" @{vid}" if vid else ""))
                mods_mod.install_mod_from_source(
                    dm, str(slug), inst, mc_version=gv, on_progress=on_progress,
                    version_id=vid, mods_dir=mods_dir)
        log(tr("模组安装完成"))

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
            save_name = extra.get("save") or extra.get("world")
            if save_name:
                from mclauncher import saves as saves_mod
                dest = saves_mod.install_datapack_into_save(
                    inst, (files or [name])[0], save_name, extra.get("version") or "")
                log(f"已放入存档: {dest}")
            else:
                log(tr("数据包已放到实例 datapacks 目录。可在存档管理里选世界安装进去。"))

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
        progress(1, 1, tr("启动内核"))
        terracotta_mod.start(log=log)
        return tr("陶瓦联机已就绪")

    @staticmethod
    def _account_kind(props, acc=None):
        if (props or {}).get("user_type") == "msa":
            return tr("正版")
        if (props or {}).get("authlib_api") or (acc or {}).get("type") == "authlib":
            return tr("皮肤站")
        if (props or {}).get("nide8_id") or (acc or {}).get("type") == "nide8":
            return tr("统一通行证")
        return tr("离线")

    def _launch_game_impl(self, progress, log, instance, version, account,
                          username, memory_mb, width, height, java=tr("自动选择"),
                          extra_game_args=None):
        if not version:
            raise LaunchError(tr("请先选择版本；还没有版本时，到「下载 → 原版游戏」安装"))
        # 多开检查
        allow_multi = bool(CONFIG.get("allow_multi_instance", False))
        if not allow_multi and self.is_game_running():
            raise LaunchError(tr("游戏正在运行中\n若要同时运行多个游戏，请到设置开启「允许多开」"))

        from mclauncher import preflight as preflight_mod
        java_exe_hint = ""
        if java and java != JAVA_AUTO:
            java_exe_hint = self.normalize_java_pref(java)
            if java_exe_hint == JAVA_AUTO:
                java_exe_hint = ""
        pf = preflight_mod.check_launch(
            self._instance(instance), version,
            memory_mb=int(memory_mb or 0), java_exe=java_exe_hint or "",
        )
        for it in pf.get("items") or []:
            lvl = it.get("level")
            line = f"[预检:{lvl}] {it.get('title')}: {it.get('detail')}"
            if lvl == "error":
                log(line)
            elif lvl == "warn":
                log(line)
        if not pf.get("ok", True):
            errs = [it for it in (pf.get("items") or []) if it.get("level") == "error"]
            msg = "\n\n".join(f"· {e.get('title')}\n{e.get('detail')}" for e in errs) or tr("启动预检未通过")
            raise LaunchError(tr("启动预检未通过") + "\n\n" + msg)

        inst = self._instance(instance)
        log(f"实例: {inst.name} | 版本: {version}")
        log(f"实例 Java 设置: {inst.java_pref()}")
        CONFIG.set("default_instance", inst.name)
        CONFIG.save()
        from mclauncher import launch_flow, version_settings as vs
        bound = vs.load(inst, version).get("login_account") or ""
        if bound:
            account = bound
            log(f"该版本绑定账号: {bound}")
        if account == tr("离线模式") or not account:
            acc = self.accounts.offline_account(
                username or "Player", skin=CONFIG.get("offline_skin") or "default")
        else:
            acc = self.accounts.get_account(account)
            if not acc:
                raise LaunchError(f"账号不存在: {account}")
            acc = self.accounts.ensure_valid(acc)
        props = self.accounts.launch_props(acc)
        log(f"账号: {props.get('name')} ({self._account_kind(props, acc)})")
        log(f"内存: {memory_mb} MB | 分辨率: {width}x{height}")

        mods_dir = inst.path / "mods"
        jar_count = 0
        if mods_dir.is_dir():
            jar_count = sum(1 for p in mods_dir.iterdir() if p.suffix.lower() == ".jar")
        looks_loader = any(tok in version.lower() for tok in (
            "forge", "fabric", "quilt", "neoforge", "optifine", "liteloader"))
        if jar_count and not looks_loader:
            log(f"警告: mods 里有 {jar_count} 个 jar，但当前版本是原版，不会加载模组")

        from mclauncher import launch_flow
        prep = launch_flow.prepare(inst, version, extra_game_args=extra_game_args, memory_mb=memory_mb)
        memory_mb = prep["memory_mb"] or memory_mb
        extra_game_args = prep["extra_game_args"]
        game_dir = prep["game_dir"]
        if prep["settings"].get("isolation") != "none":
            log(f"版本隔离: {prep['settings']['isolation']} → {game_dir}")
        if prep["global_mods"]:
            log(f"已应用 {prep['global_mods']} 个全局模组")
        launch_flow.run_hook(
            prep["settings"].get("pre_launch") or "", game_dir, log=log,
            wait=bool(prep.get("pre_launch_wait", True)))

        progress(1, 4, tr("检查 Java"))
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
        progress(2, 4, tr("构建启动参数"))
        auth_server = str(prep.get("auth_server") or "").strip()
        if auth_server and not props.get("authlib_api"):
            # 版本设置里的「认证服」：账号不是皮肤站时也按自定义 Yggdrasil 注入
            props = dict(props)
            props["authlib_api"] = auth_server
            log(f"认证服: {auth_server}")
        if props.get("authlib_api"):
            from mclauncher import authlib as authlib_mod
            authlib_mod.ensure_injector(self._dm(progress, log), on_note=log)
            log(f"皮肤站: {props.get('authlib_api')}")
        if props.get("nide8_id") or prep.get("nide8_id"):
            from mclauncher import nide8 as nide8_mod
            nide8_mod.ensure_jar(self._dm(progress, log), on_note=log)
            if prep.get("nide8_id") and not props.get("nide8_id"):
                props = dict(props)
                props["nide8_id"] = prep["nide8_id"]
            log(f"统一通行证: {props.get('nide8_id')}")
        width, height = launch_flow.resolve_resolution(prep, width, height)
        cmd, _natives, _vdir, game_dir = build_launch_command(
            inst, version, props, java_exe,
            memory_mb=memory_mb, width=width, height=height,
            extra_game_args=extra_game_args,
            extra_jvm_args=prep["jvm_args"],
            game_directory=game_dir,
            authlib_api=props.get("authlib_api"),
        )
        log(f"实际启动: {cmd[0]}")
        log(tr("正在启动游戏进程…"))
        progress(3, 4, tr("游戏启动中"))
        worker = QThread.currentThread()
        proc = GameProcess(cmd, cwd=game_dir, on_line=log, priority=prep["priority"],
                           window_title=prep.get("window_title") or "")
        with self._game_lock:
            self._game_proc = proc
        self.game_started.emit()
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
            self.game_exited.emit(code)
        if getattr(worker, "_cancelled", False):
            log(tr("已停止游戏"))
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
        return tr("已正常退出")

    def _microsoft_login_impl(self, progress, log):
        client_id = CONFIG.get("microsoft_client_id") or "00000000402b5328"
        auth = MicrosoftAuthenticator(client_id=client_id)
        worker = QThread.currentThread()

        def on_code(code, uri, exp):
            if hasattr(worker, "login_code"):
                worker.login_code.emit(code, uri)
            log(f"请打开 {uri} 并输入代码 {code}（{exp // 60} 分钟内有效）")

        def on_status(s):
            if hasattr(worker, "login_status"):
                worker.login_status.emit(str(s))
            log(str(s))
            progress(0, 0, str(s))

        account = auth.login(on_code=on_code, on_status=on_status, open_browser=True)
        self.accounts.add_account(account)
        log(f"登录成功：{account.get('name')}")

    def _authlib_login_impl(self, progress, log, api, username, password):
        from mclauncher import authlib as authlib_mod
        progress(0, 0, tr("下载 authlib-injector"))
        authlib_mod.ensure_injector(self._dm(progress, log), on_note=log)
        progress(1, 2, tr("登录皮肤站"))
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

    def _export_pack_impl(self, progress, log, instance, dest):
        from mclauncher.export_pack import export_mrpack
        inst = self._instance(instance)
        if not dest:
            dest = str(utils.ROOT / "exports" / f"{inst.name}.mrpack")
        dm = self._dm(progress, log)
        path = export_mrpack(inst, dest, dm=dm, on_note=lambda m, a, b: progress(a, b, m))
        log(f"已导出: {path}")
        return path

    def _mod_update_impl(self, progress, log, instance):
        from mclauncher.mod_update import apply_update, check_updates
        inst = self._instance(instance)
        dm = self._dm(progress, log)
        rows = check_updates(inst, dm=dm)
        if not rows:
            log(tr("已装模组都是最新"))
            return tr("没有可更新的模组")
        for i, row in enumerate(rows):
            log(f"更新 {row.get('name')} {row.get('current')} → {row.get('latest')}")
            apply_update(inst, row, dm=dm)
            progress(i + 1, len(rows), row.get("name") or "")
        return f"已更新 {len(rows)} 个模组"

    def _self_update_impl(self, progress, log):
        from mclauncher import updater as updater_mod
        info = updater_mod.check(self._dm(progress, log))
        if not info.get("has_update"):
            return info.get("message") or tr("已是最新")
        log(info.get("message") or tr("发现更新"))
        path = updater_mod.download(info, self._dm(progress, log))
        msg = updater_mod.apply_exe(path)
        log(msg)
        return msg

    def _nide8_login_impl(self, progress, log, server_id, username, password):
        from mclauncher import nide8 as nide8_mod
        progress(0, 0, tr("下载 nide8auth"))
        nide8_mod.ensure_jar(self._dm(progress, log), on_note=log)
        progress(1, 2, tr("登录统一通行证"))
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
        log(f"安装世界到 {inst.name}/saves")
        result = worlds_mod.install_world(dm, extra, inst, on_progress=dm.on_progress)
        files = (result or {}).get("files") or []
        log(f"完成: {', '.join(files) or name}")
        return f"已安装世界 {', '.join(files) or name}"

    def _export_bat_impl(self, progress, log, instance, version, dest):
        from mclauncher import launch_flow, version_ops as vops
        inst = self._instance(instance)
        acc = self.accounts.get_active() and self.accounts.get_account(self.accounts.active)
        if not acc:
            acc = self.accounts.offline_account("Player")
        props = self.accounts.launch_props(acc)
        prep = launch_flow.prepare(inst, version, memory_mb=int(CONFIG.get("memory_mb") or 4096))
        java_exe = java_mod.resolve_launch_java(inst.version_json(version) or {}, on_note=log)
        auth_server = str(prep.get("auth_server") or "").strip()
        if auth_server and not props.get("authlib_api"):
            props = dict(props)
            props["authlib_api"] = auth_server
        if props.get("authlib_api"):
            from mclauncher import authlib as authlib_mod
            authlib_mod.ensure_injector(self._dm(progress, log), on_note=log)
        if props.get("nide8_id"):
            from mclauncher import nide8 as nide8_mod
            nide8_mod.ensure_jar(self._dm(progress, log), on_note=log)
        cmd, _n, _v, gdir = build_launch_command(
            inst, version, props, java_exe,
            memory_mb=prep["memory_mb"] or 4096,
            extra_game_args=prep["extra_game_args"],
            extra_jvm_args=prep["jvm_args"],
            game_directory=prep["game_dir"],
            authlib_api=props.get("authlib_api"),
        )
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
        """导入服务器列表：JSON 数组（UI 导入对话框允许选 .json）或逐行文本。"""
        from mclauncher import servers as servers_mod
        import json as _json
        stripped = (text or "").lstrip()
        if stripped.startswith("["):
            try:
                data = _json.loads(text)
            except _json.JSONDecodeError:
                data = None
            if isinstance(data, list):
                return servers_mod.import_servers_json(self._instance(instance), data)
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
        """异步下载 Java 运行时。"""
        return self.start_task(
            tr("下载 Java {0}（{1}）").format(major, vendor),
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
        self._emit_ui_changed()

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
        result = themes_mod.load_theme(name)
        self.theme_changed.emit()
        self._emit_ui_changed()
        return result

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

    def migrate_official_launcher(self, instance: str = "default") -> str:
        return self.start_task(
            tr("导入官方启动器"),
            self._migrate_official_impl, instance,
        )

    def _migrate_official_impl(self, progress, log, instance):
        from mclauncher import official_migrate as om
        src = om.official_dir()
        if not src:
            raise FileNotFoundError(tr("未找到官方启动器目录"))
        log(f"正在从 {src} 迁移…")
        progress(1, 3, tr("扫描版本"))
        versions = om.scan_versions(src)
        if not versions:
            log(tr("未发现版本"))
            return tr("无版本可导入")
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
            raise LaunchError(tr("没有可上传的崩溃报告"))
        from mclauncher import feedback as fb
        result = fb.submit_crash(report)
        return result.get("message") or tr("已上传")

    # ==================================================================
    # 新增 API：启动命令展示
    # ==================================================================

    def get_launch_command(self, instance: str, version: str, account: str = "",
                           username: str = "", memory_mb: int = 0) -> str:
        """获取完整的启动命令文本（不实际启动游戏）。"""
        from mclauncher import launch_flow, version_ops as vops
        from mclauncher.launcher import build_launch_command
        inst = self._instance(instance)
        if not version:
            raise LaunchError(tr("请先选择版本"))
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
            raise LaunchError(tr("无法确定 Java 路径"))
        if not account or account == tr("离线模式"):
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
        """根据硬件配置提供推荐值。"""
        from mclauncher.sysinfo import get_smart_recommendation
        return get_smart_recommendation()
