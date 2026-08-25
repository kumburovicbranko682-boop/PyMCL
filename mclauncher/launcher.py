# -*- coding: utf-8 -*-
"""启动游戏：构建 JVM/游戏参数并运行。"""
import ctypes
import os
import subprocess
import threading

from . import APP_ID, LAUNCHER_NAME, LAUNCHER_VERSION
from . import java as java_mod
from . import manifest, utils
from .argsplit import split_args
from .config import CONFIG
from .downloader import DownloadError
from .installer import extract_natives, natives_present, select_native_classifier


class LaunchError(Exception):
    pass


def _version_type(version_id, resolved):
    """尽量给出正确的 --versionType。"""
    cached = utils.read_json(utils.ROOT / "cache" / "version_manifest.json", None)
    if cached:
        for v in cached.get("versions", []):
            if v.get("id") == version_id:
                return v.get("type", "release")
    return resolved.get("type") or "custom"


_JVM_VALUE_FLAGS = {
    "-p", "-cp", "-classpath", "--class-path", "--module-path",
    "--add-modules", "--add-opens", "--add-exports", "--add-reads",
}


def _expand_args(args_raw, placeholders, features):
    out = []
    for entry in args_raw or []:
        if isinstance(entry, str):
            val = utils.replace_placeholders(entry, placeholders)
            if not utils.has_placeholder(val):
                out.append(val)
        elif isinstance(entry, dict):
            if not utils.check_rules(entry.get("rules"), features):
                continue
            val = entry.get("value", [])
            vals = val if isinstance(val, list) else [val]
            for v in vals:
                v = utils.replace_placeholders(v, placeholders)
                if not utils.has_placeholder(v):
                    out.append(v)
    return _drop_orphan_jvm_flags(out)


def _drop_orphan_jvm_flags(args):
    """丢掉后面没有取值的 -p / -cp 等，避免 Java 报 Unrecognized option。"""
    out = []
    skip_next = False
    for i, a in enumerate(args):
        if skip_next:
            skip_next = False
            continue
        if a in _JVM_VALUE_FLAGS:
            nxt = args[i + 1] if i + 1 < len(args) else ""
            if not nxt or nxt.startswith("-"):
                continue
            out.append(a)
            out.append(nxt)
            skip_next = True
            continue
        out.append(a)
    return out


def _coerce_java_exe(resolved, java_exe):
    """启动前最后一次核对：带 -p / BootstrapLauncher 时绝不使用 Java 8。"""
    need = java_mod.required_java_major(resolved)
    if java_mod.java_usable_for(resolved, java_exe):
        return str(java_exe)
    alt = java_mod.pick_java_for_version(resolved)
    if java_mod.java_usable_for(resolved, alt):
        return str(alt)
    for j in java_mod.list_installed_javas() + java_mod.find_system_javas():
        cand = j.get("exe")
        if java_mod.java_usable_for(resolved, cand):
            return str(cand)
    got = java_mod.get_java_major(java_exe) if java_exe else None
    raise LaunchError(
        f"Java {got or '?'} 无法启动此版本（需要 Java {need}+）。"
        "Forge 1.17+ 会向 JVM 传入 --module-path，Java 8 会报 Unrecognized option: -p。"
        "请到「Java」页下载 Java 17，启动页 Java 选「自动选择」。"
    )


def _patch_ignore_list(jvm_args, filenames):
    """把客户端 jar 等补进 -DignoreList=。

    BootstrapLauncher 按「文件名是否以某前缀开头」决定 jar 是否进入 MC-BOOTSTRAP
    模块层。原版 1.19.2.jar 若不排除，会变成自动模块 _1._19._2，和 minecraft
    模块同时导出 com.mojang.blaze3d.systems，启动直接失败。
    """
    names = []
    for n in filenames or []:
        base = os.path.basename(str(n or "").strip())
        if base and base not in names:
            names.append(base)
    if not names:
        return jvm_args
    out = []
    for a in jvm_args:
        if isinstance(a, str) and a.startswith("-DignoreList="):
            prefixes = [p for p in a.split("=", 1)[1].split(",") if p]
            for name in names:
                if not any(name.startswith(p) for p in prefixes):
                    prefixes.append(name)
            a = "-DignoreList=" + ",".join(prefixes)
        out.append(a)
    return out


def _ignore_list_jars(jar, resolved, classpath_files):
    names = [getattr(jar, "name", None) or os.path.basename(str(jar))]
    parent = resolved.get("inheritsFrom") or resolved.get("jar")
    if parent:
        names.append(f"{parent}.jar")
    for p in classpath_files or []:
        base = os.path.basename(str(p))
        if base.endswith("-extra.jar"):
            names.append(base)
    return names


def _apply_memory(jvm_args, memory_mb):
    memory_mb = max(512, int(memory_mb or 0))
    xms = min(memory_mb // 2, 1024)
    out = [a for a in jvm_args if not a.startswith(("-Xmx", "-Xms"))]
    out.append(f"-Xmx{memory_mb}M")
    out.append(f"-Xms{xms}M")
    return out


def _client_jar_path(instance, version_id, vjson, resolved):
    """Forge 旧版通常没有独立 client jar，沿用原版 1.7.10.jar 或 json 里的 jar 字段。"""
    vdir = instance.versions_dir() / version_id
    jar = vdir / f"{version_id}.jar"
    if jar.is_file():
        return jar
    for jar_id in (vjson.get("jar"), resolved.get("jar"), vjson.get("inheritsFrom"),
                   resolved.get("inheritsFrom")):
        if not jar_id:
            continue
        alt = instance.versions_dir() / jar_id / f"{jar_id}.jar"
        if alt.is_file():
            return alt
    return jar


def _set_priority(pid: int, level: str):
    if os.name != "nt" or not pid:
        return
    mapping = {
        "low": 0x00000040,
        "below": 0x00004000,
        "normal": 0x00000020,
        "high": 0x00000080,
        "realtime": 0x00000100,
    }
    value = mapping.get(str(level or "normal").lower())
    if value is None or value == 0x00000020:
        return
    PROCESS_SET_INFORMATION = 0x0200
    handle = ctypes.windll.kernel32.OpenProcess(PROCESS_SET_INFORMATION, False, int(pid))
    if not handle:
        return
    try:
        ctypes.windll.kernel32.SetPriorityClass(handle, value)
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


_GAME_WINDOW_CLASSES = ("LWJGL", "GLFW30")


def _visible_windows_of(pid: int):
    """按 pid 找可见的顶层窗口，优先 LWJGL/GLFW 这类游戏主窗口。"""
    user32 = ctypes.windll.user32
    hits = []

    proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def visit(hwnd, _lparam):
        owner = ctypes.c_ulong(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value != pid or not user32.IsWindowVisible(hwnd):
            return True
        if user32.GetWindowTextLengthW(hwnd) <= 0:
            return True
        buf = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, buf, 64)
        hits.append((buf.value in _GAME_WINDOW_CLASSES, hwnd))
        return True

    user32.EnumWindows(proc(visit), 0)
    hits.sort(key=lambda x: not x[0])
    return [h for _preferred, h in hits]


def watch_window_title(proc, title: str, timeout: float = 90.0):
    """把游戏窗口标题改成自定义值。

    Minecraft 自己会在启动过程中反复设置标题，只改一次会被覆盖，
    所以在窗口出现后的一段时间内持续回写，超时或进程退出即停。
    """
    title = (title or "").strip()
    if os.name != "nt" or not title or proc is None:
        return

    def loop():
        import time as _time
        user32 = ctypes.windll.user32
        deadline = _time.time() + timeout
        pid = proc.pid
        hwnd = None
        while _time.time() < deadline and proc.poll() is None:
            if hwnd is None or not user32.IsWindow(hwnd):
                found = _visible_windows_of(pid)
                hwnd = found[0] if found else None
            if hwnd:
                user32.SetWindowTextW(hwnd, title)
            _time.sleep(1.0)

    threading.Thread(target=loop, daemon=True).start()


def build_launch_command(instance, version_id, account_props, java_exe,
                         memory_mb=4096, width=None, height=None,
                         extra_game_args=None, extra_jvm_args=None,
                         game_directory=None, authlib_api=None, wrapper=None):
    """
    构建启动命令。返回 (cmd, natives_dir, version_dir, game_dir)。
    account_props: {'name', 'uuid', 'token', 'user_type', 'xuid'}
    wrapper: 包装器命令（字符串或 argv 列表），挂在 java 前执行，
             如 Linux 上的 optirun / gamemoderun / mangohud。
    """
    vjson = instance.version_json(version_id)
    if not vjson:
        raise LaunchError(f"版本 {version_id} 未安装，请先安装。")

    def load_parent(pid):
        p = instance.version_json(pid)
        if not p:
            raise LaunchError(f"缺少被继承的父版本 {pid}，请重新安装 {version_id}。")
        return p

    try:
        resolved = manifest.resolve_inherits(vjson, load_parent)
    except DownloadError as e:
        raise LaunchError(str(e))

    java_exe = _coerce_java_exe(resolved, java_exe)

    vdir = instance.versions_dir() / version_id
    jar = _client_jar_path(instance, version_id, vjson, resolved)
    if not jar.is_file():
        raise LaunchError(f"客户端 jar 缺失: {jar}\n请重新安装版本 {version_id}。")

    assets_idx = resolved.get("assetIndex") or {}
    assets_id = assets_idx.get("id", "legacy")
    assets_dir = instance.assets_dir()
    libs_dir = instance.libraries_dir()
    natives_dir = extract_natives(instance, resolved, version_id)
    needs_natives = any(
        select_native_classifier(lib)
        for lib in resolved.get("libraries") or []
        if lib.get("clientreq") is not False and utils.check_rules(lib.get("rules"))
    )
    if needs_natives and not natives_present(natives_dir):
        raise LaunchError(
            "缺少 LWJGL 本地库（natives）。请重新安装该 Minecraft 版本后再启动。"
        )

    # ---- classpath（同名库保留后出现的路径，位置仍在第一次出现处）
    cp_by_id = {}
    cp_order = []
    for lib in resolved.get("libraries", []):
        if lib.get("clientreq") is False:
            continue
        if not utils.check_rules(lib.get("rules")):
            continue
        name = lib.get("name")
        if not name:
            continue
        downloads = lib.get("downloads") or {}
        artifact = downloads.get("artifact")
        if artifact and artifact.get("path"):
            path = str(libs_dir / artifact["path"])
        elif not lib.get("natives"):
            path = str(libs_dir / utils.maven_artifact_path(name))
        else:
            continue
        key = manifest.library_identity(lib)
        if key not in cp_by_id:
            cp_order.append(key)
        cp_by_id[key] = path
    cp = [cp_by_id[k] for k in cp_order]
    cp.append(str(jar))
    classpath = os.pathsep.join(cp)

    main_class = resolved.get("mainClass") or "net.minecraft.client.main.Main"

    # ---- 占位符
    props = account_props or {}
    auth_uuid = utils.dashed_uuid(props.get("uuid") or "") or "00000000-0000-0000-0000-000000000000"
    auth_token = props.get("token") or "0"
    placeholders = {
        "auth_player_name": props.get("name", "Player"),
        "auth_uuid": auth_uuid,
        "auth_access_token": auth_token,
        "auth_session": f"token:{auth_token}:{auth_uuid}",
        "user_type": props.get("user_type", "legacy"),
        "user_properties": "{}",
        "auth_xuid": props.get("xuid", ""),
        "clientid": CONFIG.get("microsoft_client_id") or APP_ID,
        "version_name": version_id,
        "version_type": _version_type(version_id, resolved),
        "game_directory": str(game_directory or instance.path),
        "assets_root": str(assets_dir),
        "assets_index_name": assets_id,
        "game_assets": str(assets_dir / "virtual" / assets_id),
        "natives_directory": str(natives_dir),
        "classpath": classpath,
        "library_directory": str(libs_dir),
        "classpath_separator": os.pathsep,
        "launcher_name": LAUNCHER_NAME,
        "launcher_version": LAUNCHER_VERSION,
        "resolution_width": str(width or 854),
        "resolution_height": str(height or 480),
    }
    features = {"is_demo_user": False, "has_custom_resolution": bool(width or height)}

    # ---- 参数
    if resolved.get("arguments"):
        argdef = resolved["arguments"]
        jvm_args = _expand_args(argdef.get("jvm", []), placeholders, features)
        game_args = _expand_args(argdef.get("game", []), placeholders, features)
        if not any(a.startswith("-Djava.library.path") for a in jvm_args):
            jvm_args.insert(0, f"-Djava.library.path={natives_dir}")
        if "-cp" not in jvm_args and "--class-path" not in jvm_args:
            jvm_args += ["-cp", classpath]
        mine_args_text = ""
    else:
        mine_args_text = resolved.get("minecraftArguments") or ""
        game_args = [
            a for a in mine_args_text.split(" ")
            if a and not utils.has_placeholder(utils.replace_placeholders(a, placeholders))
        ]
        game_args = [utils.replace_placeholders(a, placeholders) for a in game_args]
        jvm_args = [f"-Djava.library.path={natives_dir}", "-cp", classpath]

    if any(isinstance(a, str) and a.startswith("-DignoreList=") for a in jvm_args):
        jvm_args = _patch_ignore_list(jvm_args, _ignore_list_jars(jar, resolved, cp))

    # 旧版 Forge（tweakClass）
    if "tweakClass" in mine_args_text or any("tweakClass" in a for a in game_args):
        jvm_args += ["-Dfml.ignoreInvalidMinecraftCertificates=true",
                     "-Dfml.ignorePatchDiscrepancies=true"]

    # 远古版本跑在 32 位 Java 上，堆内存上限约 1G，防止启动失败
    if manifest.is_legacy_version(resolved):
        memory_mb = min(int(memory_mb or 1024), 1024)
    jvm_args = ["--module-path" if a == "-p" else a for a in jvm_args]
    jvm_args = _apply_memory(jvm_args, memory_mb)

    if not any("-Dminecraft.launcher.brand" in a for a in jvm_args):
        jvm_args.append(f"-Dminecraft.launcher.brand={LAUNCHER_NAME}")
    if not any("-Dminecraft.launcher.version" in a for a in jvm_args):
        jvm_args.append(f"-Dminecraft.launcher.version={LAUNCHER_VERSION}")

    # 日志配置
    logcfg = (resolved.get("logging") or {}).get("client")
    if logcfg and (logcfg.get("file") or {}).get("id"):
        lp = assets_dir / "log_configs" / logcfg["file"]["id"]
        if lp.is_file() and not any("log4j.configurationFile" in a for a in jvm_args):
            jvm_args.append(f"-Dlog4j.configurationFile={lp}")

    extras = [str(a) for a in (extra_game_args or []) if a not in (None, "")]
    extra_jvm = []
    if extra_jvm_args:
        extra_jvm = list(extra_jvm_args) if isinstance(extra_jvm_args, (list, tuple)) else split_args(extra_jvm_args)
    default_jvm = split_args(CONFIG.get("default_jvm_args") or "")
    jvm_args = default_jvm + extra_jvm + jvm_args
    jvm_args = _apply_memory(jvm_args, memory_mb)
    if authlib_api:
        from . import authlib as authlib_mod
        agent = authlib_mod.javaagent_arg(authlib_api)
        jvm_args = [agent] + [a for a in jvm_args if not str(a).startswith("-javaagent:")]
    nide8_id = (account_props or {}).get("nide8_id")
    if nide8_id:
        from . import nide8 as nide8_mod
        agent = nide8_mod.javaagent_arg(nide8_id)
        jvm_args = [agent] + [a for a in jvm_args if not str(a).startswith("-javaagent:")]
    cmd = [str(java_exe)] + jvm_args + [main_class] + game_args + extras
    major = java_mod.get_java_major(java_exe)
    if major is not None and major < 9 and any(a in ("-p", "--module-path", "--add-modules") for a in cmd):
        raise LaunchError(
            f"拒绝用 Java {major} 启动：命令含模块参数。请改用 Java 17。"
        )
    if wrapper:
        wrap = list(wrapper) if isinstance(wrapper, (list, tuple)) else split_args(wrapper)
        cmd = [str(a) for a in wrap if a not in (None, "")] + cmd
    return cmd, natives_dir, vdir, game_directory or instance.path


class GameProcess:
    """运行中的游戏进程，支持读取输出与终止。"""

    def __init__(self, cmd, cwd, on_line=None, env=None, priority="normal", window_title=""):
        import collections
        import time as _time
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if utils.IS_WINDOWS else 0
        self.proc = subprocess.Popen(
            cmd,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            env=env or os.environ.copy(),
            creationflags=creationflags,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        _set_priority(self.proc.pid, priority)
        watch_window_title(self.proc, window_title)
        self.on_line = on_line
        self.started_at = _time.time()
        self.lines = collections.deque(maxlen=200)
        self._thread = threading.Thread(target=self._reader, daemon=True)
        self._thread.start()

    def last_lines(self):
        return list(self.lines)

    def _reader(self):
        try:
            for line in self.proc.stdout:
                line = line.rstrip("\n")
                self.lines.append(line)
                if self.on_line:
                    try:
                        self.on_line(line)
                    except Exception:
                        pass
        except Exception:
            pass

    def poll(self):
        return self.proc.poll()

    def wait(self, timeout=None):
        code = self.proc.wait(timeout=timeout)
        if self._thread:
            self._thread.join(timeout=2)
        return code

    def kill(self):
        try:
            self.proc.kill()
        except Exception:
            pass
