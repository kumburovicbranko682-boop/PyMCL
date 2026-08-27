# -*- coding: utf-8 -*-
"""Minecraft 崩溃分析：流程与规则对齐 PCL2 ModCrash.vb。"""

from __future__ import annotations

import re
import time
import zipfile
from datetime import datetime
from pathlib import Path

from mclauncher import utils

HELP_FOOTER = (
    "如果要寻求帮助，请把错误报告文件发给对方，而不是发送这个窗口的照片或者截图。"
)

_STACK_IGNORE_PREFIX = (
    "java", "sun", "javax", "jdk", "oolloo",
    "org.lwjgl", "com.sun", "net.minecraftforge", "paulscode.sound",
    "com.mojang", "net.minecraft", "cpw.mods", "com.google", "org.apache",
    "org.spongepowered", "net.fabricmc", "com.mumfrey",
    "com.electronwill.nightconfig", "it.unimi.dsi",
    "MojangTricksIntelDriversForPerformance_javaw",
)

_STACK_IGNORE_WORDS = {
    "com", "org", "net", "asm", "fml", "mod", "jar", "sun", "lib", "map", "gui",
    "dev", "nio", "api", "dsi", "top", "mcp", "core", "init", "mods", "main",
    "file", "game", "load", "read", "done", "util", "tile", "item", "base",
    "fake", "oshi", "impl", "data", "pool", "task", "forge", "setup", "block",
    "model", "mixin", "event", "unimi", "netty", "world", "lwjgl", "fakes",
    "fabric", "gitlab", "common", "server", "config", "mixins", "compat",
    "loader", "launch", "script", "entity", "assist", "client", "plugin",
    "modapi", "mojang", "shader", "events", "github", "recipe", "render",
    "packet", "preinit", "preload", "machine", "reflect", "channel", "general",
    "handler", "content", "systems", "modules", "service", "scripts", "network",
    "fastutil", "optifine", "internal", "platform", "override", "fabricmc",
    "neoforge", "external", "injection", "listeners", "scheduler", "minecraft",
    "universal", "multipart", "neoforged", "microsoft", "transformer",
    "transformers", "minecraftforge", "blockentity", "spongepowered",
    "electronwill", "concurrent",
}

_TOKEN_RE = re.compile(
    r"(?i)((?:access[_-]?token|session[_-]?token|refresh[_-]?token"
    r"|client[_-]?secret|authorization)\s*[:=]\s*)([^\s\"']+)"
)
_UUID_AUTH_RE = re.compile(
    r"(?i)(--(?:accessToken|uuid|xuid|clientId)\s+)\S+"
)


class GameCrashError(Exception):
    """游戏进程异常退出，携带分析报告。"""

    def __init__(self, report: dict):
        self.report = report or {}
        super().__init__(self.report.get("summary") or "游戏崩溃")


def normalize_exit(code) -> int | None:
    if code is None:
        return None
    try:
        n = int(code)
    except (TypeError, ValueError):
        return None
    if n > 0x7FFFFFFF:
        n -= 0x100000000
    return n


def exit_hint(code) -> str:
    n = normalize_exit(code)
    if n is None:
        return ""
    nt = {
        0xC0000005: "内存访问冲突，常见于显卡驱动或原生库",
        0xC0000409: "栈缓冲溢出，常见于 NVIDIA/显卡驱动",
        0xC0000135: "找不到 DLL，缺运行库",
        0xC000013A: "进程被关闭（Ctrl+C 或点了控制台关闭）",
        0xC0000142: "DLL 初始化失败",
        0xC0000374: "堆损坏",
        0xC00000FD: "栈溢出",
    }
    unsigned = n & 0xFFFFFFFF
    if unsigned in nt:
        return f"退出码 {n}（{unsigned:#010x}）：{nt[unsigned]}"
    if n == 0:
        return ""
    if n == 1:
        return "退出码 1：一般错误"
    if n < 0:
        return f"退出码 {n}（{unsigned:#010x}）"
    return f"退出码 {n}"


def _inst_path(instance) -> Path:
    if hasattr(instance, "path"):
        return Path(instance.path)
    return Path(instance)


def _inst_name(instance) -> str:
    if hasattr(instance, "name"):
        return str(instance.name)
    return Path(instance).name


def read_text(path: Path, max_bytes: int = 0) -> str:
    try:
        data = Path(path).read_bytes()
    except OSError:
        return ""
    if max_bytes and len(data) > max_bytes:
        data = data[-max_bytes:]
    for enc in ("utf-8-sig", "utf-8", "gb18030", "gbk"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def filter_secrets(text: str) -> str:
    if not text:
        return ""
    text = _TOKEN_RE.sub(r"\1***", text)
    text = _UUID_AUTH_RE.sub(r"\1***", text)
    return text


def seek(text: str, pat: str, flags: int = 0) -> str:
    if not text:
        return ""
    m = re.search(pat, text, flags)
    return m.group(0) if m else ""


def search_all(text: str, pat: str, flags: int = 0) -> list[str]:
    if not text:
        return []
    return [m.group(0) for m in re.finditer(pat, text, flags)]


def between(text: str, left: str, right: str) -> str:
    if not text:
        return ""
    i = text.find(left)
    if i < 0:
        return ""
    i += len(left)
    j = text.find(right, i)
    return text[i:] if j < 0 else text[i:j]


def before_first(text: str, sep: str) -> str:
    if not text:
        return ""
    i = text.find(sep)
    return text if i < 0 else text[:i]


def after_last(text: str, sep: str) -> str:
    if not text:
        return ""
    i = text.rfind(sep)
    return "" if i < 0 else text[i + len(sep):]


def head_tail_lines(raw: list[str], head: int, tail: int) -> str:
    lines = [ln.rstrip("\r") for ln in raw if ln and ln.strip()]
    if len(lines) <= head + tail:
        seen, out = set(), []
        for ln in lines:
            if ln in seen:
                continue
            seen.add(ln)
            out.append(ln)
        return "\n".join(out)
    seen, front, back = set(), [], []
    for ln in lines:
        if ln in seen:
            continue
        seen.add(ln)
        front.append(ln)
        if len(front) >= head:
            break
    for ln in reversed(lines):
        if ln in seen:
            continue
        seen.add(ln)
        back.append(ln)
        if len(back) >= tail:
            break
    back.reverse()
    return "\n".join(front + back)


def _fresh(path: Path, started_at: float | None) -> bool:
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return False
    now = time.time()
    if abs(now - mtime) < 180:
        return True
    if started_at is not None and mtime >= started_at - 15:
        return True
    return False


# ---------------------------------------------------------------------------
# 收集
# ---------------------------------------------------------------------------

def collect_files(instance_path: Path, started_at: float | None = None, loose: bool = False,
                 extra_roots=None) -> list[Path]:
    found: list[Path] = []

    def add(p: Path):
        try:
            if p.is_file() and p.stat().st_size > 0:
                found.append(p)
        except OSError:
            pass

    def want(p: Path) -> bool:
        return loose or _fresh(p, started_at)

    roots = [Path(instance_path)]
    for extra in extra_roots or []:
        if extra and str(Path(extra).resolve()) != str(Path(instance_path).resolve()):
            roots.append(Path(extra))
    for root in roots:
        crashes = root / "crash-reports"
        if crashes.is_dir():
            for p in crashes.rglob("*"):
                if p.suffix.lower() in (".txt", ".log") and want(p):
                    add(p)
        logs = root / "logs"
        for name in ("latest.log", "debug.log"):
            p = logs / name
            if want(p):
                add(p)
        for p in root.glob("hs_err_pid*.log"):
            if want(p):
                add(p)
        for p in root.glob("replay_pid*.log"):
            if want(p):
                add(p)
        for p in root.glob("*.log"):
            if want(p):
                add(p)
    seen, out = set(), []
    for p in found:
        key = str(p.resolve()) if p.exists() else str(p)
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def classify(path: Path) -> str:
    name = path.name.lower()
    if name.startswith("hs_err"):
        return "hs"
    if name.startswith("crash-"):
        return "crash"
    if name in (
        "latest.log", "latest log.txt", "debug.log", "debug log.txt",
        "rawoutput.log", "游戏崩溃前的输出.txt",
    ):
        return "mc"
    if name.endswith(".log"):
        return "extra_log"
    if name.endswith(".txt"):
        return "extra_txt"
    return "ignore"


# ---------------------------------------------------------------------------
# 分析
# ---------------------------------------------------------------------------

class _Analyzer:
    def __init__(self):
        self.log_mc = ""
        self.log_debug = ""
        self.log_hs = ""
        self.log_crash = ""
        self.log_all = ""
        self.reasons: dict[str, list[str]] = {}
        self.files: list[str] = []
        self.direct_file = ""
        self.raw_output = ""

    def append(self, code: str, extra=None):
        extras: list[str] = []
        if extra is None:
            extras = []
        elif isinstance(extra, str):
            if extra:
                extras = [extra]
        else:
            extras = [str(x) for x in extra if x]
        if code in self.reasons:
            merged = self.reasons[code] + extras
            self.reasons[code] = list(dict.fromkeys(merged))
        else:
            self.reasons[code] = list(dict.fromkeys(extras))

    def prepare(self, files: list[Path], output_lines: list[str] | None):
        buckets: dict[str, list[tuple[Path, list[str]]]] = {
            "hs": [], "crash": [], "mc": [], "extra_log": [], "extra_txt": [],
        }
        for path in files:
            kind = classify(path)
            if kind == "ignore":
                continue
            text = read_text(path)
            if not text.strip():
                continue
            lines = text.splitlines()
            buckets[kind].append((path, lines))
            if kind in ("hs", "crash") and not self.direct_file:
                self.direct_file = str(path)
            if kind == "mc":
                self.direct_file = str(path)

        if output_lines:
            cleaned = [ln.rstrip("\r") for ln in output_lines if ln is not None]
            self.raw_output = "\n".join(cleaned)
            if cleaned:
                buckets["mc"].insert(0, (Path("rawoutput.log"), cleaned))

        if not any(buckets[k] for k in ("hs", "crash", "mc")) and buckets["extra_log"]:
            buckets["mc"] = buckets["extra_log"]
            buckets["extra_log"] = []

        if buckets["hs"]:
            path, lines = max(buckets["hs"], key=lambda x: _mtime(x[0]))
            self.files.append(str(path))
            self.log_hs = head_tail_lines(lines, 200, 100)
            if not self.direct_file:
                self.direct_file = str(path)
        if buckets["crash"]:
            path, lines = max(buckets["crash"], key=lambda x: _mtime(x[0]))
            self.files.append(str(path))
            self.log_crash = head_tail_lines(lines, 300, 700)
            if not self.direct_file or classify(Path(self.direct_file)) == "mc":
                self.direct_file = str(path)
        if buckets["mc"]:
            names = {p.name.lower(): (p, ln) for p, ln in buckets["mc"]}
            for p, _ln in buckets["mc"]:
                self.files.append(str(p))
            for key in (
                "rawoutput.log", "游戏崩溃前的输出.txt",
                "latest.log", "latest log.txt", "debug.log", "debug log.txt",
            ):
                if key not in names:
                    continue
                path, lines = names[key]
                if key in ("rawoutput.log", "游戏崩溃前的输出.txt"):
                    self.log_mc += "\n".join(lines[-500:])
                else:
                    self.log_mc += "\n" + head_tail_lines(lines, 1500, 500)
                break
            else:
                path, lines = buckets["mc"][0]
                self.log_mc += "\n" + head_tail_lines(lines, 1500, 500)
            for key in ("debug.log", "debug log.txt"):
                if key in names:
                    self.log_debug = head_tail_lines(names[key][1], 1000, 0)
                    break
        for p, _ln in buckets["extra_log"] + buckets["extra_txt"]:
            self.files.append(str(p))

        self.log_mc = (self.log_mc or "").strip()
        self.log_debug = (self.log_debug or "").strip()
        self.log_hs = (self.log_hs or "").strip()
        self.log_crash = (self.log_crash or "").strip()
        self.log_all = self.log_mc + self.log_hs + self.log_crash
        return bool(self.log_mc or self.log_hs or self.log_crash)

    def analyze(self):
        if not (self.log_mc or self.log_hs or self.log_crash):
            self.append("no_files")
            return
        self.crit1()
        if self.reasons:
            return
        self.crit2()
        if self.reasons:
            return
        blob = self.log_all
        if any(tok in blob for tok in ("orge", "abric", "uilt", "iteloader")):
            keywords: list[str] = []
            if self.log_crash:
                keywords += analyze_stack_keyword(before_first(self.log_crash, "System Details"))
            if self.log_mc:
                fatals = re.findall(r"/FATAL] .+?(?=[\n]+\[)", self.log_mc, re.S)
                if "Unreported exception thrown!" in self.log_mc:
                    fatals.append(between(self.log_mc, "Unreported exception thrown!", "at oolloo.jlw.Wrapper"))
                for fatal in fatals:
                    keywords += analyze_stack_keyword(fatal)
            if self.log_hs:
                keywords += analyze_stack_keyword(between(self.log_hs, "T H R E A D", "Registers:"))
            keywords = list(dict.fromkeys(keywords))
            if keywords:
                names = analyze_mod_name(keywords, self.log_crash, self.log_debug)
                if names:
                    self.append("stack_mod", names)
                else:
                    self.append("stack_keyword", keywords)
                return
        self.crit3()

    def crit1(self):
        mc, hs, cr = self.log_mc, self.log_hs, self.log_crash
        if cr:
            if "Unable to make protected final java.lang.Class java.lang.ClassLoader.defineClass" in cr:
                self.append("java_too_new")
            if "Failed loading config file " in cr:
                mod = seek(cr, r"(?<=Failed loading config file .+ for modid )[^\n]+").strip()
                cfg = seek(cr, r"(?<=Failed loading config file ).+(?= of type)").strip()
                extras = [x for x in (try_mod_name(mod, cr, self.log_debug)[:1] + [cfg]) if x]
                self.append("mod_config", extras)
        if mc:
            if "Unrecognized option:" in mc:
                self.append("jvm_args")
            if "Found multiple arguments for option fml.forgeVersion, but you asked for only one" in mc:
                self.append("multi_forge_json")
            if "reads more than one module named" in mc:
                self.append(
                    "dup_module",
                    seek(mc, r"(?<=reads more than one module named )[\w.$]+"),
                )
            if "The driver does not appear to support OpenGL" in mc:
                self.append("no_opengl")
            if "java.lang.ClassCastException: java.base/jdk" in mc or "java.lang.ClassCastException: class jdk." in mc:
                self.append("jdk")
            of_forge = (
                "java.lang.NoSuchMethodError: 'void net.minecraft.client.renderer.texture.SpriteContents.",
                "java.lang.NoSuchMethodError: 'java.lang.String com.mojang.blaze3d.systems.RenderSystem.getBackendDescription",
                "java.lang.NoSuchMethodError: 'void net.minecraft.client.renderer.block.model.BakedQuad.",
                "java.lang.NoSuchMethodError: 'void net.minecraftforge.client.gui.overlay.ForgeGui.renderSelectedItemName",
                "java.lang.NoSuchMethodError: 'void net.minecraft.server.level.DistanceManager",
                "java.lang.NoSuchMethodError: 'net.minecraft.network.chat.FormattedText net.minecraft.client.gui.Font.ellipsize",
            )
            if any(s in mc for s in of_forge):
                self.append("optifine_forge")
            if "Open J9 is not supported" in mc or "OpenJ9 is incompatible" in mc or ".J9VMInternals." in mc:
                self.append("openj9")
            if "java.lang.NoSuchFieldException: ucp" in mc:
                self.append("java_too_new")
            if "because module java.base does not export" in mc:
                self.append("java_too_new")
            if "java.lang.ClassNotFoundException: jdk.nashorn.api.scripting.NashornScriptEngineFactory" in mc:
                self.append("java_too_new")
            if "java.lang.ClassNotFoundException: java.lang.invoke.LambdaMetafactory" in mc:
                self.append("java_too_new")
            if "The directories below appear to be extracted jar files. Fix this before you continue." in mc:
                self.append("mod_unzipped")
            if "Extracted mod jars found, loading will NOT continue" in mc:
                self.append("mod_unzipped")
            if "java.lang.ClassNotFoundException: org.spongepowered.asm.launch.MixinTweaker" in mc:
                self.append("mixin_bootstrap")
            if "Couldn't set pixel format" in mc:
                self.append("pixel_format")
            if "java.lang.OutOfMemoryError" in mc or "an out of memory error" in mc:
                self.append("oom")
            if "Shaders Mod detected. Please remove it, OptiFine has built-in support for shaders." in mc:
                self.append("shaders_optifine")
            if "java.lang.NoSuchMethodError: sun.security.util.ManifestEntryVerifier" in mc or \
                    "java.lang.NoSuchMethodError: 'void sun.security.util.ManifestEntryVerifier" in mc:
                self.append("old_forge_new_java")
            if "1282: Invalid operation" in mc:
                self.append("opengl_1282")
            if "signer information does not match signer information of other classes in the same package" in mc:
                self.append(
                    "verify_fail",
                    seek(mc, r'(?<=class ")[^"]+(?="\'s signer information)'),
                )
            if "Maybe try a lower resolution resourcepack?" in mc:
                self.append("hd_pack")
            if "ChunkManager$ProxyTicketManager.shouldForceTicks(J)Z" in mc and "OptiFine" in mc:
                self.append("optifine_world")
            if "com.electronwill.nightconfig.core.io.ParsingException: Not enough data available" in mc \
                    and "mod_config" not in self.reasons:
                self.append("nightconfig")
            if "Cannot find launch target fmlclient, unable to launch" in mc:
                self.append("forge_incomplete")
            if "Invalid paths argument, contained no existing paths" in mc and r"libraries\net\minecraftforge\fmlcore" in mc:
                self.append("forge_incomplete")
            if "Invalid module name: '' is not a Java identifier" in mc:
                self.append("mod_name_chars")
            if "has been compiled by a more recent version of the Java Runtime (class file version 55.0)" in mc:
                self.append("need_java11")
            if "sun.misc.Unsafe.defineAnonymousClass(Class,byte[],Object[])Class/invokeVirtual" in mc:
                self.append("need_java11")
            if "The requested compatibility level JAVA_11 could not be set" in mc:
                self.append("need_java11")
            if "Unsupported class file major version" in mc or "Unsupported major.minor version" in mc:
                self.append("java_mismatch")
            if "Level is not supported by the active JRE or ASM version" in mc:
                self.append("java_mismatch")
            if "Invalid maximum heap size" in mc:
                self.append("java32")
            if "Could not reserve enough space" in mc:
                if "for 1048576KB object heap" in mc:
                    self.append("java32")
                else:
                    self.append("oom")
            if "Caught exception from " in mc:
                name = seek(mc, r"(?<=Caught exception from )[^\n]+").strip()
                self.append("mod_certain", try_mod_name(name, cr, self.log_debug))
            if "DuplicateModsFoundException" in mc:
                self.append(
                    "mod_dup",
                    search_all(mc, r"(?<=\n\t[\w]+ : [A-Z]:[^\n]+[/\\])[^/\\\n]+?\.jar", re.I),
                )
            if "Found a duplicate mod" in mc:
                self.append("mod_dup", search_all(seek(mc, r"Found a duplicate mod[^\n]+"), r"[^\\/]+\.jar", re.I))
            if "Found duplicate mods" in mc:
                self.append("mod_dup", list(dict.fromkeys(search_all(mc, r"(?<=Mod ID: ')\w+?(?=' from mod files:)"))))
            if "ModResolutionException: Duplicate" in mc:
                self.append(
                    "mod_dup",
                    search_all(seek(mc, r"ModResolutionException: Duplicate[^\n]+"), r"[^\\/]+\.jar", re.I),
                )
            if "Incompatible mods found!" in mc:
                block = seek(mc, r"(?<=Incompatible mods found![\s\S]+: )[\s\S]+?(?=\tat )")
                block = before_first(block, "更多信息：")
                block = block.replace(
                    "Some of your mods are incompatible with the game or each other!", ""
                ).strip()
                self.append("mod_incompat", block)
            if "Missing or unsupported mandatory dependencies:" in mc:
                deps = search_all(
                    mc,
                    r"(?<=Missing or unsupported mandatory dependencies:)(?:[\n\r]+\t.*)+",
                    re.I,
                )
                cleaned = [re.sub(r"^\s+", "", d, flags=re.M).strip() for d in deps]
                self.append("mod_missing", cleaned)
        if hs:
            if "The system is out of physical RAM or swap space" in hs or "Out of Memory Error" in hs:
                self.append("oom")
            if "EXCEPTION_ACCESS_VIOLATION" in hs:
                if "# C [ig" in hs:
                    self.append("intel_av")
                if "# C [atio" in hs:
                    self.append("amd_av")
                if "# C [nvoglv" in hs:
                    self.append("nvidia_av")
        if cr:
            if "maximum id range exceeded" in cr:
                self.append("mod_id_limit")
            if "java.lang.OutOfMemoryError" in cr:
                self.append("oom")
            if "Pixel format not accelerated" in cr:
                self.append("pixel_format")
            if "Manually triggered debug crash" in cr:
                self.append("debug_crash")
            if "has mods that were not found" in cr and re.search(
                r"The Mod File [^\n]+optifine\\OptiFine[^\n]+ has mods that were not found", cr, re.I
            ):
                self.append("optifine_forge")
            if "-- MOD " in cr:
                chunk = between(cr, "-- MOD ", "Failure message:")
                if ".jar" in chunk.lower():
                    self.append("mod_certain", seek(chunk, r"(?<=Mod File: ).+").strip())
                else:
                    msg = seek(cr, r"(?<=Failure message: )[\w\W]+?(?=\tMod)").replace("\t", " ").strip()
                    self.append("loader_error", msg)
            if "Multiple entries with same key: " in cr:
                self.append(
                    "mod_certain",
                    try_mod_name(seek(cr, r"(?<=Multiple entries with same key: )[^=]+").strip(), cr, self.log_debug),
                )
            if "LoaderExceptionModCrash: Caught exception from " in cr:
                self.append(
                    "mod_certain",
                    try_mod_name(
                        seek(cr, r"(?<=LoaderExceptionModCrash: Caught exception from )[^\n]+").strip(),
                        cr, self.log_debug,
                    ),
                )

    def crit2(self):
        def mixin_analyze(text: str) -> bool:
            if not text:
                return False
            hit = (
                "Mixin prepare failed " in text or "Mixin apply failed " in text
                or "MixinApplyError" in text or "MixinTransformerError" in text
                or "mixin.injection.throwables." in text or ".json] FAILED during )" in text
            )
            if not hit:
                return False
            name = seek(text, r"(?<=from mod )[^./ ]+(?=] from)") or seek(text, r"(?<=for mod )[^./ ]+(?= failed)")
            if name:
                self.append("mixin", try_mod_name(name.strip(), self.log_crash, self.log_debug))
                return True
            for jn in search_all(text, r"(?<=^[^\t]+[ \[{(])[^ \[{(]+\.[^ ]+(?=\.json)", re.M):
                cleaned = jn.replace("mixins", "mixin").replace(".mixin", "").replace("mixin.", "")
                self.append("mixin", try_mod_name(cleaned, self.log_crash, self.log_debug))
                return True
            self.append("mixin")
            return True

        mc, cr = self.log_mc, self.log_crash
        is_mixin = False
        if mc:
            is_mixin = mixin_analyze(mc)
            if "An exception was thrown, the game will display an error screen and halt." in mc:
                msg = seek(mc, r"(?<=the game will display an error screen and halt.[\n\r]+[^\n]+?Exception: )[\s\S]+?(?=\n\tat)")
                self.append("forge_error", (msg or "").strip())
            for mark, pat in (
                ("A potential solution has been determined:\n",
                 r"(?<=A potential solution has been determined:\n)(?:[ \t]+-[ \t]+[^\n]+\n?)+"),
                ("A potential solution has been determined, this may resolve your problem:\n",
                 r"(?<=A potential solution has been determined, this may resolve your problem:\n)(?:[ \t]+-[ \t]+[^\n]+\n?)+"),
                ("确定了一种可能的解决方法，这样做可能会解决你的问题：\n",
                 r"(?<=确定了一种可能的解决方法，这样做可能会解决你的问题：\n)(?:[ \t]+-[ \t]+[^\n]+\n?)+"),
            ):
                if mark.strip() in mc:
                    block = seek(mc, pat)
                    tips = [ln.strip(" \t-") for ln in block.splitlines() if ln.strip()]
                    self.append("fabric_solution", "\n".join(tips) if tips else block.strip())
            if (not is_mixin) and "due to errors, provided by " in mc:
                self.append(
                    "mod_certain",
                    try_mod_name(seek(mc, r"(?<=due to errors, provided by ')[^']+").strip(), cr, self.log_debug),
                )
        if cr:
            mixin_analyze(cr)
            if "Suspected Mod" in cr:
                raw = between(cr, "Suspected Mod", "Stacktrace")
                if not raw.startswith("s: None"):
                    suspects = search_all(raw, r"(?<=\n\t[^(\t]+\()[^)\n]+")
                    if suspects:
                        self.append("mod_suspect", try_mod_name(suspects, cr, self.log_debug))

    def crit3(self):
        mc, cr = self.log_mc, self.log_crash
        if mc:
            if (not ("at net." in mc or "INFO]" in mc)) and not self.log_hs and not self.log_crash and len(mc) < 100:
                self.append("tiny_output", mc)
            if "Mod resolution failed" in mc:
                self.append("loader_error")
            if "Failed to create mod instance." in mc:
                mid = seek(mc, r"(?<=Failed to create mod instance. ModID: )[^,]+") \
                    or seek(mc, r"(?<=Failed to create mod instance. ModId )[^\n]+(?= for )")
                self.append("mod_init", try_mod_name(mid.strip(), cr, self.log_debug))
        if cr:
            if "\tBlock location: World: " in cr:
                block = seek(cr, r"(?<=\tBlock: Block\{)[^}]+")
                loc = seek(cr, r"(?<=\tBlock location: World: )\([^)]+\)")
                self.append("bad_block", f"{block} {loc}".strip())
            if "\tEntity's Exact location: " in cr:
                et = seek(cr, r"(?<=\tEntity Type: )[^\n]+(?= \()")
                loc = seek(cr, r"(?<=\tEntity's Exact location: )[^\n]+").strip()
                self.append("bad_entity", f"{et} ({loc})" if et else loc)


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def analyze_stack_keyword(stack: str) -> list[str]:
    text = "\n" + (stack or "") + "\n"
    hits = search_all(text, r"(?<=\n[^{]+)[a-zA-Z_]+\w+\.[a-zA-Z_]+[\w.]+(?=\.[\w.$]+\.)")
    hits += [s.replace("$", ".") for s in search_all(text, r"(?<=at [^(]+?\.\w+\$\w+\$)[\w$]+?(?=\$\w+\()")]
    hits = list(dict.fromkeys(hits))
    possible = []
    for stack_item in hits:
        if any(stack_item.startswith(p) for p in _STACK_IGNORE_PREFIX):
            continue
        possible.append(stack_item.strip())
    possible = list(dict.fromkeys(possible))
    words = []
    for stack_item in possible:
        parts = stack_item.split(".")
        for word in parts[:4]:
            if len(word) <= 2 or word.startswith("func_"):
                continue
            if word.lower() in _STACK_IGNORE_WORDS:
                continue
            words.append(word.strip())
    words = list(dict.fromkeys(words))
    if len(words) > 10:
        return []
    return words


def analyze_mod_name(keywords, log_crash: str, log_debug: str) -> list[str]:
    real = []
    for kw in keywords:
        for part in str(kw).split("("):
            p = part.strip(" )")
            if p:
                real.append(p)
    names: list[str] = []
    if log_crash and "A detailed walkthrough of the error" in log_crash:
        details = log_crash.replace("A detailed walkthrough of the error", "\x01")
        fabric = "Fabric Mods" in details
        if fabric:
            details = details.replace("Fabric Mods", "\x01")
        details = after_last(details, "\x01")
        mod_lines = []
        for line in details.splitlines():
            if line.lower().count(".jar") == 1:
                mod_lines.append(line)
            elif fabric and line.startswith("\t\t") and not re.search(r"\t\tfabric[\w-]*: Fabric", line):
                mod_lines.append(line)
        hints = []
        for kw in real:
            key = kw.lower().replace("_", "")
            for line in mod_lines:
                low = line.lower().replace("_", "")
                if key not in low:
                    continue
                if "minecraft.jar" in low or " forge-" in low or " mixin-" in low:
                    continue
                hints.append(line.strip())
                break
        hints = list(dict.fromkeys(hints))
        for line in hints:
            if fabric:
                name = seek(line, r"(?<=: )[^\n]+(?= [^\n]+)")
            else:
                name = seek(line, r"(?<=\()[^\t]+\.jar(?=\))|(?<=(\t\t)|(\| ))[^\t|]+\.jar", re.I)
            if name:
                names.append(name)
    if log_debug:
        rows = search_all(log_debug, r"(?<=valid mod file ).*", re.M)
        hints = []
        for kw in real:
            for row in rows:
                if "{" + kw + "}" in row:
                    hints.append(row)
        for line in dict.fromkeys(hints):
            name = seek(line, r".*(?= with)")
            if name:
                names.append(name)
    names = list(dict.fromkeys(n for n in names if n))
    return names


def try_mod_name(keyword, log_crash: str, log_debug: str) -> list[str]:
    if isinstance(keyword, (list, tuple)):
        keys = [str(k) for k in keyword if k]
        if not keys:
            return []
        return analyze_mod_name(keys, log_crash, log_debug) or keys
    key = (keyword or "").strip()
    if not key:
        return []
    return analyze_mod_name([key], log_crash, log_debug) or [key]


# ---------------------------------------------------------------------------
# 文案（对齐 PCL GetAnalyzeResult，启动器名改 PyMCL）
# ---------------------------------------------------------------------------

def _fmt_reason(code: str, extra: list[str], log_all: str, manual: bool) -> tuple[str, bool]:
    """返回 (正文, 是否追加求助页脚)。"""
    e = extra or []
    one = e[0] if e else ""
    many = "\n - ".join(e)
    help_me = True

    def sure_mod(lead: str) -> str:
        view = "" if manual else "\n你可以查看错误报告了解错误具体是如何发生的。"
        if len(e) <= 1:
            return (
                f"{lead}名为 {one or '未知'} 的 Mod 导致了游戏出错。"
                f"\n你可以尝试禁用此 Mod，然后观察游戏是否还会崩溃。{view}"
            )
        return (
            f"{lead}以下 Mod 导致了游戏出错：\n - {many}\n\n"
            f"你可以尝试依次禁用上述 Mod，然后观察游戏是否还会崩溃。{view}"
        )

    table = {
        "jvm_args": (
            "由于 Java 参数有误，导致游戏无法继续运行。\n"
            "请检查启动设置里的 Java 虚拟机参数和游戏参数是否有误。", False),
        "mod_unzipped": (
            "由于 Mod 文件被解压了，导致游戏无法继续运行。\n"
            "直接把整个 Mod 文件放进 mods 文件夹即可，解压就会出错。\n\n"
            "请删除 mods 里已被解压的文件夹，然后再启动游戏。", False),
        "oom": (
            "Minecraft 内存不足，导致其无法继续运行。\n"
            "这很可能是电脑内存不足、游戏分配的内存不足，或配置要求过高。\n\n"
            "请在启动设置中增加为游戏分配的内存，并删掉过高的材质、Mod、光影。\n"
            "如果还不行，启动前尽量关掉其它软件。", True),
        "openj9": (
            "游戏因为使用 OpenJ9 而崩溃了。\n"
            "请在本实例的 Java 选项中改用非 OpenJ9 的 Java，然后再启动。", False),
        "jdk": (
            "游戏似乎因为使用 JDK，或 Java 版本过高而崩溃了。\n"
            "请在本实例的 Java 选项中改用 Java 8，然后再启动。", False),
        "java_too_new": (
            "游戏似乎因为你使用的 Java 版本过高而崩溃了。\n"
            "请在本实例的 Java 选项中改用较低版本的 Java，然后再启动。", False),
        "java_mismatch": (
            "游戏不兼容你当前使用的 Java。\n"
            "请在本实例的 Java 选项中改用合适版本的 Java，然后再启动。", False),
        "mod_name_chars": (
            "由于有 Mod 的名称包含特殊字符，导致游戏崩溃。\n"
            "请把该 Mod 文件名改成只含英文、数字、减号、下划线和小数点，然后再启动。", False),
        "mixin_bootstrap": (
            "由于缺失 MixinBootstrap，导致游戏崩溃。\n请尝试安装 MixinBootstrap。", False),
        "need_java11": (
            "你安装的部分 Mod 需要使用 Java 11 启动。\n"
            "请在本实例的 Java 选项中改用 Java 11，然后再启动。", False),
        "tiny_output": (
            f"程序返回了以下信息：\n{one}", True),
        "optifine_world": (
            "你使用的 OptiFine 可能导致游戏出问题。\n该问题只在特定 OptiFine 版本出现，可以换一个 OptiFine 版本。", True),
        "hd_pack": (
            "材质分辨率过高，或显卡配置不足，导致游戏无法继续运行。\n"
            "如果正在用高清材质，先把它移除。否则请更新显卡驱动。", True),
        "nightconfig": (
            "由于 Night Config 存在问题，导致了游戏崩溃。\n"
            "可以尝试安装 Night Config Fixes 模组。", True),
        "opengl_1282": (
            "你使用的光影或材质导致游戏出现了问题。\n请尝试删除这些额外资源。", True),
        "mod_id_limit": (
            "安装的 Mod 过多，超出了游戏的 ID 限制。\n请尝试安装 JEID 等修复 Mod，或删除部分大型 Mod。", False),
        "verify_fail": (
            "部分文件或内容校验失败，导致游戏出问题。\n请尝试删除游戏（包括 Mod）并重新下载。", True),
        "forge_incomplete": (
            "由于安装的 Forge 文件丢失，游戏无法正常运行。\n请重新安装一次相同版本的 Forge。", True),
        "debug_crash": (
            "* 事实上，你的游戏没有任何问题，这是你自己触发的调试崩溃。", False),
        "optifine_forge": (
            "由于 OptiFine 与当前版本的 Forge 不兼容，导致游戏崩溃。\n"
            "请到 OptiFine 官网查看它所兼容的 Forge 版本，并按对应版本重装。", False),
        "shaders_optifine": (
            "无需同时安装 OptiFine 和 Shaders Mod，OptiFine 已经集成了光影功能。\n删掉 Shaders Mod 后再启动。", False),
        "old_forge_new_java": (
            "由于低版本 Forge 与当前 Java 不兼容，导致游戏崩溃。\n"
            "请尝试：把 Forge 更新到 36.2.26 或更高，或换用低于 1.8.0.320 的 Java。", False),
        "multi_forge_json": (
            "当前版本文件存在异常（版本 Json 里有多个 Forge）。\n请尝试重新全新安装 Forge。", False),
        "no_files": (
            "你的游戏出现了一些问题，但未能找到相关记录文件，因此无法继续分析。", True),
    }
    if code in table:
        return table[code]

    if code == "dup_module":
        mod = f"（{one}）" if one else ""
        return (
            f"同一个 Java 模块被加载了两次{mod}，游戏在模块解析阶段直接退出。\n"
            "通常是版本 JSON 被重复合并，或依赖库同时进入 --module-path 与 classpath "
            "却没有被 ignoreList 排除。\n\n请重新安装或修复该版本后再启动。", True)

    if code == "java32":
        import platform
        if platform.machine().endswith("64") or "64" in platform.architecture()[0]:
            return (
                "你似乎正在使用 32 位 Java，Minecraft 无法使用所需内存。\n"
                "请在本实例的 Java 选项中改用 64 位 Java，然后再启动。", False)
        return (
            "你正在使用 32 位操作系统，Minecraft 无法使用所需内存。\n"
            "需要重装 64 位系统，或换一台电脑。", True)

    if code == "mod_missing":
        if e:
            return (
                "由于未安装正确的前置 Mod，导致游戏退出。\n缺失的依赖项：\n - "
                + many + "\n\n请按上面的信息处理；看不懂英文可以用翻译。", False)
        return ("由于未安装正确的前置 Mod，导致游戏退出。\n请根据错误报告中的日志处理。", True)

    if code == "stack_keyword":
        if len(e) <= 1:
            return (
                f"游戏遇到了问题，分析到一个可疑关键词：{one or '未知'}。\n"
                "如果你知道它对应哪个 Mod，就先禁用它，也可以查看错误报告。", True)
        return (
            "游戏遇到了问题，分析到这些可疑关键词：\n - "
            + ", ".join(e) + "\n\n知道某个词对应哪个 Mod，就先禁用它。", True)

    if code in ("stack_mod", "mod_suspect"):
        view = "" if manual else "\n你可以查看错误报告了解错误具体是如何发生的。"
        if len(e) <= 1:
            return (
                f"怀疑名为 {one or '未知'} 的 Mod 导致了游戏出错，但不能完全确定。\n"
                f"可以先禁用它，看还会不会崩。{view}", True)
        return (
            "怀疑以下 Mod 导致了游戏出错，但不能完全确定：\n - "
            + many + f"\n\n可以依次禁用，看还会不会崩。{view}", True)

    if code == "mod_certain":
        return sure_mod(""), True

    if code == "mixin":
        view = "" if manual else "\n你可以查看错误报告了解错误具体是如何发生的。"
        if not e:
            return (
                "部分 Mod 注入失败，导致游戏出错。\n"
                "一般是该 Mod 和其它 Mod 或当前环境不兼容，或它自己有 Bug。\n"
                f"可以逐步禁用 Mod 来定位。{view}", True)
        if len(e) == 1:
            return (
                f"名为 {one} 的 Mod 注入失败，导致游戏出错。\n"
                f"一般是它和其它 Mod 或当前环境不兼容，或它自己有 Bug。\n可以先禁用它。{view}", True)
        return (
            "以下 Mod 导致了游戏出错：\n - " + many
            + f"\n一般是它们和其它 Mod 或当前环境不兼容。{view}", True)

    if code == "mod_config":
        if len(e) >= 2:
            return (
                f"名为 {e[0]} 的 Mod 导致了游戏出错：\n其配置文件 {e[1]} 存在异常，无法读取。", False)
        return sure_mod(""), True

    if code == "mod_init":
        view = "" if manual else "\n你可以查看错误报告了解错误具体是如何发生的。"
        if len(e) <= 1:
            return (
                f"名为 {one or '未知'} 的 Mod 初始化失败，游戏无法继续加载。\n可以先禁用它。{view}", True)
        return (
            "以下 Mod 初始化失败：\n - " + many + f"\n可以依次禁用。{view}", True)

    if code == "bad_block":
        if one:
            return (
                f"游戏似乎因为方块 {one} 出了问题。\n\n"
                "可以新建一个世界看还能不能玩：\n"
                " - 若正常，多半是这个方块；需要想办法删掉它。\n"
                " - 若仍然出错，问题可能来自别处。", True)
        return ("游戏似乎因为世界中的某些方块出了问题。\n可以新建世界试一次。", True)

    if code == "mod_dup":
        if len(e) >= 2:
            return (
                "你重复安装了多个相同的 Mod：\n - " + many
                + "\n\n每个 Mod 只能出现一次，删掉重复的再启动。", False)
        return ("你可能重复安装了多个相同的 Mod。\n每个 Mod 只能出现一次，请删掉重复的再启动。", True)

    if code == "bad_entity":
        if "minecraft:player" in one:
            if " " in one:
                return (f"游戏因为位于 {one.split(' ', 1)[-1]} 的玩家实体导致了崩溃。", True)
            return ("游戏因为玩家实体导致了崩溃。", True)
        if one:
            return (f"游戏因为实体 {one} 导致了崩溃。", True)
        return ("游戏因为世界中的某个实体导致了崩溃。", True)

    gpu = {"pixel_format", "intel_av", "amd_av", "nvidia_av", "no_opengl"}
    if code in gpu:
        if "hd graphics " in (log_all or "").lower():
            return (
                "显卡驱动有问题，或没有用独立显卡，游戏无法正常运行。\n\n"
                "如果电脑有独显，请用独显运行启动器和 Minecraft。\n"
                "仍不行就升级或回退出厂显卡驱动，或换用 8.0.51 及更低的 Java。", True)
        return (
            "显卡驱动存在问题，导致游戏无法正常运行。\n\n"
            "请升级显卡驱动到最新，或回退出厂版本后再启动。\n"
            "还不行可以试试 8.0.51 及更低的 Java。", True)

    if code == "fabric_solution":
        if one:
            return ("Fabric 提供了以下解决方案：\n" + "\n".join(e) + "\n\n请按上面的信息处理；看不懂英文可以用翻译。", False)
        return ("Fabric 可能已经给出解决方案，请根据错误报告中的日志处理。", True)
    if code == "forge_error":
        if one:
            return ("Forge 提供了以下错误信息：\n" + one + "\n\n请按上面的信息处理；看不懂英文可以用翻译。", False)
        return ("Forge 可能已经给出错误信息，请根据错误报告中的日志处理。", True)
    if code == "mod_incompat":
        if one:
            return ("你安装的 Mod 不兼容：\n" + one + "\n\n请按上面的信息处理；看不懂英文可以用翻译。", False)
        return ("你安装的 Mod 不兼容。请根据错误报告中的日志处理。", True)
    if code == "loader_error":
        if one:
            return ("Mod 加载器提供了以下错误信息：\n" + one + "\n\n请按上面的信息处理；看不懂英文可以用翻译。", False)
        return ("Mod 加载器可能已经给出错误信息，请根据错误报告中的日志处理。", True)

    return (f"分析到错误原因（{code}），但没有更详细的说明。请把错误报告发给能帮你的人。", True)


def format_result(analyzer: _Analyzer, *, manual: bool, exit_code=None) -> tuple[str, bool]:
    if not analyzer.reasons:
        if manual:
            body = "很抱歉，无法确定错误原因。"
        else:
            body = "很抱歉，你的游戏出现了一些问题……\n" + HELP_FOOTER
        hint = exit_hint(exit_code)
        if hint:
            body = hint + "\n\n" + body
        return body, True

    parts = []
    need_help = False
    for code, extra in analyzer.reasons.items():
        text, help_me = _fmt_reason(code, extra, analyzer.log_all, manual)
        parts.append(text)
        need_help = need_help or help_me
    body = "\n\n此外，".join(parts)
    hint = exit_hint(exit_code)
    if hint and normalize_exit(exit_code) not in (0, None):
        body = hint + "\n\n" + body
    if need_help and not manual:
        body = body.rstrip() + "\n" + HELP_FOOTER
    return body.strip(), need_help


def looks_like_crash(exit_code, analyzer: _Analyzer, cancelled: bool) -> bool:
    if cancelled:
        return False
    n = normalize_exit(exit_code)
    if n not in (0, None):
        return True
    blob = analyzer.log_all + "\n" + (analyzer.raw_output or "")
    if re.search(r"Game crashed!|---- Minecraft Crash Report ----", blob):
        return True
    if analyzer.log_hs:
        return True
    return False


def _title_of(reasons: dict) -> str:
    if not reasons:
        return "Minecraft 出现错误"
    first = next(iter(reasons))
    titles = {
        "oom": "内存不足",
        "java_mismatch": "Java 版本不兼容",
        "java_too_new": "Java 版本过高",
        "need_java11": "需要 Java 11",
        "mod_certain": "Mod 导致崩溃",
        "mixin": "Mod 注入失败",
        "fabric_solution": "Fabric 报错",
        "forge_error": "Forge 报错",
        "mod_dup": "Mod 重复安装",
        "dup_module": "Java 模块重复加载",
        "mod_missing": "缺少前置 Mod",
        "mod_incompat": "Mod 不兼容",
        "nvidia_av": "显卡驱动崩溃",
        "intel_av": "显卡驱动崩溃",
        "amd_av": "显卡驱动崩溃",
        "pixel_format": "显卡无法设置像素格式",
        "debug_crash": "手动调试崩溃",
    }
    return titles.get(first, "Minecraft 出现错误")


def _run(instance, *, output_lines=None, exit_code=None, started_at=None,
         cancelled=False, manual=False, version="", extra_roots=None) -> dict:
    path = _inst_path(instance)
    an = _Analyzer()
    files = collect_files(path, started_at, loose=manual, extra_roots=extra_roots)
    an.prepare(files, list(output_lines or []))
    an.analyze()
    crashed = looks_like_crash(exit_code, an, cancelled) if not manual else True
    detail, _need = format_result(an, manual=manual, exit_code=exit_code)
    summary = detail.split("\n", 1)[0][:220]
    reasons = [
        {"code": k, "title": _title_of({k: v}), "extra": v}
        for k, v in an.reasons.items()
    ]
    report = {
        "is_crash": bool(crashed),
        "title": "Minecraft 出现错误" if crashed else "游戏已退出",
        "headline": _title_of(an.reasons),
        "summary": summary,
        "detail": detail,
        "help": HELP_FOOTER,
        "reasons": reasons,
        "exit_code": normalize_exit(exit_code),
        "exit_hint": exit_hint(exit_code),
        "direct_file": an.direct_file,
        "files": list(dict.fromkeys(an.files)),
        "instance": _inst_name(instance),
        "version": version or "",
        "output_tail": (an.raw_output or "")[-8000:],
        "log_mc": (an.log_mc or "")[-4000:],
        "log_crash": (an.log_crash or "")[-4000:],
        "log_hs": (an.log_hs or "")[-2000:],
        "has_latest": bool(an.log_mc),
        "has_crash": bool(an.log_crash),
        "has_hs_err": bool(an.log_hs),
    }
    report["actions"] = build_actions(report, path)
    return report


def _match_mod_files(mods_dir: Path, names: list[str]) -> list[str]:
    """把分析里的模组名模糊映射到 mods 目录实际文件。"""
    if not mods_dir.is_dir() or not names:
        return []
    files = [p for p in mods_dir.iterdir()
             if p.is_file() and (p.suffix.lower() == ".jar"
                                 or p.name.lower().endswith(".jar.disabled"))]
    hit: list[str] = []
    lower_map = {p.name.lower(): p.name for p in files}
    stems = {(p.name[:-9] if p.name.lower().endswith(".disabled") else p.stem).lower(): p.name
             for p in files}
    for raw in names:
        key = (raw or "").strip()
        if not key:
            continue
        low = key.lower()
        if low in lower_map:
            hit.append(lower_map[low])
            continue
        stem = Path(key).stem.lower()
        if stem in stems:
            hit.append(stems[stem])
            continue
        for p in files:
            pn = p.name.lower()
            if low in pn or stem in pn or pn.startswith(stem):
                hit.append(p.name)
                break
    # 去重保序
    return list(dict.fromkeys(hit))


def build_actions(report: dict, instance_path: Path | str | None = None) -> list[dict]:
    """根据 reasons 生成可一键执行的修复动作（PCL 风格）。"""
    report = report or {}
    reasons = report.get("reasons") or []
    path = Path(instance_path) if instance_path else None
    if path is None and report.get("instance"):
        try:
            from mclauncher.instances import Instance
            path = Instance(str(report["instance"])).path
        except Exception:
            path = None
    mods_dir = (path / "mods") if path else None
    actions: list[dict] = []
    seen: set[str] = set()

    def add(action: dict):
        aid = action.get("id") or ""
        key = (
            aid + "|" + ",".join(action.get("mods") or [])
            + "|" + str(action.get("major") or "")
            + "|" + str(action.get("path") or "")
        )
        if key in seen:
            return
        seen.add(key)
        actions.append(action)

    mod_codes = (
        "mod_suspect", "stack_mod", "mod_name_chars", "shaders_optifine",
        "mod_certain", "mixin", "mod_config", "mod_init", "mod_incompat",
        "optifine_forge", "mixin_bootstrap", "nightconfig",
    )
    java_codes = (
        "openj9", "jdk", "java_too_new", "java_mismatch", "java32",
        "need_java11", "old_forge_new_java", "java_too_old",
    )
    repair_codes = (
        "forge_incomplete", "verify_fail", "multi_forge_json", "dup_module",
        "libs_missing", "assets_index_missing", "assets_missing",
        "natives_missing", "loader_error", "forge_error",
    )
    open_mods_codes = (
        "mod_unzipped", "mod_duplicate", "mod_breaks", "mod_dup",
        "mod_missing", "mod_id_limit", "hd_pack",
    )

    for row in reasons:
        code = (row.get("code") or "").strip()
        extra = list(row.get("extra") or [])
        if code in mod_codes:
            mods = _match_mod_files(mods_dir, extra) if mods_dir else []
            if not mods and extra:
                mods = [e for e in extra if e]
            if mods:
                add({
                    "id": "disable_mods",
                    "label": "禁用嫌疑 Mod",
                    "mods": mods,
                    "codes": [code],
                })
            elif mods_dir and mods_dir.is_dir():
                add({
                    "id": "open_mods_folder",
                    "label": "打开 Mods 文件夹排查",
                    "codes": [code],
                })
        elif code == "oom":
            from mclauncher.config import CONFIG
            cur = int(CONFIG.get("memory_mb") or 4096)
            add({
                "id": "bump_memory",
                "label": "提高默认内存",
                "memory_mb": min(16384, cur + 2048),
                "codes": [code],
            })
        elif code in java_codes:
            if code == "need_java11":
                major = 11
            elif code in ("jdk", "java_too_old", "old_forge_new_java"):
                major = 8
            elif code == "java_too_new":
                major = 17
            elif code == "java32":
                major = 17
            else:
                major = 17
            add({
                "id": "need_java",
                "label": f"下载 Java {major}",
                "major": major,
                "codes": [code],
            })
        elif code in repair_codes:
            if report.get("version"):
                add({
                    "id": "repair_version",
                    "label": "修复该版本文件",
                    "version": report.get("version") or "",
                    "instance": report.get("instance") or "",
                    "codes": [code],
                })
        elif code in open_mods_codes:
            add({
                "id": "open_mods_folder",
                "label": "打开 Mods 文件夹",
                "codes": [code],
            })
            if code == "mod_dup" and extra:
                mods = _match_mod_files(mods_dir, extra) if mods_dir else [e for e in extra if e]
                if mods:
                    add({
                        "id": "disable_mods",
                        "label": "禁用重复 Mod",
                        "mods": mods[1:] if len(mods) > 1 else mods,
                        "codes": [code],
                    })
        elif code in ("nvidia_av", "intel_av", "amd_av", "pixel_format",
                      "no_opengl", "opengl_1282"):
            add({
                "id": "open_gpu_hint",
                "label": "查看显卡驱动提示",
                "codes": [code],
            })
        elif code in ("jvm_args",):
            add({
                "id": "reset_jvm_args",
                "label": "清空自定义 JVM 参数",
                "codes": [code],
            })

    # 通用：有崩溃报告文件时允许打开
    direct = (report.get("direct_file") or "").strip()
    if direct and Path(direct).is_file():
        add({
            "id": "open_crash_file",
            "label": "打开崩溃报告",
            "path": direct,
            "codes": ["_file"],
        })
    # 有 hs_err 时提供清理建议动作
    if report.get("has_hs_err") and path:
        hs = list(path.glob("hs_err_pid*.log"))[:1]
        if hs:
            add({
                "id": "open_crash_file",
                "label": "打开 hs_err 日志",
                "path": str(hs[0]),
                "codes": ["_hs"],
            })

    return actions


def analyze_launch(instance, *, exit_code, output_lines=None, started_at=None,
                   cancelled=False, version="", extra_roots=None) -> dict:
    return _run(
        instance, output_lines=output_lines, exit_code=exit_code,
        started_at=started_at, cancelled=cancelled, manual=False, version=version,
        extra_roots=extra_roots,
    )


def analyze_instance(instance, extra_log: str = "", manual: bool = True) -> dict:
    lines = extra_log.splitlines() if extra_log else None
    return _run(instance, output_lines=lines, exit_code=None, manual=manual)


def export_report(report: dict, dest: str | Path | None = None) -> str:
    report = report or {}
    stamp = datetime.now().strftime("%Y-%m-%d_%H.%M.%S")
    dest_path = Path(dest) if dest else (utils.ROOT / f"错误报告-{stamp}.zip")
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest_path.with_suffix(".tmpdir")
    if tmp.exists():
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)
    analysis = (
        f"实例: {report.get('instance')}\n"
        f"版本: {report.get('version')}\n"
        f"退出码: {report.get('exit_code')} {report.get('exit_hint') or ''}\n\n"
        f"{report.get('detail') or ''}\n"
    )
    (tmp / "分析结论.txt").write_text(filter_secrets(analysis), encoding="utf-8")
    tail = report.get("output_tail") or ""
    if tail:
        (tmp / "游戏崩溃前的输出.txt").write_text(filter_secrets(tail), encoding="utf-8")
    copied = set()
    for raw in report.get("files") or []:
        src = Path(raw)
        if not src.is_file():
            continue
        name = src.name
        if name.lower() == "latest.log":
            name = "latest.log"
        target = tmp / name
        n = 1
        while str(target) in copied or target.exists():
            target = tmp / f"{src.stem}_{n}{src.suffix}"
            n += 1
        try:
            text = filter_secrets(read_text(src))
            target.write_text(text, encoding="utf-8")
            copied.add(str(target))
        except OSError:
            continue
    if dest_path.exists():
        dest_path.unlink()
    with zipfile.ZipFile(dest_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in tmp.rglob("*"):
            if p.is_file():
                zf.write(p, p.relative_to(tmp).as_posix())
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)
    return str(dest_path)


def open_path(path: str | Path) -> bool:
    p = Path(path)
    try:
        if p.is_file() or p.is_dir():
            if utils.IS_WINDOWS:
                import os
                os.startfile(str(p))  # type: ignore[attr-defined]
            else:
                import subprocess
                subprocess.Popen(["xdg-open" if not utils.IS_MAC else "open", str(p)])
            return True
    except OSError:
        return False
    return False


def collect_logs(instance) -> dict:
    """兼容旧 diagnose.collect_logs。"""
    path = _inst_path(instance)
    files = collect_files(path, loose=True)
    latest = crash = hs = ""
    latest_p = crash_p = hs_p = ""
    debug = ""
    for f in files:
        kind = classify(f)
        text = read_text(f, 120_000)
        if kind == "mc" and f.name.lower().startswith("latest") and not latest:
            latest, latest_p = text, str(f)
        elif kind == "crash" and not crash:
            crash, crash_p = text, str(f)
        elif kind == "hs" and not hs:
            hs, hs_p = text, str(f)
        elif f.name.lower() == "debug.log":
            debug = text
    if not latest:
        p = path / "logs" / "latest.log"
        if p.is_file():
            latest, latest_p = read_text(p, 120_000), str(p)
    return {
        "latest_path": latest_p,
        "crash_path": crash_p,
        "hs_path": hs_p,
        "latest": latest,
        "crash": crash,
        "hs_err": hs,
        "debug_tail": debug[-20_000:],
    }


def log_excerpt(instance, kind: str = "latest", max_chars: int = 6000) -> str:
    blob = collect_logs(instance)
    key = {"crash": "crash", "hs": "hs_err"}.get(kind, "latest")
    path_key = {"crash": "crash_path", "hs": "hs_path"}.get(kind, "latest_path")
    text = blob.get(key) or ""
    path = blob.get(path_key) or ""
    if not text:
        return f"没有 {kind} 日志"
    return f"文件: {path}\n\n{text[-max_chars:]}"


def main(argv=None) -> int:
    import argparse
    import json
    import sys
    p = argparse.ArgumentParser(prog="mclauncher.crash")
    p.add_argument("--instance", default="")
    p.add_argument("--version", default="")
    p.add_argument("--exit-code", type=int, default=0)
    p.add_argument("--output", default="", help="游戏输出末尾行文件")
    p.add_argument("--started-at", type=float, default=0)
    p.add_argument("--json-out", default="")
    p.add_argument("--from-json", default="")
    p.add_argument("--export", default="")
    p.add_argument("--open", default="")
    args = p.parse_args(argv)
    if args.open:
        return 0 if open_path(args.open) else 1
    report = {}
    if args.from_json:
        report = json.loads(Path(args.from_json).read_text(encoding="utf-8"))
    elif args.instance:
        from mclauncher.instances import Instance
        lines = []
        if args.output and Path(args.output).is_file():
            lines = Path(args.output).read_text(encoding="utf-8", errors="replace").splitlines()
        report = analyze_launch(
            Instance(args.instance),
            exit_code=args.exit_code,
            output_lines=lines,
            started_at=args.started_at or None,
            version=args.version,
        )
    if args.json_out and report:
        Path(args.json_out).write_text(
            json.dumps(report, ensure_ascii=False), encoding="utf-8")
    if args.export:
        if not report:
            return 2
        dest = export_report(report, args.export if args.export != "-" else None)
        sys.stdout.write(dest + "\n")
        return 0
    if report and not args.json_out:
        sys.stdout.write(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
