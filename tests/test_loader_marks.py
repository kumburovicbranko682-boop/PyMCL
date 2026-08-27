# -*- coding: utf-8 -*-
"""加载器版本「推荐 / 最新 / 测试版」标注（PCL2 / HMCL 同款）。

覆盖：
- forge_promos 官方 promotions_slim 解析、BMCLAPI 回退、全失败降级空表
- _forge 按 promotions 打 recommended / latest 标（含 1.7.10 带 branch 构件）
- fabric stable 位透传
- 标注失败不影响版本列表本身
- 两个门面签名对齐
"""
from __future__ import annotations

import unittest

from mclauncher import loader_meta
from mclauncher.installer import BMCLAPI


class _FakeDM:
    """按 URL 分发返回值的假 DownloadManager；值为 Exception 时抛出。"""

    def __init__(self, routes: dict):
        self.routes = routes
        self.calls: list[str] = []

    def _hit(self, url):
        self.calls.append(url)
        for key, val in self.routes.items():
            if key in url:
                if isinstance(val, Exception):
                    raise val
                return val
        raise AssertionError(f"unexpected URL: {url}")

    def fetch_json(self, url, timeout=None, expand=True, **kw):
        return self._hit(url)

    def fetch_text(self, url, timeout=None, expand=True, **kw):
        return self._hit(url)


OFFICIAL_PROMOS = {
    "homepage": "https://files.minecraftforge.net/net/minecraftforge/forge/",
    "promos": {
        "1.20.1-recommended": "47.2.0",
        "1.20.1-latest": "47.3.12",
        "1.7.10-recommended": "10.13.4.1614",
        "1.7.10-latest": "10.13.4.1614",
    },
}

BMCL_PROMOS = [
    {"name": "1.20.1-recommended", "build": {"version": "47.2.0"}},
    {"name": "1.20.1-latest", "build": {"version": "47.3.12"}},
    {"name": "bad-row", "build": {}},          # 缺 version，跳过
    {"name": "", "build": {"version": "1.0"}},  # 缺 name，跳过
]


class TestForgePromos(unittest.TestCase):
    def test_official_format(self):
        dm = _FakeDM({"promotions_slim.json": OFFICIAL_PROMOS})
        promos = loader_meta.forge_promos(dm)
        self.assertEqual(promos["1.20.1-recommended"], "47.2.0")
        self.assertEqual(promos["1.20.1-latest"], "47.3.12")
        # 官方成功就不打 BMCLAPI
        self.assertEqual(len(dm.calls), 1)

    def test_fallback_to_bmclapi(self):
        dm = _FakeDM({
            "promotions_slim.json": OSError("blocked"),
            f"{BMCLAPI}/forge/promos": BMCL_PROMOS,
        })
        promos = loader_meta.forge_promos(dm)
        self.assertEqual(promos, {
            "1.20.1-recommended": "47.2.0",
            "1.20.1-latest": "47.3.12",
        })

    def test_both_fail_returns_empty(self):
        dm = _FakeDM({
            "promotions_slim.json": OSError("down"),
            f"{BMCLAPI}/forge/promos": OSError("down too"),
        })
        self.assertEqual(loader_meta.forge_promos(dm), {})

    def test_official_malformed_falls_back(self):
        dm = _FakeDM({
            "promotions_slim.json": {"no_promos_key": 1},
            f"{BMCLAPI}/forge/promos": BMCL_PROMOS,
        })
        promos = loader_meta.forge_promos(dm)
        self.assertEqual(promos["1.20.1-recommended"], "47.2.0")


class TestForgeMarks(unittest.TestCase):
    def _rows(self, mc, builds, promos=OFFICIAL_PROMOS):
        dm = _FakeDM({
            f"{BMCLAPI}/forge/minecraft/{mc}": builds,
            "promotions_slim.json": promos,
        })
        return loader_meta.list_loader_versions(dm, mc, "forge")

    def test_recommended_and_latest_marks(self):
        rows = self._rows("1.20.1", [
            {"version": "47.3.12", "mcversion": "1.20.1"},
            {"version": "47.2.0", "mcversion": "1.20.1"},
            {"version": "47.1.0", "mcversion": "1.20.1"},
        ])
        by_id = {r["id"]: r for r in rows}
        self.assertTrue(by_id["1.20.1-47.2.0"]["recommended"])
        self.assertFalse(by_id["1.20.1-47.2.0"]["latest"])
        self.assertTrue(by_id["1.20.1-47.3.12"]["latest"])
        self.assertFalse(by_id["1.20.1-47.3.12"]["recommended"])
        self.assertFalse(by_id["1.20.1-47.1.0"]["recommended"])
        self.assertFalse(by_id["1.20.1-47.1.0"]["latest"])
        # 排序不受标注影响：最新构建在最前
        self.assertEqual(rows[0]["id"], "1.20.1-47.3.12")

    def test_branch_artifact_1710(self):
        """1.7.10 构件是 1.7.10-<ver>-1.7.10，标注要按中段构建号比对。"""
        rows = self._rows("1.7.10", [
            {"version": "10.13.4.1614", "mcversion": "1.7.10", "branch": "1.7.10"},
            {"version": "10.13.2.1230", "mcversion": "1.7.10"},
        ])
        by_id = {r["id"]: r for r in rows}
        row = by_id["1.7.10-10.13.4.1614-1.7.10"]
        self.assertTrue(row["recommended"])
        self.assertTrue(row["latest"])
        self.assertFalse(by_id["1.7.10-10.13.2.1230"]["recommended"])

    def test_promos_failure_keeps_list(self):
        """promotions 拉不到时版本列表照常返回，只是没有标注。"""
        rows = self._rows("1.20.1", [
            {"version": "47.2.0", "mcversion": "1.20.1"},
        ], promos=OSError("promos down"))
        # BMCLAPI promos 回退也失败
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0]["recommended"])
        self.assertFalse(rows[0]["latest"])
        self.assertTrue(rows[0]["stable"])

    def test_no_promo_entry_for_mc(self):
        rows = self._rows("1.21.3", [
            {"version": "53.0.1", "mcversion": "1.21.3"},
        ])
        self.assertFalse(rows[0]["recommended"])
        self.assertFalse(rows[0]["latest"])


class TestFabricStable(unittest.TestCase):
    def test_stable_flag_passthrough(self):
        dm = _FakeDM({
            "/versions/loader/1.20.1": [
                {"loader": {"version": "0.16.9", "stable": True}},
                {"loader": {"version": "0.17.0-beta.1", "stable": False}},
            ],
        })
        rows = loader_meta.list_loader_versions(dm, "1.20.1", "fabric")
        self.assertEqual(
            [(r["id"], r["stable"]) for r in rows],
            [("0.16.9", True), ("0.17.0-beta.1", False)],
        )


class TestFacades(unittest.TestCase):
    def test_bridge_api_has_method(self):
        from bridge.api import BackendAPI
        self.assertTrue(callable(getattr(BackendAPI, "list_loader_versions", None)))

    def test_decorate_marks(self):
        """向导的标注函数：推荐 / 测试版后缀只改显示文本。"""
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from app.pages.install_wizard import InstallWizardDialog
        dec = InstallWizardDialog._decorate
        self.assertEqual(dec({"label": "1.20.1-47.2.0", "stable": True,
                              "recommended": True}), "1.20.1-47.2.0（推荐）")
        self.assertEqual(dec({"label": "0.17.0-beta.1", "stable": False}),
                         "0.17.0-beta.1（测试版）")
        self.assertEqual(dec({"label": "47.9.9-pre1", "stable": False,
                              "recommended": True}), "47.9.9-pre1（推荐，测试版）")
        self.assertEqual(dec({"label": "0.16.9", "stable": True}), "0.16.9")


if __name__ == "__main__":
    unittest.main()
