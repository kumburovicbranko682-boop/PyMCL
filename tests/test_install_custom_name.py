# -*- coding: utf-8 -*-
"""安装新游戏时自定义版本名（HMCL/PCL2 安装页同款）。"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import PropertyMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mclauncher import utils  # noqa: E402
from mclauncher.config import CONFIG  # noqa: E402
from mclauncher.game_install import install_game  # noqa: E402
from mclauncher.installer import InstallError  # noqa: E402


class _FakeInstaller:
    """只造版本目录，不真下载。"""

    def __init__(self, instance):
        self.instance = instance
        self.skip_assets = False
        self.notes = []
        self.installed = []

    def _note(self, msg, done=0, total=1):
        self.notes.append(str(msg))

    def _make(self, vid: str) -> str:
        vdir = self.instance.versions_dir() / vid
        vdir.mkdir(parents=True)
        utils.write_json(vdir / f"{vid}.json", {"id": vid, "mainClass": "x"})
        self.installed.append(vid)
        return vid

    def install_version(self, mc, force=False, java=None):
        return self._make(mc)

    def install_fabric(self, mc, loader_version=None, force=False):
        return self._make(f"fabric-loader-0.16.0-{mc}")


class CustomNameTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        self.addCleanup(self._td.cleanup)
        patcher = patch.object(type(CONFIG), "instances_dir",
                               new_callable=PropertyMock,
                               return_value=self.root)
        patcher.start()
        self.addCleanup(patcher.stop)
        from mclauncher.instances import Instance
        (self.root / "inst").mkdir(parents=True)
        self.inst = Instance("inst")
        self.installer = _FakeInstaller(self.inst)

    def test_default_name_unchanged(self):
        vid = install_game(self.installer, "1.20.1")
        self.assertEqual(vid, "1.20.1")
        self.assertTrue((self.inst.versions_dir() / "1.20.1" / "1.20.1.json").is_file())

    def test_custom_name_renames_and_rewrites_id(self):
        vid = install_game(self.installer, "1.20.1",
                           extra={"custom_name": "我的生存"})
        self.assertEqual(vid, "我的生存")
        vdir = self.inst.versions_dir() / "我的生存"
        self.assertTrue((vdir / "我的生存.json").is_file())
        self.assertEqual(utils.read_json(vdir / "我的生存.json", {}).get("id"), "我的生存")
        # 旧目录不残留
        self.assertFalse((self.inst.versions_dir() / "1.20.1").exists())

    def test_custom_name_with_loader(self):
        vid = install_game(self.installer, "1.20.1", loader="fabric",
                           extra={"custom_name": "F 整合"})
        self.assertEqual(vid, "F 整合")
        self.assertFalse(
            (self.inst.versions_dir() / "fabric-loader-0.16.0-1.20.1").exists())

    def test_same_as_auto_name_is_noop(self):
        vid = install_game(self.installer, "1.20.1",
                           extra={"custom_name": "1.20.1"})
        self.assertEqual(vid, "1.20.1")

    def test_collision_fails_before_download(self):
        (self.inst.versions_dir() / "taken").mkdir(parents=True)
        with self.assertRaises(InstallError):
            install_game(self.installer, "1.20.1", extra={"custom_name": "taken"})
        # 预检失败时什么都没装（没浪费下载）
        self.assertEqual(self.installer.installed, [])

    def test_illegal_chars_sanitized(self):
        vid = install_game(self.installer, "1.20.1",
                           extra={"custom_name": "a/b:c"})
        self.assertEqual(vid, "a-b-c")

    def test_blank_name_rejected_by_sanitize(self):
        with self.assertRaises(InstallError):
            install_game(self.installer, "1.20.1", extra={"custom_name": "  . "})


class WizardPayloadTests(unittest.TestCase):
    """安装向导把版本名塞进 extra.custom_name。"""

    @classmethod
    def setUpClass(cls):
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_payload_carries_custom_name(self):
        class _B:
            def call_async(self, fn, done=None, fail=None):
                pass
        from PySide6.QtWidgets import QWidget
        from app.pages.install_wizard import InstallWizardDialog
        host = QWidget()
        host.resize(800, 600)
        host.show()
        dlg = InstallWizardDialog(_B(), "1.20.1", "default", host)
        self.assertEqual(dlg.payload()["extra"].get("custom_name"), None)
        dlg.name_edit.setText("  我的世界  ")
        self.assertEqual(dlg.payload()["extra"]["custom_name"], "我的世界")


if __name__ == "__main__":
    unittest.main()
