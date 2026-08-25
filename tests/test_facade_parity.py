# -*- coding: utf-8 -*-
"""门面对齐审计：app/backend.py（Qt）与 bridge/api.py（无 Qt）的公开方法必须一致。

约束来源：改了 backend 公开方法必须同步 bridge.api。
本测试静态解析两份源码（不 import，不需要 PySide6），比较：
1. 公开方法名集合（白名单外不允许单边存在）；
2. 公共方法的参数名与顺序（不比默认值——两边默认值可能各自写死同义常量）。
"""
import ast
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
QT_FILE = ROOT / "app" / "backend.py"
BRIDGE_FILE = ROOT / "bridge" / "api.py"

# Qt 前端专属，桥接端不需要：
#   call_async / shutdown        —— Qt 线程池调度与应用生命周期
#   is_download_title            —— 任务停靠角标的标题分类（纯 UI 约定）
#   invalidate_instances         —— Qt 端实例快照缓存的失效钩子（bridge 无缓存）
QT_ONLY = {"call_async", "shutdown", "is_download_title", "invalidate_instances"}

# RPC 传输层专属，Qt 前端不需要：
#   ai_* —— 桥接端的 AI 会话状态机（事件流 + 确认握手）。
#   Qt 前端在 ai_page 里直接调 mclauncher.ai.agent.run_agent，不走这层。
BRIDGE_ONLY = {"ai_answer", "ai_confirm", "ai_delete_chat", "ai_list_chats",
               "ai_new_chat", "ai_send", "ai_set_active", "ai_stop"}


def _methods(path: Path, cls: str = "BackendAPI") -> dict:
    """{方法名: (参数名元组，不含 self)}，只收公开方法。"""
    tree = ast.parse(path.read_text("utf-8"))
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == cls:
            for n in node.body:
                if (isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and not n.name.startswith("_")):
                    args = [a.arg for a in n.args.args if a.arg != "self"]
                    if n.args.vararg:
                        args.append("*" + n.args.vararg.arg)
                    args += [a.arg for a in n.args.kwonlyargs]
                    if n.args.kwarg:
                        args.append("**" + n.args.kwarg.arg)
                    out[n.name] = tuple(args)
    return out


class FacadeParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = _methods(QT_FILE)
        cls.bridge = _methods(BRIDGE_FILE)

    def test_no_unexpected_qt_only_methods(self):
        drift = set(self.qt) - set(self.bridge) - QT_ONLY
        self.assertFalse(
            drift,
            "这些 backend 公开方法没同步到 bridge/api.py（若确属 Qt 专属请加进白名单并说明）: "
            f"{sorted(drift)}")

    def test_no_unexpected_bridge_only_methods(self):
        drift = set(self.bridge) - set(self.qt) - BRIDGE_ONLY
        self.assertFalse(
            drift,
            "这些 bridge 公开方法在 Qt backend 里不存在（若确属 RPC 专属请加进白名单并说明）: "
            f"{sorted(drift)}")

    def test_whitelists_not_stale(self):
        """白名单条目必须真实存在且仍是单边的，防止清理后留死名单。"""
        qt_only_actual = set(self.qt) - set(self.bridge)
        bridge_only_actual = set(self.bridge) - set(self.qt)
        self.assertEqual(QT_ONLY - qt_only_actual, set(),
                         "QT_ONLY 里有已经不是 Qt 单边的条目，请移除")
        self.assertEqual(BRIDGE_ONLY - bridge_only_actual, set(),
                         "BRIDGE_ONLY 里有已经不是 bridge 单边的条目，请移除")

    def test_common_method_signatures_match(self):
        mismatch = []
        for name in sorted(set(self.qt) & set(self.bridge)):
            if self.qt[name] != self.bridge[name]:
                mismatch.append(f"{name}: qt{self.qt[name]} != bridge{self.bridge[name]}")
        self.assertFalse(
            mismatch,
            "公共方法参数不一致（参数名/顺序必须完全对齐）:\n  " + "\n  ".join(mismatch))


class BridgePortedMethodTests(unittest.TestCase):
    """本轮从 Qt 门面移植到 bridge 的方法：真实调用验证行为。"""

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

    def _make_version(self, inst, vid: str):
        vdir = inst.versions_dir() / vid
        vdir.mkdir(parents=True, exist_ok=True)
        (vdir / f"{vid}.json").write_text(json.dumps({"id": vid}), "utf-8")
        return vdir

    def test_get_mods_targets_lists_isolated_versions(self):
        from mclauncher import version_settings as vs
        inst = self.api._instance("default")
        self._make_version(inst, "1.20.1")
        self._make_version(inst, "1.21-iso")
        vs.save(inst, "1.21-iso", {"isolation": vs.ISOLATION_MODS})

        rows = self.api.get_mods_targets("default")
        self.assertEqual(rows[0]["value"], "")
        self.assertIn("1.21-iso", [r["value"] for r in rows])
        self.assertNotIn("1.20.1", [r["value"] for r in rows])

    def test_get_installed_mods_respects_version_and_disabled(self):
        from mclauncher import version_settings as vs
        inst = self.api._instance("default")
        self._make_version(inst, "1.21-iso")
        vs.save(inst, "1.21-iso", {"isolation": vs.ISOLATION_MODS})
        mods = self.api._mods_folder(inst, "1.21-iso")
        mods.mkdir(parents=True, exist_ok=True)
        (mods / "a.jar").write_bytes(b"jar")
        (mods / "b.jar.disabled").write_bytes(b"jar")

        names = self.api.get_installed_mods("default", "1.21-iso")
        self.assertEqual(names, ["a.jar"])

    def test_get_installed_modpacks_reads_meta(self):
        inst = self.api._instance("default")
        inst.set_meta("modpack", {"name": "AllTheMods", "version": "10"})
        self.assertEqual(self.api.get_installed_modpacks("default"), ["AllTheMods 10"])
        self.assertEqual(self.api.get_installed_modpacks("default")[0],
                         "AllTheMods 10")

    def test_skin_urls_offline_default(self):
        urls = self.api.skin_urls("")
        self.assertIn("avatar", urls)
        self.assertIn("body", urls)
        self.assertTrue(urls["avatar"].startswith("http"))

    def test_local_ips_returns_list(self):
        self.assertIsInstance(self.api.local_ips(), list)

    def test_terracotta_enter_world_requires_room(self):
        from mclauncher.terracotta import TerracottaError
        with self.assertRaises(TerracottaError):
            self.api.terracotta_enter_world()

    def test_add_offline_account_with_skin(self):
        name = self.api.add_offline_account("Steve玩家", skin="alex")
        acc = self.api.accounts.get_account(name)
        self.assertEqual(acc.get("skin"), "alex")


if __name__ == "__main__":
    unittest.main()
