# -*- coding: utf-8 -*-
"""游戏 options.txt：首次启动把游戏语言对齐启动器语言（对齐 PCL2 / HMCL）。

只在 options.txt 还没有 lang 条目时写入；玩家在游戏里改过语言就绝不碰。
"""
from __future__ import annotations

import re
from pathlib import Path

from . import utils


def _mc_minor(version_id: str):
    m = re.search(r"\b1\.(\d+)", str(version_id or ""))
    return int(m.group(1)) if m else None


def mc_lang(launcher_lang: str, version_id: str = "") -> str:
    """启动器语言对应的游戏语言代码；游戏默认就是英文时返回空串。"""
    lang = (launcher_lang or "").replace("-", "_")
    if not lang.lower().startswith("zh"):
        return ""
    low = lang.lower()
    if low.startswith(("zh_tw", "zh_hk", "zh_mo")):
        suffix = low[:5].split("_", 1)[1]  # tw / hk / mo
    else:
        suffix = "cn"
    minor = _mc_minor(version_id)
    # 1.10 及以前语言代码带大写地区（zh_CN），1.11+ 全小写（zh_cn）
    if minor is not None and minor <= 10:
        return f"zh_{suffix.upper()}"
    return f"zh_{suffix}"


def ensure_language(game_dir, version_id: str = "", launcher_lang: str | None = None) -> bool:
    """options.txt 缺 lang 时写入启动器语言。返回是否写入。"""
    if launcher_lang is None:
        from . import i18n
        launcher_lang = i18n.current_language()
    code = mc_lang(launcher_lang, version_id)
    if not code:
        return False
    path = Path(game_dir) / "options.txt"
    try:
        if path.is_file():
            text = path.read_text("utf-8", errors="replace")
            for line in text.splitlines():
                if line.split(":", 1)[0].strip() == "lang":
                    return False  # 玩家已有语言选择，不覆盖
            sep = "" if (not text or text.endswith("\n")) else "\n"
            path.write_text(f"{text}{sep}lang:{code}\n", "utf-8")
            return True
        utils.ensure_dir(path.parent)
        path.write_text(f"lang:{code}\n", "utf-8")
        return True
    except OSError:
        return False
