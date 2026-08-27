# -*- coding: utf-8 -*-
"""模组批量管理（PCL2「全选 / 批量启用 / 禁用 / 删除」同款）。

离屏 Qt + 假后端：验证批量模式的勾选框渲染、全选/取消全选跟随筛选、
批量禁用后选中集合映射到 .disabled 新文件名、批量删除带确认与失败保留。
"""
from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class FakeBackend:
    """内存版 mods 目录：文件名 -> 是否启用。"""

    def __init__(self):
        self.mods = {"a.jar": True, "b.jar": True, "c.jar.disabled": False}

    def get_instances(self):
        return [{"name": "default"}]

    def get_mods_targets(self, inst):
        return [{"label": "shared", "value": ""}]

    def get_installed_mod_entries(self, inst, ver=""):
        return [{"filename": f, "enabled": en, "bytes": 10}
                for f, en in sorted(self.mods.items())]

    def get_mods_folder(self, inst, ver=""):
        return ""

    def get_mod_details(self, inst, ver=""):
        return []

    def call_async(self, fn, ok=None, fail=None):
        try:
            result = fn()
        except Exception as e:  # noqa: BLE001
            if fail:
                fail(e)
            return
        if ok:
            ok(result)

    def enable_mod(self, inst, filename, ver=""):
        new = filename[:-len(".disabled")] if filename.endswith(".disabled") else filename
        del self.mods[filename]
        self.mods[new] = True
        return new

    def disable_mod(self, inst, filename, ver=""):
        new = filename if filename.endswith(".disabled") else filename + ".disabled"
        del self.mods[filename]
        self.mods[new] = False
        return new

    def delete_mod(self, inst, filename, ver=""):
        if filename == "undeletable.jar":
            raise OSError("locked")
        del self.mods[filename]


def _row_checkboxes(page):
    from qfluentwidgets import CheckBox
    from app.pages.mod_page import _ModRow
    boxes = []
    for i in range(page.list_layout.count()):
        w = page.list_layout.itemAt(i).widget()
        if isinstance(w, _ModRow):
            cb = w.findChild(CheckBox)
            if cb is not None:
                boxes.append((w.entry.get("filename"), cb))
    return boxes


class TestModBatch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from app.pages.mod_page import ModManagerPage
        self.backend = FakeBackend()
        self.page = ModManagerPage(self.backend)
        self.addCleanup(self.page.deleteLater)

    def test_batch_bar_hidden_by_default(self):
        self.assertFalse(self.page.batch_bar.isVisibleTo(self.page))
        self.assertEqual(_row_checkboxes(self.page), [])

    def test_toggle_batch_shows_checkboxes(self):
        self.page.batch_btn.setChecked(True)
        self.assertTrue(self.page.batch_bar.isVisibleTo(self.page))
        boxes = _row_checkboxes(self.page)
        self.assertEqual(len(boxes), 3)
        # 关掉批量模式：选中清空、勾选框消失
        boxes[0][1].setChecked(True)
        self.assertEqual(len(self.page._selected), 1)
        self.page.batch_btn.setChecked(False)
        self.assertEqual(self.page._selected, set())
        self.assertEqual(_row_checkboxes(self.page), [])

    def test_select_all_and_clear(self):
        self.page.batch_btn.setChecked(True)
        self.page._select_all_visible()
        self.assertEqual(self.page._selected,
                         {"a.jar", "b.jar", "c.jar.disabled"})
        self.assertIn("3", self.page.sel_label.text())
        # 已全选时再点一次 → 取消
        self.page._select_all_visible()
        self.assertEqual(self.page._selected, set())

    def test_select_all_respects_filter(self):
        self.page.batch_btn.setChecked(True)
        self.page.search.setText("a.jar")
        self.page._select_all_visible()
        self.assertEqual(self.page._selected, {"a.jar"})

    def test_batch_disable_remaps_selection(self):
        self.page.batch_btn.setChecked(True)
        self.page._selected = {"a.jar", "b.jar"}
        self.page._batch_apply("disable")
        self.assertEqual(self.backend.mods, {
            "a.jar.disabled": False, "b.jar.disabled": False,
            "c.jar.disabled": False})
        # 选中集合映射到新文件名，重载后仍有效
        self.assertEqual(self.page._selected,
                         {"a.jar.disabled", "b.jar.disabled"})

    def test_batch_enable(self):
        self.page.batch_btn.setChecked(True)
        self.page._selected = {"c.jar.disabled"}
        self.page._batch_apply("enable")
        self.assertTrue(self.backend.mods["c.jar"])
        self.assertEqual(self.page._selected, {"c.jar"})

    def test_batch_delete_with_confirm(self):
        self.page.batch_btn.setChecked(True)
        self.page._selected = {"a.jar", "b.jar"}
        confirm = MagicMock()
        confirm.return_value.exec.return_value = 1
        with patch("app.pages.mod_page.MessageBox", confirm):
            self.page._batch_apply("delete")
        self.assertEqual(set(self.backend.mods), {"c.jar.disabled"})
        self.assertEqual(self.page._selected, set())

    def test_batch_delete_cancelled(self):
        self.page.batch_btn.setChecked(True)
        self.page._selected = {"a.jar"}
        confirm = MagicMock()
        confirm.return_value.exec.return_value = 0
        with patch("app.pages.mod_page.MessageBox", confirm):
            self.page._batch_apply("delete")
        self.assertIn("a.jar", self.backend.mods)

    def test_batch_delete_failure_keeps_selection(self):
        self.backend.mods["undeletable.jar"] = True
        self.page.reload_list()
        self.page.batch_btn.setChecked(True)
        self.page._selected = {"a.jar", "undeletable.jar"}
        confirm = MagicMock()
        confirm.return_value.exec.return_value = 1
        with patch("app.pages.mod_page.MessageBox", confirm):
            self.page._batch_apply("delete")
        self.assertNotIn("a.jar", self.backend.mods)
        self.assertIn("undeletable.jar", self.backend.mods)
        self.assertEqual(self.page._selected, {"undeletable.jar"})

    def test_stale_selection_pruned_on_refill(self):
        self.page.batch_btn.setChecked(True)
        self.page._selected = {"a.jar", "ghost.jar"}
        self.page._refill()
        self.assertEqual(self.page._selected, {"a.jar"})


if __name__ == "__main__":
    unittest.main()
