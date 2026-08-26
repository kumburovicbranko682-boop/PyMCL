# -*- coding: utf-8 -*-
"""游戏运行栈导出（HMCL 日志窗口「导出游戏运行栈」同款）。

游戏卡死（窗口未响应、日志不再滚动）时，对游戏进程做一次线程转储，
不打断游戏，转储文件能直接看出主线程卡在哪。HMCL 走 JVM Attach API；
Python 侧的等价做法是调 JDK 自带的诊断命令行工具：

1. 优先用启动这局游戏的 Java 同目录下的 jstack —— 同一个 JVM 发行版
   attach 成功率最高；新版 jstack 先带 -e（扩展信息，JDK 11+），不认
   该参数的旧版自动退回 `jstack -l`；
2. 该 Java 是 JRE（不带诊断工具）时，从启动器已知的全部 Java 里找带
   jstack / jcmd 的 JDK 顶上；
3. jstack 拿不到时用 `jcmd <pid> Thread.print -l` 兜底。

导出文件名与 HMCL 对齐：minecraft-exported-jstack-dump-<时间戳>.log。
"""
from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path

from . import utils
from .i18n import tr

#: 一次转储子进程的默认超时（秒）。attach 卡死的 JVM 可能较慢，放宽些。
DUMP_TIMEOUT = 30

#: 判定「这真的是一份线程转储」的特征串（jstack 与 jcmd Thread.print 都有）。
_DUMP_MARKERS = ("Full thread dump", "java.lang.Thread.State")

#: 诊断工具按优先级排列：jstack 输出更规整，jcmd 兜底。
_TOOLS = ("jstack", "jcmd")


class StackDumpError(RuntimeError):
    """找不到诊断工具，或所有 attach 尝试都失败。"""


def _tool_beside(java_exe, tool: str) -> str | None:
    """java 可执行文件同目录下的诊断工具路径；不存在返回 None。"""
    if not java_exe:
        return None
    parent = Path(str(java_exe)).parent
    for name in ((f"{tool}.exe", tool) if utils.IS_WINDOWS else (tool,)):
        cand = parent / name
        if cand.is_file():
            return str(cand)
    return None


def find_dump_tools(java_exe=None, javas=None) -> list[tuple[str, str]]:
    """按优先级列出可用的诊断工具。返回 [(工具路径, "jstack"|"jcmd")]。

    java_exe 是启动游戏用的 Java（优先它旁边的工具）；javas 不传时用
    launcher 已知的全部 Java（零子进程的 cached_all_javas，避免扫盘）。
    """
    if javas is None:
        from . import java as java_mod
        javas = java_mod.cached_all_javas()
    candidates = []
    if java_exe:
        candidates.append(str(java_exe))
    candidates += [str(j.get("exe") or "") for j in javas if j.get("exe")]
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for exe in candidates:
        for tool in _TOOLS:
            path = _tool_beside(exe, tool)
            if path and path not in seen:
                seen.add(path)
                out.append((path, tool))
    return out


def _attempts(path: str, kind: str, pid: int) -> list[list[str]]:
    if kind == "jstack":
        # -e（扩展信息）是 JDK 11+ 才有；旧 jstack 会报 Unknown option，
        # 所以准备一次不带 -e 的重试（HMCL GP-5426 / GP-5662 同款问题）。
        return [[path, "-e", "-l", str(pid)], [path, "-l", str(pid)]]
    return [[path, str(pid), "Thread.print", "-l"]]


def _looks_like_dump(text: str) -> bool:
    return any(marker in text for marker in _DUMP_MARKERS)


def dump_threads(pid, java_exe=None, javas=None, timeout: float = DUMP_TIMEOUT) -> str:
    """对 pid 指向的 JVM 做线程转储，返回转储文本。

    按 find_dump_tools 的顺序逐个尝试，全部失败抛 StackDumpError
    （信息里带上每次失败的原因，方便用户反馈）。
    """
    pid = int(pid or 0)
    if pid <= 0:
        raise StackDumpError(tr("游戏进程号无效"))
    tools = find_dump_tools(java_exe=java_exe, javas=javas)
    if not tools:
        raise StackDumpError(
            tr("没有找到 jstack / jcmd 诊断工具（JRE 不自带）。"
               "请安装一个 JDK，或在 Java 管理页添加一个 JDK 后重试。"))
    errors: list[str] = []
    for path, kind in tools:
        for cmd in _attempts(path, kind, pid):
            label = f"{Path(path).name} {' '.join(cmd[1:])}"
            try:
                proc = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=timeout,
                    encoding="utf-8", errors="replace")
            except (OSError, subprocess.TimeoutExpired) as e:
                errors.append(f"{label}: {e}")
                continue
            out = proc.stdout or ""
            if proc.returncode == 0 and _looks_like_dump(out):
                return out
            reason = (proc.stderr or out or "").strip()
            reason = reason.splitlines()[-1] if reason else f"exit {proc.returncode}"
            errors.append(f"{label}: {reason}")
    detail = "\n".join(errors[-4:])
    raise StackDumpError(tr("导出游戏运行栈失败：所有诊断工具都无法连接游戏进程。")
                         + ("\n" + detail if detail else ""))


def default_dump_path(base_dir=None) -> Path:
    """默认导出路径（文件名与 HMCL 一致，放启动器数据目录）。"""
    stamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    base = Path(base_dir) if base_dir else utils.ROOT
    return base / f"minecraft-exported-jstack-dump-{stamp}.log"


def export_dump(pid, java_exe=None, dest=None, javas=None,
                timeout: float = DUMP_TIMEOUT) -> str:
    """转储并写盘。返回导出文件的绝对路径。"""
    text = dump_threads(pid, java_exe=java_exe, javas=javas, timeout=timeout)
    path = Path(dest) if dest else default_dump_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return str(path.resolve())
