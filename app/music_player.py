# -*- coding: utf-8 -*-
"""启动器背景音乐播放器（PCL2 音乐播放器同款）。

曲库与播单顺序在 mclauncher.music；这里只做 Qt 播放：
随机整轮播放 music/ 文件夹，一轮放完重新洗牌接着放。
QtMultimedia 懒加载：不开开关就完全不碰（有些精简系统装不上解码器）。
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QUrl, Signal

from mclauncher import music, utils
from mclauncher.config import CONFIG


class MusicPlayer(QObject):
    track_changed = Signal(str)  # 当前曲目文件名；空串 = 已停止

    def __init__(self, parent=None):
        super().__init__(parent)
        self._player = None
        self._audio = None
        self._queue: list[str] = []
        self._current = ""
        self._bad_streak = 0  # 连续解码失败数，防整库全坏时无限跳曲

    # ------------------------------------------------------------- 懒初始化
    def _ensure(self) -> bool:
        if self._player is not None:
            return True
        try:
            from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
        except ImportError as exc:
            utils.log.warning("QtMultimedia 不可用，背景音乐关闭: %s", exc)
            return False
        self._audio = QAudioOutput(self)
        self._player = QMediaPlayer(self)
        self._player.setAudioOutput(self._audio)
        self._player.mediaStatusChanged.connect(self._on_status)
        self._player.errorOccurred.connect(
            lambda _err, msg: utils.log.warning("背景音乐播放出错: %s", msg))
        self.set_volume(int(CONFIG.get("music_volume", 50) or 0))
        return True

    # ------------------------------------------------------------- 播放控制
    def start(self) -> bool:
        """开始（或重新开始）随机整轮播放。曲库为空返回 False。"""
        if not self._ensure():
            return False
        if not self._queue:
            self._queue = music.playlist()
        if not self._queue:
            self._set_current("")
            return False
        self._play_path(self._queue.pop(0))
        return True

    def stop(self):
        if self._player is not None:
            self._player.stop()
        self._queue = []
        self._set_current("")

    def next_track(self) -> str:
        """切下一曲；一轮放完重新洗牌。返回新曲目文件名（空 = 没歌）。"""
        if not self._ensure():
            return ""
        if not self._queue:
            self._queue = music.playlist()
        if not self._queue:
            self._set_current("")
            return ""
        self._play_path(self._queue.pop(0))
        return self._current

    def set_volume(self, percent: int):
        if self._audio is not None:
            self._audio.setVolume(max(0, min(100, int(percent))) / 100.0)

    def is_playing(self) -> bool:
        return bool(self._current)

    def current_track(self) -> str:
        return self._current

    # ------------------------------------------------------------- 内部
    def _play_path(self, path: str):
        self._player.setSource(QUrl.fromLocalFile(path))
        self._player.play()
        self._set_current(Path(path).name)

    def _set_current(self, name: str):
        if name != self._current:
            self._current = name
            self.track_changed.emit(name)

    def _on_status(self, status):
        from PySide6.QtMultimedia import QMediaPlayer
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self._bad_streak = 0
            self.next_track()
        elif status == QMediaPlayer.MediaStatus.InvalidMedia:
            # 坏文件直接跳过；整库都放不动就停，别无限跳曲
            utils.log.warning("背景音乐无法解码，跳过: %s", self._current)
            self._bad_streak += 1
            if self._bad_streak >= max(1, len(music.list_tracks())):
                utils.log.warning("音乐文件夹里没有可播放的文件，背景音乐停止")
                self.stop()
                return
            self.next_track()
        elif status == QMediaPlayer.MediaStatus.BufferedMedia:
            self._bad_streak = 0
