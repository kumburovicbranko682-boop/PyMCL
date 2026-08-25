# -*- coding: utf-8 -*-
"""get_mods_folder：mods 目录路径的公开门面（目录监视用）。"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import PropertyMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mclauncher.config import CONFIG  # noqa: E402


class GetModsFolderTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        patcher = patch.object(type(CONFIG), "instances_dir",
                               new_callable=PropertyMock,
                               return_value=self.root)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._td.cleanup)

        from bridge.api import BackendAPI

        class _Bus:
            def emit(self, *a, **k):
                pass

        self.api = BackendAPI(_Bus())

    def test_default_shared_mods_dir(self):
        from mclauncher import instances
        name = instances.list_instances()[0]
        folder = self.api.get_mods_folder(name)
        self.assertEqual(Path(folder), self.root / name / "mods")

    def test_qt_backend_has_same_method(self):
        from app.backend import BackendAPI as QtBackend
        self.assertTrue(callable(getattr(QtBackend, "get_mods_folder", None)))


if __name__ == "__main__":
    unittest.main()
