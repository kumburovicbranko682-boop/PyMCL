# -*- coding: utf-8 -*-
"""删除走系统回收站（对标 PCL2 / HMCL：删错了还能从回收站捞回来）。

PyMCL 此前所有删除（实例 / 版本 / 世界存档 / 模组 / 资源文件）都是
shutil.rmtree / unlink 永久删除，误删存档 = 游戏进度直接没了。
PCL2 与 HMCL 的删除都会进系统回收站。

- Windows: SHFileOperationW + FOF_ALLOWUNDO（系统回收站）
- macOS:   移入 ~/.Trash，重名自动加序号
- Linux:   XDG Trash 规范（Trash/files + Trash/info/*.trashinfo）

回收站不可用（无桌面环境、权限、跨盘失败等）时 move_to_trash 返回
False，trash_or_delete 会退回永久删除——删除操作本身永远成功。
"""

from __future__ import annotations

import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from . import utils


def move_to_trash(path) -> bool:
    """尽量把文件/目录移入系统回收站。成功返回 True。"""
    p = Path(path)
    if not p.exists():
        return False
    try:
        if utils.IS_WINDOWS:
            return _windows_trash(p)
        if sys.platform == "darwin":
            return _macos_trash(p)
        return _xdg_trash(p)
    except Exception as e:
        utils.log.warning("移入回收站失败 %s: %s", p, e)
        return False


def trash_or_delete(path) -> str:
    """优先回收站，不可用退回永久删除。返回 "trash" 或 "deleted"。"""
    p = Path(path)
    if move_to_trash(p):
        utils.log.info("已移入回收站: %s", p)
        return "trash"
    utils.remove_tree(p)
    return "deleted"


def _unique_dest(folder: Path, name: str, info_dir: Path | None = None) -> Path:
    """回收站里的落点：重名自动加 .2、.3 …（info 目录同名 .trashinfo 也算占用）。"""
    dest = folder / name
    n = 2
    while dest.exists() or (
            info_dir is not None and (info_dir / f"{dest.name}.trashinfo").exists()):
        dest = folder / f"{name}.{n}"
        n += 1
    return dest


def _windows_trash(p: Path) -> bool:
    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [
            ("hwnd", wintypes.HWND),
            ("wFunc", ctypes.c_uint),
            ("pFrom", wintypes.LPCWSTR),
            ("pTo", wintypes.LPCWSTR),
            ("fFlags", ctypes.c_ushort),
            ("fAnyOperationsAborted", wintypes.BOOL),
            ("hNameMappings", ctypes.c_void_p),
            ("lpszProgressTitle", wintypes.LPCWSTR),
        ]

    FO_DELETE = 3
    FOF_SILENT = 0x0004
    FOF_NOCONFIRMATION = 0x0010
    FOF_ALLOWUNDO = 0x0040
    FOF_NOERRORUI = 0x0400

    op = SHFILEOPSTRUCTW()
    op.hwnd = None
    op.wFunc = FO_DELETE
    # pFrom 需要双 \0 结尾；LPCWSTR 转换自动补一个，这里再补一个
    op.pFrom = str(p.resolve()) + "\0"
    op.pTo = None
    op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI
    code = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    return code == 0 and not op.fAnyOperationsAborted and not p.exists()


def _macos_trash(p: Path) -> bool:
    trash = Path.home() / ".Trash"
    if not trash.is_dir():
        return False
    dest = _unique_dest(trash, p.name)
    shutil.move(str(p), str(dest))
    return True


def _xdg_trash(p: Path) -> bool:
    """XDG Trash 规范的家目录回收站；GNOME/KDE 文件管理器都能看到并还原。"""
    data_home = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))
    root = data_home / "Trash"
    files_dir = root / "files"
    info_dir = root / "info"
    files_dir.mkdir(parents=True, exist_ok=True)
    info_dir.mkdir(parents=True, exist_ok=True)

    src = p.resolve()
    dest = _unique_dest(files_dir, p.name, info_dir=info_dir)
    info_file = info_dir / f"{dest.name}.trashinfo"
    stamp = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    info_file.write_text(
        "[Trash Info]\n"
        f"Path={quote(str(src), safe='/')}\n"
        f"DeletionDate={stamp}\n",
        encoding="utf-8")
    try:
        shutil.move(str(p), str(dest))
    except Exception:
        try:
            info_file.unlink()
        except OSError:
            pass
        raise
    return True
