# -*- coding: utf-8 -*-
"""游戏 options.txt：首次启动自动设置游戏语言（对齐 PCL2 / HMCL）。

Minecraft 新装后默认英文，中文玩家每装一个版本都得进游戏手动改语言。
PCL2 / HMCL 会在版本首次启动（options.txt 还不存在）时替玩家写好 `lang:`。
这里做同样的事，且只做安全的事：

- options.txt 缺失 → 创建并写入 lang
- 文件存在但没有 lang 行 → 追加一行
- 已有 lang 行 → 一个字都不动（玩家自己改过的语言必须尊重）
- 1.11（16w32a）起语言代码全小写（zh_cn），更早版本是 zh_CN，大小写错了不生效
"""
from __future__ import annotations

from pathlib import Path

from . import manifest, utils
from .config import CONFIG

# 语言代码在 1.11 改成全小写
_LOWERCASE_SINCE = (1, 11, 0)

# 启动器语言 -> Minecraft 语言代码（现代小写形式）
LAUNCHER_TO_MC = {
    "zh_CN": "zh_cn",
    "en": "en_us",
}

# Minecraft 出厂默认语言：目标就是它时没必要写文件
MC_DEFAULT_LANG = "en_us"

# 设置项 game_lang 的取值：auto（跟随启动器）/ off（不写入）/ 具体代码如 zh_cn
GAME_LANG_CHOICES = ("auto", "off", "zh_cn", "en_us")


def base_mc_version(instance, version_id, depth: int = 6) -> str:
    """从版本 id / inheritsFrom 链解析出原版版本号，解析不出返回空串。

    加载器版本 id（如 1.20.1-forge-47.2.0）能直接从 id 前缀解析；
    自定义 id 靠 version.json 的 inheritsFrom / jar 往上找。
    """
    vid = str(version_id or "").strip()
    seen = set()
    while vid and vid not in seen and depth > 0:
        seen.add(vid)
        depth -= 1
        if manifest.mc_version_tuple(vid):
            return vid
        try:
            j = instance.version_json(vid) or {}
        except Exception:
            j = {}
        nxt = str(j.get("inheritsFrom") or j.get("jar") or "").strip()
        if not nxt or nxt == vid:
            return ""
        vid = nxt
    return vid if manifest.mc_version_tuple(vid) else ""


def lang_code_for_version(lang: str, mc_version: str | None) -> str:
    """按 MC 版本调整语言代码大小写：1.11 前是 zh_CN，之后是 zh_cn。

    解析不出版本（快照/未知）按现代版处理——快照全部在 1.11 之后。
    """
    lang = str(lang or "").strip()
    if not lang:
        return ""
    t = manifest.mc_version_tuple(mc_version) if mc_version else None
    if t and t < _LOWERCASE_SINCE:
        head, _, tail = lang.partition("_")
        return head.lower() + ("_" + tail.upper() if tail else "")
    return lang.lower()


def target_lang(setting=None, launcher_lang=None) -> str:
    """算出要写入的 MC 语言代码（现代小写形式），空串表示不写。

    - auto：跟随启动器界面语言；映射结果等于 MC 默认（en_us）时不写
    - off：永不写入
    - 其他：当作具体的 MC 语言代码
    """
    raw = setting if setting is not None else CONFIG.get("game_lang", "auto")
    s = str(raw or "auto").strip()
    if s.lower() in ("off", "none"):
        return ""
    if s.lower() in ("", "auto"):
        if launcher_lang is None:
            from . import i18n
            launcher_lang = i18n.current_language()
        code = LAUNCHER_TO_MC.get(str(launcher_lang), "")
        return "" if not code or code == MC_DEFAULT_LANG else code
    return s.lower()


def has_lang(path) -> bool:
    """options.txt 里是否已有 lang 行。文件不存在返回 False。"""
    p = Path(path)
    if not p.is_file():
        return False
    try:
        text = p.read_text("utf-8", errors="replace")
    except OSError:
        return True  # 读不了就别碰它
    return any(line.strip().lower().startswith("lang:") for line in text.splitlines())


def ensure_lang(game_dir, mc_version=None, setting=None, launcher_lang=None) -> str:
    """options.txt 没有 lang 时写入目标语言。

    返回实际写入的语言代码；未改动（关闭 / 已有 lang / 目标是默认语言 / IO 失败）
    返回空串。绝不覆盖已存在的 lang 行。
    """
    lang = target_lang(setting, launcher_lang)
    if not lang:
        return ""
    code = lang_code_for_version(lang, mc_version)
    gdir = Path(game_dir)
    path = gdir / "options.txt"
    try:
        if path.is_file():
            text = path.read_text("utf-8", errors="replace")
            if any(line.strip().lower().startswith("lang:") for line in text.splitlines()):
                return ""
            sep = "" if (not text or text.endswith("\n")) else "\n"
            with open(path, "a", encoding="utf-8") as f:
                f.write(f"{sep}lang:{code}\n")
        else:
            gdir.mkdir(parents=True, exist_ok=True)
            path.write_text(f"lang:{code}\n", encoding="utf-8")
    except OSError as e:
        utils.log.warning("写入游戏语言失败 %s: %s", path, e)
        return ""
    utils.log.info("首次启动，游戏语言已设为 %s（%s）", code, path)
    return code
