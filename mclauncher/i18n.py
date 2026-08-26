# -*- coding: utf-8 -*-
"""多语言框架（i18n）。

用法：
    from mclauncher.i18n import _
    label = _("启动游戏")       # 翻译当前语言
    label = _("启动游戏", "en")  # 取英文

语言切换：i18n.set_language("en") 后所有后续 _() 用新语言。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Optional

from . import utils
from .config import CONFIG

# 语言列表
LANGUAGES = {
    "zh_CN": "简体中文",
    "en": "English",
}

_DEFAULT_LANG = "zh_CN"
_current_lang: str = _DEFAULT_LANG
_strings: dict[str, dict[str, str]] = {}
_loaded = False


def _locales_dir() -> Path:
    """Bundled JSON catalogs: next to this module, or PyInstaller _MEIPASS."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        bundled = Path(meipass) / "mclauncher" / "locales"
        if bundled.is_dir():
            return bundled
    return Path(__file__).resolve().parent / "locales"


def _load_strings():
    global _strings, _loaded
    _strings = {}
    # 加载内置翻译
    base = _locales_dir()
    if base.is_dir():
        for f in sorted(base.glob("*.json")):
            lang = f.stem
            try:
                data = json.loads(f.read_text("utf-8"))
                if isinstance(data, dict):
                    _strings[lang] = data
            except (json.JSONDecodeError, OSError):
                pass
    # 确保有默认语言
    if _DEFAULT_LANG not in _strings:
        _strings[_DEFAULT_LANG] = {}
    if "en" not in _strings:
        _strings["en"] = {}
    _loaded = True


def _ensure():
    if not _loaded:
        _load_strings()


def available_languages() -> dict[str, str]:
    """返回 {lang_code: display_name}。"""
    _ensure()
    langs = dict(LANGUAGES)
    for code in _strings:
        langs.setdefault(code, code)
    return langs


def current_language() -> str:
    global _current_lang
    return _current_lang


def set_language(lang: str):
    global _current_lang
    _ensure()
    lang = (lang or _DEFAULT_LANG).strip().replace("-", "_")
    if lang not in _strings:
        lang = _DEFAULT_LANG
    _current_lang = lang
    CONFIG.set("language", lang)
    CONFIG.save()


def init_language():
    """从配置加载语言设置。"""
    lang = CONFIG.get("language", "")
    set_language(lang)


def _lookup(key: str, lang: str) -> Optional[str]:
    """在指定语言中查找。

    key 本身就是中文原文，所以源语言（zh_CN）和英文都**不能**回退到对方：
    否则 zh_CN 里漏掉一条，中文界面上就会冒出一句英文。
    只有第三方语言（如 ja）才回退英文，再回退 key。
    """
    _ensure()
    data = _strings.get(lang, {})
    if key in data:
        return data[key]
    if lang not in (_DEFAULT_LANG, "en"):
        return _strings.get("en", {}).get(key)
    return None


def _(key: str, lang: Optional[str] = None, default: Optional[str] = None) -> str:
    """翻译 key。如果找不到翻译，返回 key 本身（或 default）。"""
    if lang is None:
        lang = current_language()
    val = _lookup(key, lang)
    if val is not None:
        return val
    return default if default is not None else key


# UI 代码统一用 tr()：`_` 在 app/ 里有 40 处被当丢弃变量用
# （path, _ = ... / lambda _: ... / for _ in ...），会把 i18n 函数遮蔽掉。
tr = _


def add_translations(lang: str, mapping: dict):
    """运行时添加翻译。"""
    _ensure()
    if lang not in _strings:
        _strings[lang] = {}
    _strings[lang].update(mapping)


def save_translations(lang: str):
    """将运行时的翻译持久化到 locales 文件。"""
    _ensure()
    if lang not in _strings:
        return
    if getattr(sys, "frozen", False):
        base = utils.ROOT / "locales"
    else:
        base = _locales_dir()
    base.mkdir(parents=True, exist_ok=True)
    path = base / f"{lang}.json"
    utils.write_json(path, _strings[lang])