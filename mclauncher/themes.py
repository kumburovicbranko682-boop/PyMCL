# -*- coding: utf-8 -*-
"""主题包体系：保存/加载/管理完整主题配置。

一个主题包包含：颜色、深色模式、背景图路径。
"""
from __future__ import annotations

import os
import json
from pathlib import Path
from typing import Optional

from . import utils
from .config import CONFIG

THEMES_DIR = "themes"
THEME_EXT = ".json"


def _themes_dir() -> Path:
    p = utils.ROOT / THEMES_DIR
    p.mkdir(parents=True, exist_ok=True)
    return p


def _theme_path(name: str) -> Path:
    return _themes_dir() / f"{_sanitize(name)}{THEME_EXT}"


def _sanitize(name: str) -> str:
    safe = "".join(c if c.isalnum() or c in " _-" else "_" for c in name).strip()
    return safe or "untitled"


def _current_theme() -> dict:
    """读取当前配置的主题。"""
    return {
        "name": "当前主题",
        "theme_color": CONFIG.get("theme_color", "#2E9B6B"),
        "ui_dark": bool(CONFIG.get("ui_dark", False)),
        "ui_background": CONFIG.get("ui_background", ""),
        "ui_font_family": CONFIG.get("ui_font_family", ""),
        "window_mode": CONFIG.get("window_mode", "window"),
        "custom_homepage": CONFIG.get("custom_homepage", ""),
        "homepage_mode": CONFIG.get("homepage_mode", "news"),
    }


def list_themes() -> list[dict]:
    """列出所有已保存的主题包。"""
    d = _themes_dir()
    if not d.is_dir():
        return []
    result = []
    for f in sorted(d.iterdir()):
        if f.suffix.lower() == THEME_EXT:
            data = utils.read_json(f, None)
            if isinstance(data, dict):
                result.append({
                    "name": data.get("name", f.stem),
                    "file": f.name,
                    "theme_color": data.get("theme_color", "#2E9B6B"),
                    "ui_dark": bool(data.get("ui_dark", False)),
                })
    return result


def save_theme(name: str) -> dict:
    """保存当前配置为主题包。"""
    theme = _current_theme()
    theme["name"] = name
    path = _theme_path(name)
    utils.write_json(path, theme)
    return theme


def load_theme(name: str) -> dict:
    """加载主题包，应用到全局配置，返回主题数据。"""
    path = _theme_path(name)
    if not path.is_file():
        raise FileNotFoundError(f"主题包不存在: {name}")
    theme = utils.read_json(path, {})
    if not isinstance(theme, dict):
        raise ValueError(f"主题包数据损坏: {name}")
    updates = {}
    for key in ("theme_color", "ui_dark", "ui_background", "ui_font_family",
                 "window_mode", "custom_homepage", "homepage_mode"):
        if key in theme:
            updates[key] = theme[key]
    if updates:
        CONFIG.update(updates)
        CONFIG.save()
    return theme


def delete_theme(name: str):
    """删除主题包。"""
    path = _theme_path(name)
    if path.is_file():
        path.unlink()


def export_theme(name: str, dest: str) -> str:
    """导出主题包到外部文件。"""
    src = _theme_path(name)
    if not src.is_file():
        raise FileNotFoundError(f"主题包不存在: {name}")
    dst = Path(dest)
    import shutil
    shutil.copy2(src, dst)
    return str(dst)


def import_theme(path: str) -> str:
    """从外部文件导入主题包。"""
    src = Path(path)
    if not src.is_file():
        raise FileNotFoundError(f"文件不存在: {path}")
    data = utils.read_json(src, None)
    if not isinstance(data, dict):
        raise ValueError("文件不是有效的主题包")
    name = str(data.get("name", src.stem))
    dst = _theme_path(name)
    import shutil
    shutil.copy2(src, dst)
    return name