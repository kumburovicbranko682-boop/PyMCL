# -*- coding: utf-8 -*-
"""游戏日志分级解析（HMCL 日志窗口同款）。

Minecraft 日志典型格式 `[12:34:56] [Render thread/INFO]: ...`；堆栈行
（\tat / Caused by / ... N more）没有自带级别，跟随上一行。"""
from __future__ import annotations

import re
from pathlib import Path

from . import utils

LEVELS = ("fatal", "error", "warn", "info", "debug", "trace")

_BRACKET_RE = re.compile(r"\[[^\]]*/(FATAL|ERROR|WARN(?:ING)?|INFO|DEBUG|TRACE)\]", re.I)
# log4j XML / 简单前缀形式：`ERROR] ...`、`13:52:01 ERROR ...`
_PLAIN_RE = re.compile(r"(?:^|\s)(FATAL|ERROR|WARN(?:ING)?|INFO|DEBUG|TRACE)[\]:\s]")
_EXC_RE = re.compile(r"\b[A-Za-z_$][\w$.]*(?:Exception|Error)\b(?::|$)")


def _norm(word: str) -> str:
    w = word.lower()
    return "warn" if w == "warning" else w


def is_stack_line(line: str) -> bool:
    s = str(line or "")
    stripped = s.strip()
    return (s.startswith("\tat ") or s.startswith("    at ")
            or stripped.startswith("at ") and "(" in stripped
            or stripped.startswith("Caused by:")
            or stripped.startswith("Suppressed:")
            or bool(re.match(r"^\.\.\. \d+ more", stripped)))


def parse_level(line, prev: str = "info") -> str:
    """单行日志的级别。堆栈延续行返回 prev。"""
    s = str(line or "")
    if not s.strip():
        return prev if prev in LEVELS else "info"
    if is_stack_line(s):
        return prev if prev in LEVELS else "error"
    m = _BRACKET_RE.search(s)
    if m:
        return _norm(m.group(1))
    if "[STDERR]" in s or s.startswith("Exception in thread"):
        return "error"
    m = _PLAIN_RE.search(s)
    if m:
        return _norm(m.group(1))
    if _EXC_RE.search(s):
        return "error"
    return "info"


def annotate(lines, prev: str = "info") -> list[tuple[str, str]]:
    """给一批行标级别，堆栈行继承前一行级别。返回 [(level, line)]。"""
    out = []
    for line in lines or []:
        level = parse_level(line, prev)
        out.append((level, str(line)))
        prev = level
    return out


def count_levels(rows) -> dict:
    counts = {k: 0 for k in LEVELS}
    for level, _line in rows or []:
        counts[level if level in LEVELS else "info"] += 1
    return counts


def export_lines(lines, dest) -> str:
    """把日志行写成 UTF-8 文本文件，返回路径。"""
    p = Path(dest)
    utils.ensure_dir(p.parent)
    text = "\n".join(str(x) for x in (lines or []))
    p.write_text(text + ("\n" if text else ""), encoding="utf-8")
    return str(p)
