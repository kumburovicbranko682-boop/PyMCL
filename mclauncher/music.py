# -*- coding: utf-8 -*-
"""启动器背景音乐（PCL2 音乐播放器同款）。

把音频文件放进启动器目录下的 music/ 文件夹，启动器随机循环播放。
本模块只管曲库与播单顺序；真正的播放在 app 侧（QMediaPlayer），
无 Qt 前端通过门面拿曲库自己放。
"""
from __future__ import annotations

import random
import shutil
from pathlib import Path

from . import utils

FOLDER = "music"
# QMediaPlayer 的 ffmpeg 后端都放得动这些
EXTS = (".mp3", ".ogg", ".wav", ".flac", ".m4a", ".aac", ".opus")


class MusicError(Exception):
    """音乐库操作失败。"""


def folder() -> Path:
    return utils.ensure_dir(utils.ROOT / FOLDER)


def list_tracks() -> list[dict]:
    """曲库列表（按文件名排序）：[{name, path, size}]。"""
    rows = []
    fold = folder()
    for p in sorted(fold.iterdir(), key=lambda x: x.name.lower()):
        if p.is_file() and p.suffix.lower() in EXTS:
            rows.append({"name": p.name, "path": str(p), "size": p.stat().st_size})
    return rows


def playlist(seed=None) -> list[str]:
    """随机顺序的整轮播单（文件绝对路径）。PCL2 同款：每轮洗一次牌。"""
    paths = [r["path"] for r in list_tracks()]
    rng = random.Random(seed)
    rng.shuffle(paths)
    return paths


def _safe_child(name: str) -> Path:
    fold = folder()
    p = (fold / name).resolve()
    if not str(p).startswith(str(fold.resolve())):
        raise MusicError(f"非法文件名: {name}")
    return p


def import_tracks(paths) -> list[str]:
    """把外部音频文件复制进曲库。重名自动加序号，返回新增文件名。"""
    added = []
    for raw in paths or []:
        src = Path(raw)
        if not src.is_file():
            raise MusicError(f"文件不存在: {src}")
        if src.suffix.lower() not in EXTS:
            raise MusicError(f"不支持的音频格式: {src.name}（支持 {' '.join(EXTS)}）")
        dest = _safe_child(src.name)
        base, suffix = dest.stem, dest.suffix
        n = 1
        while dest.exists():
            n += 1
            dest = dest.with_name(f"{base} ({n}){suffix}")
        shutil.copy2(src, dest)
        added.append(dest.name)
    return added


def delete_track(name: str) -> str:
    """删除一首（尽量进回收站）。返回被删的文件名。"""
    p = _safe_child(name)
    if not p.is_file():
        raise MusicError(f"曲目不存在: {name}")
    from . import trash
    trash.trash_or_delete(p)
    return name
