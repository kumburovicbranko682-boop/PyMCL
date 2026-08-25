# -*- coding: utf-8 -*-
"""启动器背景音乐（PCL2 音乐播放器同款）。

覆盖：曲库列表/导入/删除/随机播单、配置默认值、两个门面的设置键与曲库方法、
Qt 播放器的空曲库与基本控制（离屏、不依赖音频设备）。
"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from mclauncher import music, utils
from mclauncher.config import CONFIG, DEFAULT_CONFIG


class _Isolated(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        self.addCleanup(self._td.cleanup)
        data = {k: (list(v) if isinstance(v, list) else dict(v) if isinstance(v, dict) else v)
                for k, v in DEFAULT_CONFIG.items()}
        for p in (patch.object(utils, "ROOT", self.root),
                  patch.object(CONFIG, "data", data),
                  patch.object(CONFIG, "save", lambda: None)):
            p.start()
            self.addCleanup(p.stop)

    def _track(self, name: str, content: bytes = b"x") -> Path:
        p = music.folder() / name
        p.write_bytes(content)
        return p


class ConfigDefaultTests(unittest.TestCase):
    def test_defaults(self):
        self.assertIs(DEFAULT_CONFIG.get("music_enabled"), False)
        self.assertEqual(DEFAULT_CONFIG.get("music_volume"), 50)


class LibraryTests(_Isolated):
    def test_list_filters_and_sorts(self):
        self._track("b.mp3")
        self._track("A.ogg")
        self._track("notes.txt")  # 非音频不进曲库
        (music.folder() / "sub").mkdir()
        rows = music.list_tracks()
        self.assertEqual([r["name"] for r in rows], ["A.ogg", "b.mp3"])
        self.assertTrue(all(r["size"] >= 1 for r in rows))

    def test_playlist_is_shuffled_permutation(self):
        names = [f"t{i}.mp3" for i in range(8)]
        for n in names:
            self._track(n)
        pl1 = music.playlist(seed=42)
        pl2 = music.playlist(seed=42)
        self.assertEqual(pl1, pl2)  # 同种子可复现
        self.assertEqual(sorted(Path(p).name for p in pl1), sorted(names))
        # 不同种子几乎必然给出不同顺序（8! 种排列）
        self.assertNotEqual(music.playlist(seed=1), music.playlist(seed=2))

    def test_import_copies_and_dedupes(self):
        src_dir = self.root / "src"
        src_dir.mkdir()
        song = src_dir / "song.mp3"
        song.write_bytes(b"aaa")
        self.assertEqual(music.import_tracks([str(song)]), ["song.mp3"])
        self.assertEqual(music.import_tracks([str(song)]), ["song (2).mp3"])
        self.assertEqual(len(music.list_tracks()), 2)

    def test_import_rejects_bad_ext_and_missing(self):
        bad = self.root / "cover.png"
        bad.write_bytes(b"png")
        with self.assertRaises(music.MusicError):
            music.import_tracks([str(bad)])
        with self.assertRaises(music.MusicError):
            music.import_tracks([str(self.root / "ghost.mp3")])

    def test_delete_and_traversal_guard(self):
        self._track("gone.mp3")
        self.assertEqual(music.delete_track("gone.mp3"), "gone.mp3")
        self.assertEqual(music.list_tracks(), [])
        with self.assertRaises(music.MusicError):
            music.delete_track("gone.mp3")
        with self.assertRaises(music.MusicError):
            music.delete_track("../../etc/passwd")


class BridgeFacadeTests(_Isolated):
    def setUp(self):
        super().setUp()
        from bridge.api import BackendAPI

        class _Bus:
            def emit(self, *a, **k):
                pass

        self.api = BackendAPI(_Bus())

    def test_list_music_tracks(self):
        self._track("bgm.flac")
        rows = self.api.list_music_tracks()
        self.assertEqual([r["name"] for r in rows], ["bgm.flac"])

    def test_settings_keys_roundtrip_with_clamp(self):
        settings = self.api.get_settings()
        self.assertIs(settings.get("music_enabled"), False)
        self.assertEqual(settings.get("music_volume"), 50)
        self.api.save_settings({"music_enabled": True, "music_volume": 250})
        self.assertIs(CONFIG.get("music_enabled"), True)
        self.assertEqual(CONFIG.get("music_volume"), 100)  # 上限 100
        self.api.save_settings({"music_volume": -3})
        self.assertEqual(CONFIG.get("music_volume"), 0)


class QtFacadeTests(_Isolated):
    def setUp(self):
        super().setUp()
        for target in ("mclauncher.source.invalidate_probe",
                       "mclauncher.source.warmup_async",
                       "mclauncher.net.apply_proxy_policy"):
            p = patch(target, lambda *a, **k: None)
            p.start()
            self.addCleanup(p.stop)

    def test_settings_keys(self):
        from app.backend import BackendAPI as QtBackend
        settings = QtBackend.get_settings(None)
        self.assertIs(settings.get("music_enabled"), False)
        self.assertEqual(settings.get("music_volume"), 50)
        QtBackend.save_settings(None, {"music_enabled": True, "music_volume": 130})
        self.assertIs(CONFIG.get("music_enabled"), True)
        self.assertEqual(CONFIG.get("music_volume"), 100)


class PlayerTests(_Isolated):
    """离屏播放器：不校验声音输出，只验证队列/状态机不炸。"""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls._app = QApplication.instance() or QApplication([])

    def test_empty_library_start_returns_false(self):
        from app.music_player import MusicPlayer
        player = MusicPlayer()
        self.assertFalse(player.start())
        self.assertFalse(player.is_playing())

    def test_start_next_stop_cycle(self):
        import struct
        import wave
        wav = music.folder() / "loop.wav"
        with wave.open(str(wav), "w") as f:
            f.setnchannels(1)
            f.setsampwidth(2)
            f.setframerate(8000)
            f.writeframes(struct.pack("<800h", *([0] * 800)))

        from app.music_player import MusicPlayer
        player = MusicPlayer()
        seen = []
        player.track_changed.connect(seen.append)
        self.assertTrue(player.start())
        self.assertEqual(player.current_track(), "loop.wav")
        player.set_volume(30)
        self.assertEqual(player.next_track(), "loop.wav")  # 单曲整轮循环回自己
        player.stop()
        self.assertFalse(player.is_playing())
        self.assertIn("loop.wav", seen)
        self.assertEqual(seen[-1], "")


if __name__ == "__main__":
    unittest.main()
