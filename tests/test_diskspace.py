# -*- coding: utf-8 -*-
"""磁盘空间检查：free_bytes / ensure_free 与安装入口守卫。"""

import shutil
import sys
import tempfile
import unittest
from collections import namedtuple
from pathlib import Path
from unittest.mock import PropertyMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mclauncher import diskspace  # noqa: E402
from mclauncher.config import CONFIG  # noqa: E402

_Usage = namedtuple("usage", "total used free")


class FreeBytesTests(unittest.TestCase):
    def test_existing_dir(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertGreater(diskspace.free_bytes(td), 0)

    def test_missing_path_walks_up_to_parent(self):
        with tempfile.TemporaryDirectory() as td:
            missing = Path(td) / "not" / "created" / "yet"
            self.assertGreater(diskspace.free_bytes(missing), 0)

    def test_os_error_returns_minus_one(self):
        with patch.object(diskspace.shutil, "disk_usage", side_effect=OSError("x")):
            self.assertEqual(diskspace.free_bytes("/"), -1)


class EnsureFreeTests(unittest.TestCase):
    def test_plenty_of_space_passes(self):
        with patch.object(diskspace.shutil, "disk_usage",
                          return_value=_Usage(10 ** 12, 0, 10 ** 11)):
            self.assertEqual(diskspace.ensure_free("/tmp"), 10 ** 11)

    def test_low_space_raises_readable_error(self):
        with patch.object(diskspace.shutil, "disk_usage",
                          return_value=_Usage(10 ** 12, 0, 50 * 1024 * 1024)):
            with self.assertRaises(diskspace.DiskSpaceError) as ctx:
                diskspace.ensure_free("/tmp", what="安装版本 1.20.1")
        msg = str(ctx.exception)
        self.assertIn("磁盘空间不足", msg)
        self.assertIn("安装版本 1.20.1", msg)
        self.assertIn("50.0 MB", msg)

    def test_unknown_free_does_not_block(self):
        with patch.object(diskspace.shutil, "disk_usage", side_effect=OSError("x")):
            self.assertEqual(diskspace.ensure_free("/tmp"), -1)


class InstallGuardTests(unittest.TestCase):
    """满盘时安装入口在动网络/解压之前就报人话错误。"""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        patcher = patch.object(type(CONFIG), "instances_dir",
                               new_callable=PropertyMock,
                               return_value=self.root)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._td.cleanup)
        from mclauncher.instances import Instance
        self.inst = Instance("测试")
        self.inst.create()

    def _patch_full_disk(self):
        return patch.object(diskspace.shutil, "disk_usage",
                            return_value=_Usage(10 ** 12, 0, 10 * 1024 * 1024))

    def test_install_version_guard(self):
        from mclauncher.installer import Installer
        with self._patch_full_disk():
            with self.assertRaises(diskspace.DiskSpaceError):
                Installer(self.inst).install_version("1.20.1")

    def test_install_mrpack_guard(self):
        from mclauncher import modpack
        with self._patch_full_disk():
            with self.assertRaises(diskspace.DiskSpaceError):
                modpack.install_mrpack(None, "/nonexistent/pack.mrpack", self.inst)

    def test_install_cf_zip_guard(self):
        from mclauncher import modpack
        with self._patch_full_disk():
            with self.assertRaises(diskspace.DiskSpaceError):
                modpack.install_cf_zip(None, "/nonexistent/pack.zip", self.inst)


if __name__ == "__main__":
    unittest.main()
