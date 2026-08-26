# -*- coding: utf-8 -*-
"""服务器页表单：加一个服务器不再连闯三个模态框，编辑时端口能改。

以前 _on_add 依次弹名称/地址/端口三个 InputDialog（6 次交互起步），
_on_edit 弹两个而且端口没处改。现在一个表单一次填完。

钉住：
1. 添加：一个对话框 → backend.add_server 收到名称/地址/端口；
2. 编辑：表单预填旧值，端口可改 → backend.update_server 收到新端口；
3. 地址留空时 validate() 拦下，不许提交；
4. 无服务器时显示空状态。

全程 offscreen + 临时数据目录，对话框 exec 打桩，不弹任何窗口。
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYMCL_HOME", tempfile.mkdtemp(prefix="pymcl_test_srv_"))

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget  # noqa: E402

_app = QApplication.instance() or QApplication([])

from app.pages import servers_page as sp_mod  # noqa: E402
from app.pages.servers_page import ServerPage, _ServerFormDialog  # noqa: E402


class _StubBackend:
    def __init__(self):
        self.servers = []
        self.added = []
        self.updated = []

    def get_instances(self):
        return [{"name": "default", "versions": 1, "mc": "1.21.1",
                 "java_label": ""}]

    def list_servers(self, instance):
        return list(self.servers)

    def add_server(self, instance, name, ip, port):
        self.added.append((instance, name, ip, port))
        self.servers.append({"name": name, "ip": ip, "port": port})

    def update_server(self, instance, index, name="", ip="", port=25565):
        self.updated.append((instance, index, name, ip, port))
        self.servers[index] = {"name": name, "ip": ip, "port": port}


class ServerFormTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.backend = _StubBackend()
        cls.host = QWidget()
        cls.host.resize(1000, 700)
        lay = QVBoxLayout(cls.host)
        cls.page = ServerPage(cls.backend)
        lay.addWidget(cls.page)
        cls.host.show()
        _app.processEvents()
        cls.page.reload()
        _app.processEvents()

    @classmethod
    def tearDownClass(cls):
        cls.host.close()
        _app.processEvents()

    def test_1_empty_state_shown(self):
        self.assertIs(self.page._body.currentWidget(), self.page.empty,
                      "没有服务器时应显示空状态")

    def test_2_add_uses_single_form(self):
        exec_calls = []
        orig_exec = _ServerFormDialog.exec

        def fake_exec(dlg):
            exec_calls.append(1)
            dlg.name_edit.setText("Hypixel")
            dlg.ip_edit.setText("mc.hypixel.net")
            dlg.port_edit.setText("25565")
            return 1

        _ServerFormDialog.exec = fake_exec
        try:
            self.page._on_add()
        finally:
            _ServerFormDialog.exec = orig_exec
        _app.processEvents()

        self.assertEqual(len(exec_calls), 1, "添加服务器只该弹一个对话框")
        self.assertEqual(self.backend.added,
                         [("default", "Hypixel", "mc.hypixel.net", 25565)])

    def test_3_edit_can_change_port(self):
        self.backend.servers = [{"name": "S1", "ip": "1.2.3.4", "port": 25565}]
        self.page.reload()
        _app.processEvents()

        seen = {}
        orig_exec = _ServerFormDialog.exec

        def fake_exec(dlg):
            seen["prefill"] = dlg.values()
            dlg.port_edit.setText("25599")
            return 1

        _ServerFormDialog.exec = fake_exec
        try:
            self.page._on_edit(0)
        finally:
            _ServerFormDialog.exec = orig_exec
        _app.processEvents()

        self.assertEqual(seen["prefill"], ("S1", "1.2.3.4", 25565),
                         "编辑表单应预填旧值")
        self.assertEqual(self.backend.updated[-1],
                         ("default", 0, "S1", "1.2.3.4", 25599),
                         "改掉的端口必须真的传给后端")

    def test_4_empty_address_blocked(self):
        dlg = _ServerFormDialog("t", parent=self.page)
        dlg.ip_edit.setText("   ")
        self.assertFalse(dlg.validate(), "地址留空不许提交")
        dlg.ip_edit.setText("example.com")
        self.assertTrue(dlg.validate())
        dlg.deleteLater()
        _app.processEvents()

    def test_5_bad_port_falls_back(self):
        dlg = _ServerFormDialog("t", ip="a.b", parent=self.page)
        dlg.port_edit.setText("not-a-port")
        self.assertEqual(dlg.values(), ("", "a.b", 25565),
                         "非法端口应回落默认 25565")
        dlg.deleteLater()
        _app.processEvents()


if __name__ == "__main__":
    unittest.main(verbosity=2)
