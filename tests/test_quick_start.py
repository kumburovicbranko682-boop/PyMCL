# -*- coding: utf-8 -*-
"""「开始游戏」新手流程（HMCL 同款）：未装任何版本时自动下载最新正式版并直接启动。

行为约定：
1. 最新正式版未安装 —— 先走既有安装通道，再走既有启动通道（同一个任务里串联）；
2. 最新正式版已安装 —— 跳过下载直接启动；
3. 拿不到最新版本号 —— 抛 LaunchError，别静默失败。
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


class QuickStartTests(unittest.TestCase):
    def setUp(self):
        from mclauncher import utils
        from mclauncher.config import CONFIG, DEFAULT_CONFIG

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

        from bridge.api import BackendAPI

        class _Bus:
            def emit(self, *a, **k):
                pass

        self.api = BackendAPI(_Bus())
        self.api._install_game_impl = Mock(return_value="已安装 1.99.0")
        self.api._launch_game_impl = Mock(return_value="游戏已退出")

    def _make_version(self, vid: str):
        inst = self.api._instance("default")
        vdir = inst.versions_dir() / vid
        vdir.mkdir(parents=True, exist_ok=True)
        (vdir / f"{vid}.json").write_text(json.dumps({"id": vid}), "utf-8")

    def _run(self):
        return self.api._quick_start_impl(
            lambda *a: None, lambda *a: None,
            "default", "离线模式", "Player", 4096, 854, 480)

    def test_downloads_latest_release_then_launches(self):
        with patch("mclauncher.manifest.get_version_manifest",
                   return_value={"latest": {"release": "1.99.0"}}):
            result = self._run()

        self.assertEqual(result, "游戏已退出")
        install_args = self.api._install_game_impl.call_args[0]
        self.assertEqual(install_args[2], "1.99.0")   # version
        self.assertEqual(install_args[5], "default")  # instance
        launch_args = self.api._launch_game_impl.call_args[0]
        self.assertEqual(launch_args[2], "default")   # instance
        self.assertEqual(launch_args[3], "1.99.0")    # version

    def test_skips_download_when_latest_installed(self):
        self._make_version("1.99.0")
        with patch("mclauncher.manifest.get_version_manifest",
                   return_value={"latest": {"release": "1.99.0"}}):
            self._run()

        self.api._install_game_impl.assert_not_called()
        launch_args = self.api._launch_game_impl.call_args[0]
        self.assertEqual(launch_args[3], "1.99.0")

    def test_hidden_installed_version_still_skips_download(self):
        """版本被「隐藏」不等于没安装：开始游戏不应重复下载它。"""
        from mclauncher import version_settings as vs
        self._make_version("1.99.0")
        vs.save(self.api._instance("default"), "1.99.0", {"hidden": True})
        with patch("mclauncher.manifest.get_version_manifest",
                   return_value={"latest": {"release": "1.99.0"}}):
            self._run()

        self.api._install_game_impl.assert_not_called()

    def test_raises_when_latest_unknown(self):
        from bridge.api import LaunchError
        with patch("mclauncher.manifest.get_version_manifest", return_value={}):
            with self.assertRaises(LaunchError):
                self._run()
        self.api._install_game_impl.assert_not_called()
        self.api._launch_game_impl.assert_not_called()

    def test_quick_start_game_registers_launch_task(self):
        """quick_start_game 必须登记为启动任务：停止按钮 / 退出清理都靠它。"""
        with patch.object(self.api, "start_task", return_value="task-quick") as st:
            tid = self.api.quick_start_game("default", "离线模式", "Player",
                                            4096, 854, 480)
        self.assertEqual(tid, "task-quick")
        self.assertEqual(self.api._launch_task_id, "task-quick")
        self.assertEqual(st.call_args[0][1], self.api._quick_start_impl)


if __name__ == "__main__":
    unittest.main()
