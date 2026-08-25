# -*- coding: utf-8 -*-
"""导出模组清单（HMCL parity）：Markdown 表格 / 纯文本，禁用标记与转义。"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mclauncher import mod_info, utils  # noqa: E402
from tests.test_mod_info import _fabric_jar  # noqa: E402


class ExportModListTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.base = Path(self._td.name)
        self.addCleanup(self._td.cleanup)
        self.mods = self.base / "inst" / "mods"
        self.mods.mkdir(parents=True)
        # 缓存/导出都锁进沙箱
        patcher = patch.object(utils, "ROOT", self.base / "root")
        patcher.start()
        self.addCleanup(patcher.stop)

    def _fill(self):
        _fabric_jar(self.mods / "demo.jar")
        _fabric_jar(self.mods / "other.jar.disabled",
                    id="other", name="Other|Mod", version="0.9")

    def test_markdown_export(self):
        self._fill()
        dest = self.base / "out" / "list.md"
        path = mod_info.export_mod_list(self.mods, dest, title="我的实例")
        text = Path(path).read_text("utf-8")
        self.assertIn("# 模组清单 · 我的实例", text)
        self.assertIn("共 2 个模组", text)
        self.assertIn("| 名称 | 版本 | 加载器 | 文件 | 状态 |", text)
        self.assertIn("| Demo Mod | 1.2.3 | fabric | demo.jar | 启用 |", text)
        # 禁用标记 + 表格管道符转义
        self.assertIn("Other\\|Mod", text)
        self.assertIn("| 禁用 |", text)

    def test_text_export(self):
        self._fill()
        dest = self.base / "out" / "list.txt"
        path = mod_info.export_mod_list(self.mods, dest, fmt="text", title="T")
        text = Path(path).read_text("utf-8")
        self.assertIn("# 模组清单 · T（共 2 个）", text)
        self.assertIn("Demo Mod 1.2.3  [demo.jar]", text)
        self.assertIn("（已禁用）", text)

    def test_empty_dir_still_writes(self):
        dest = self.base / "out" / "empty.md"
        path = mod_info.export_mod_list(self.mods, dest, title="空")
        self.assertIn("共 0 个模组", Path(path).read_text("utf-8"))

    def test_bridge_facade(self):
        from unittest.mock import PropertyMock
        from mclauncher.config import CONFIG
        from bridge.api import BackendAPI

        root = self.base / "instances"
        root.mkdir()
        inst_patch = patch.object(type(CONFIG), "instances_dir",
                                  new_callable=PropertyMock, return_value=root)
        inst_patch.start()
        self.addCleanup(inst_patch.stop)
        from mclauncher.instances import Instance
        inst = Instance("测试")
        inst.create()
        _fabric_jar(inst.path / "mods" / "demo.jar")

        class _Bus:
            def emit(self, *a, **k):
                pass

        api = BackendAPI(_Bus())
        path = api.export_mod_list("测试")
        self.assertTrue(path.endswith("modlist-测试.md"))
        self.assertIn("Demo Mod", Path(path).read_text("utf-8"))
        self.assertTrue(str(Path(path)).startswith(str(self.base / "root")))

    def test_qt_backend_has_same_method(self):
        import ast
        src = (Path(__file__).resolve().parents[1] / "app" / "backend.py").read_text("utf-8")
        tree = ast.parse(src)
        names = {n.name for n in ast.walk(tree)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        self.assertIn("export_mod_list", names)


if __name__ == "__main__":
    unittest.main()
