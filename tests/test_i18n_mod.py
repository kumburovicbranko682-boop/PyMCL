# -*- coding: utf-8 -*-
"""一键模组汉化（对齐 PCL2）：I18nUpdateMod 安装入口。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mclauncher import mods


class FakeInst:
    def __init__(self, path, ids=()):
        self.path = Path(path)
        self.name = "测试实例"
        self._ids = list(ids)

    def installed_ids(self):
        return self._ids


class TestDetectInstalled(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.inst = FakeInst(self.tmp.name)
        self.mods_dir = Path(self.tmp.name) / "mods"
        self.mods_dir.mkdir()

    def test_empty(self):
        self.assertEqual(mods.i18n_mod_installed(self.inst), "")

    def test_missing_dir(self):
        inst = FakeInst(Path(self.tmp.name) / "nope")
        self.assertEqual(mods.i18n_mod_installed(inst), "")

    def test_standard_filename(self):
        (self.mods_dir / "I18nUpdateMod-3.5.5-all.jar").write_bytes(b"PK")
        self.assertEqual(mods.i18n_mod_installed(self.inst),
                         "I18nUpdateMod-3.5.5-all.jar")

    def test_disabled_counts(self):
        (self.mods_dir / "i18n-update-mod-2.0.jar.disabled").write_bytes(b"PK")
        self.assertTrue(mods.i18n_mod_installed(self.inst))

    def test_unrelated_jar_ignored(self):
        (self.mods_dir / "jei-15.0.jar").write_bytes(b"PK")
        (self.mods_dir / "i18n-notes.txt").write_text("x")
        self.assertEqual(mods.i18n_mod_installed(self.inst), "")

    def test_explicit_mods_dir(self):
        iso = Path(self.tmp.name) / "versions" / "v" / "mods"
        iso.mkdir(parents=True)
        (iso / "I18nUpdateMod.jar").write_bytes(b"PK")
        self.assertEqual(mods.i18n_mod_installed(self.inst), "")
        self.assertEqual(mods.i18n_mod_installed(self.inst, iso), "I18nUpdateMod.jar")


class TestFacade(unittest.TestCase):
    """bridge.api 与 backend 对齐：install_i18n_mod 的前置检查与委托。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        from bridge.api import BackendAPI
        self.api = BackendAPI.__new__(BackendAPI)
        self.inst = FakeInst(self.tmp.name, ids=["1.20.1-fabric"])
        self.api._instance = lambda name: self.inst
        self.api.install_mod = mock.Mock(return_value="task-1")

    def test_requires_loader(self):
        self.inst._ids = ["1.20.1"]
        with self.assertRaises(ValueError):
            self.api.install_i18n_mod("t")
        self.api.install_mod.assert_not_called()

    def test_rejects_when_installed(self):
        mods_dir = Path(self.tmp.name) / "mods"
        mods_dir.mkdir()
        (mods_dir / "I18nUpdateMod-3.5.jar").write_bytes(b"PK")
        with self.assertRaises(ValueError):
            self.api.install_i18n_mod("t")

    def test_delegates_to_install_mod(self):
        out = self.api.install_i18n_mod("t")
        self.assertEqual(out, "task-1")
        args, _ = self.api.install_mod.call_args
        self.assertEqual(args[0], mods.I18N_MOD_SLUG)
        self.assertEqual(args[1], self.inst.name)
        self.assertEqual(args[2]["slug"], mods.I18N_MOD_SLUG)

    def test_version_isolated_target(self):
        iso = Path(self.tmp.name) / "iso-mods"
        iso.mkdir()
        (iso / "I18nUpdateMod.jar").write_bytes(b"PK")
        self.api._mods_folder = mock.Mock(return_value=iso)
        with self.assertRaises(ValueError):
            self.api.install_i18n_mod("t", version="1.20.1-fabric")
        self.api._mods_folder.assert_called_once_with(self.inst, "1.20.1-fabric")


if __name__ == "__main__":
    unittest.main()
