# -*- coding: utf-8 -*-
"""添加/编辑服务器：一个对话框填完，不再连闯三个模态框。

钉住的行为：
- 旧流程：添加 = 名称框 → 地址框 → 端口框 三连；编辑 = 两连且把端口
  丢回默认。现在一个框（名称可选 + 地址，地址可写 host:port）；
- 地址解析：host / host:port / 非法端口回退 25565；
- 空地址本地拦下，不调后端；
- 编辑时带端口的地址正确回填（host:port）；
- 页面源码不再使用逐项 InputDialog 链。

全程 offscreen，对话框打桩不 exec，后端打桩，不联网。
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYMCL_HOME", tempfile.mkdtemp(prefix="pymcl_test_srv_"))

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mclauncher import feedback as _fb  # noqa: E402

_fb.start_heartbeat = lambda *a, **k: None
_fb.stop_heartbeat = lambda *a, **k: None

_app = None


def setUpModule():
    global _app
    from PySide6.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication([])


class ParseAddressTests(unittest.TestCase):
    def test_parse(self):
        from app.pages.servers_page import parse_server_address
        self.assertEqual(parse_server_address("play.example.com"),
                         ("play.example.com", 25565))
        self.assertEqual(parse_server_address("1.2.3.4:25566"), ("1.2.3.4", 25566))
        self.assertEqual(parse_server_address("  host:80  "), ("host", 80))
        # 非法端口：不炸，回退默认
        host, port = parse_server_address("host:99999")
        self.assertEqual(port, 25565)


class ServerDialogFlowTests(unittest.TestCase):
    def _page(self, servers=None):
        from unittest import mock
        from app.backend import BackendAPI

        added = []
        updated = []
        patches = [
            mock.patch.object(
                BackendAPI, "get_instances",
                lambda self: [{"name": "default", "versions": 0,
                               "mc": "", "java_label": ""}]),
            mock.patch.object(
                BackendAPI, "list_servers",
                lambda self, inst="": list(servers or [])),
            mock.patch.object(
                BackendAPI, "add_server",
                lambda self, inst, name, ip, port=25565, description="":
                    added.append((name, ip, port)) or {}),
            mock.patch.object(
                BackendAPI, "update_server",
                lambda self, inst, idx, **kw: updated.append((idx, kw)) or {}),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        from app.pages.servers_page import ServerPage
        page = ServerPage(BackendAPI())
        page.reload()
        _app.processEvents()
        return page, added, updated

    def test_single_dialog_no_input_chain(self):
        src = (ROOT / "app" / "pages" / "servers_page.py").read_text(encoding="utf-8")
        self.assertNotIn("InputDialog", src,
                         "添加/编辑服务器不得再用逐项 InputDialog 三连")

    def test_dialog_collects_all_fields(self):
        from PySide6.QtWidgets import QWidget
        from app.pages.servers_page import _ServerDialog
        host = QWidget()
        host.resize(800, 600)
        self.addCleanup(host.deleteLater)
        dlg = _ServerDialog("t", parent=host)
        dlg.name_edit.setText("我的服")
        dlg.addr_edit.setText("mc.example.com:25570")
        self.assertEqual(dlg.values(), ("我的服", "mc.example.com", 25570))

    def test_add_goes_through_backend_once(self):
        from unittest import mock
        page, added, _updated = self._page()

        class FakeDlg:
            def __init__(self, *a, **k):
                pass

            def exec(self):
                return True

            def values(self):
                return ("srv", "play.example.com", 25566)

        with mock.patch("app.pages.servers_page._ServerDialog", FakeDlg):
            page._on_add()
        _app.processEvents()
        self.assertEqual(added, [("srv", "play.example.com", 25566)])

    def test_empty_address_blocked_locally(self):
        from unittest import mock
        page, added, _updated = self._page()

        class FakeDlg:
            def __init__(self, *a, **k):
                pass

            def exec(self):
                return True

            def values(self):
                return ("srv", "", 25565)

        with mock.patch("app.pages.servers_page._ServerDialog", FakeDlg):
            page._on_add()
        _app.processEvents()
        self.assertEqual(added, [], "空地址不该发给后端")

    def test_edit_prefills_port_and_keeps_it(self):
        from unittest import mock
        page, _added, updated = self._page(
            servers=[{"name": "srv", "ip": "h.example.com", "port": 25570}])

        captured = {}

        class FakeDlg:
            def __init__(self, title, name="", address="", parent=None):
                captured["name"] = name
                captured["address"] = address

            def exec(self):
                return True

            def values(self):
                return ("srv", "h.example.com", 25570)

        with mock.patch("app.pages.servers_page._ServerDialog", FakeDlg):
            page._on_edit(0)
        _app.processEvents()
        self.assertEqual(captured["address"], "h.example.com:25570",
                         "编辑时非默认端口必须回填，不能丢")
        self.assertEqual(updated[0][1].get("port"), 25570,
                         "保存后端口不能被丢回默认")


if __name__ == "__main__":
    unittest.main(verbosity=2)
