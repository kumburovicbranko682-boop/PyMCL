# -*- coding: utf-8 -*-
"""Fabric API / QSL 随装（HMCL 安装页可选组件同款）。

覆盖：game_install 在 fabric/quilt 下按 extra["fabric_api"] 装前置、
forge/原版跳过、前置失败不炸游戏安装；向导勾选框联动与 payload。
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from mclauncher import game_install


def _installer(vid="fabric-loader-0.16.9-1.20.1"):
    ins = MagicMock()
    ins.install_fabric.return_value = vid
    ins.install_quilt.return_value = "quilt-loader-0.27.0-1.20.1"
    ins.install_forge.return_value = "1.20.1-forge-47.4.10"
    ins.instance.versions_dir.return_value.__truediv__ = lambda s, x: MagicMock(
        exists=lambda: False)
    return ins


class TestGameInstallFabricApi(unittest.TestCase):
    def test_fabric_installs_fabric_api(self):
        ins = _installer()
        with patch("mclauncher.mods.install_modrinth_mod",
                   return_value={"slug": "fabric-api", "version": "0.92.0",
                                 "files": ["fabric-api-0.92.0.jar"]}) as mocked:
            vid = game_install.install_game(
                ins, "1.20.1", "Fabric", extra={"fabric_api": True})
        self.assertEqual(vid, "fabric-loader-0.16.9-1.20.1")
        mocked.assert_called_once()
        args, kwargs = mocked.call_args
        self.assertEqual(args[0], ins.dm)
        self.assertEqual(args[1], "fabric-api")
        self.assertEqual(kwargs.get("mc_version"), "1.20.1")
        self.assertEqual(kwargs.get("loader"), "fabric")

    def test_quilt_installs_qsl(self):
        ins = _installer()
        with patch("mclauncher.mods.install_modrinth_mod",
                   return_value={"slug": "qsl", "version": "9.0.0",
                                 "files": ["qsl-9.0.0.jar"]}) as mocked:
            game_install.install_game(
                ins, "1.20.1", "Quilt", extra={"fabric_api": True})
        self.assertEqual(mocked.call_args[0][1], "qsl")
        self.assertEqual(mocked.call_args[1].get("loader"), "quilt")

    def test_forge_skips_fabric_api(self):
        ins = _installer()
        with patch("mclauncher.mods.install_modrinth_mod") as mocked:
            game_install.install_game(
                ins, "1.20.1", "Forge", extra={"fabric_api": True})
        mocked.assert_not_called()

    def test_flag_off_skips(self):
        ins = _installer()
        with patch("mclauncher.mods.install_modrinth_mod") as mocked:
            game_install.install_game(
                ins, "1.20.1", "Fabric", extra={"fabric_api": False})
        mocked.assert_not_called()

    def test_fabric_api_failure_keeps_install(self):
        """前置下载失败只留提示，游戏安装照常返回版本号。"""
        ins = _installer()
        with patch("mclauncher.mods.install_modrinth_mod",
                   side_effect=OSError("cdn down")):
            vid = game_install.install_game(
                ins, "1.20.1", "Fabric", extra={"fabric_api": True})
        self.assertEqual(vid, "fabric-loader-0.16.9-1.20.1")
        notes = " ".join(str(c.args[0]) for c in ins._note.call_args_list)
        self.assertIn("安装失败", notes)


class TestWizardCheckbox(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def _dialog(self):
        from PySide6.QtWidgets import QWidget
        from app.pages.install_wizard import InstallWizardDialog

        class NoAsyncBackend:
            pass  # 没有 call_async：向导跳过网络加载

        self._host = QWidget()
        return InstallWizardDialog(NoAsyncBackend(), "1.20.1", "default",
                                   parent=self._host)

    def test_fabric_enables_checkbox_and_payload(self):
        dlg = self._dialog()
        dlg.primary.setCurrentText("Fabric")
        self.assertTrue(dlg.fabric_api.isEnabled())
        self.assertTrue(dlg.payload()["extra"]["fabric_api"])
        # 取消勾选 → payload False
        dlg.fabric_api.setChecked(False)
        self.assertFalse(dlg.payload()["extra"]["fabric_api"])

    def test_quilt_relabels(self):
        dlg = self._dialog()
        dlg.primary.setCurrentText("Quilt")
        self.assertTrue(dlg.fabric_api.isEnabled())
        self.assertIn("QSL", dlg.fabric_api.text())

    def test_forge_disables_and_payload_false(self):
        dlg = self._dialog()
        dlg.primary.setCurrentText("Forge")
        self.assertFalse(dlg.fabric_api.isEnabled())
        self.assertFalse(dlg.payload()["extra"]["fabric_api"])

    def test_vanilla_disables(self):
        dlg = self._dialog()
        dlg.primary.setCurrentIndex(0)
        self.assertFalse(dlg.fabric_api.isEnabled())
        self.assertFalse(dlg.payload()["extra"]["fabric_api"])


if __name__ == "__main__":
    unittest.main()
