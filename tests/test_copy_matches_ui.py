# -*- coding: utf-8 -*-
"""报错/提示文案必须指向界面上真实存在的地方。

侧栏里没有「版本页」，安装版本的地方叫「下载 → 原版游戏」。
把人指到不存在的页面 = 走投无路。这里钉死两件事：
1. 源码里不再出现指向幽灵页面的导航句式；
2. 文案里引用的「下载 / 原版游戏」与侧栏真实命名一致。
"""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 界面上不存在的去处：报错文案一旦这么写，用户按图索骥必然找不到
_GHOST_PHRASES = [
    "请到版本页",
    "请在版本页",
    "到「版本」页",
    "「启动」页安装",       # 启动页只能启动，安装在「下载 → 原版游戏」
    "请到「模组」页安装",   # 从远端装 Mod 在「下载 → Mod」，模组页只管本地
]

_SCAN_DIRS = ["app", "mclauncher"]
_SKIP_PARTS = {"_app_backup_i18n", "__pycache__", "tests"}


def _py_files():
    for d in _SCAN_DIRS:
        for p in (ROOT / d).rglob("*.py"):
            if not any(part in _SKIP_PARTS for part in p.parts):
                yield p


class CopyMatchesUiTest(unittest.TestCase):
    def test_no_ghost_page_references(self):
        bad = []
        for path in _py_files():
            text = path.read_text(encoding="utf-8", errors="replace")
            for i, line in enumerate(text.splitlines(), 1):
                for phrase in _GHOST_PHRASES:
                    if phrase in line:
                        bad.append(f"{path.relative_to(ROOT)}:{i}: {phrase}")
        self.assertEqual(bad, [], "文案指向了界面上不存在的页面:\n" + "\n".join(bad))

    def test_locales_dropped_ghost_keys(self):
        for name in ("zh_CN.json", "en.json"):
            data = json.loads((ROOT / "mclauncher" / "locales" / name).read_text(encoding="utf-8"))
            for key in data:
                for phrase in _GHOST_PHRASES:
                    self.assertNotIn(phrase, key, f"{name} 还留着幽灵页面词条: {key}")

    def test_multiplayer_intro_leads_with_actions(self):
        """联机页开头必须是「怎么加入」，黑话只能放在技术说明里。"""
        src = (ROOT / "app" / "pages" / "multiplayer_page.py").read_text(encoding="utf-8")
        for jargon in ("打洞", "FRP"):
            self.assertNotIn(jargon, src,
                             f"联机页文案不应再出现黑话「{jargon}」")
        self.assertIn("邀请码即可加入", src, "开头应直接告诉玩家怎么加入")

    def test_install_wizard_leads_with_vanilla_path(self):
        """安装向导第一句必须先讲「直接点开始安装就是原版」，
        再谈加载器——不能拿「主加载器只能选一个」当开场白。"""
        src = (ROOT / "app" / "pages" / "install_wizard.py").read_text(encoding="utf-8")
        self.assertIn("直接点「开始安装」就是原版", src,
                      "向导引导句应先告诉原版玩家零操作通过")
        self.assertIn("想装 Mod 才需要选加载器", src,
                      "应说明加载器只与装 Mod 有关")
        vanilla_at = src.find("直接点「开始安装」就是原版")
        loader_at = src.find("主加载器只能选一个")
        self.assertTrue(0 <= vanilla_at < loader_at,
                        "原版指引必须出现在加载器规则之前")

    def test_download_source_labels_are_shared(self):
        """首次向导与设置页必须共用 DOWNLOAD_SOURCE_LABELS，不许各写一份。"""
        for rel in ("app/pages/first_run.py", "app/pages/settings_page.py"):
            src = (ROOT / rel).read_text(encoding="utf-8")
            self.assertIn("DOWNLOAD_SOURCE_LABELS", src,
                          f"{rel} 应引用共享的下载源标签表")
            self.assertNotIn("官方>4秒", src,
                             f"{rel} 不应再内嵌另一种「自动」说法")

    def test_referenced_names_match_sidebar(self):
        """「下载 → 原版游戏」不是随口一说：必须与主窗侧栏/横条命名一致。"""
        src = (ROOT / "app" / "main_window.py").read_text(encoding="utf-8")
        self.assertIsNotNone(
            re.search(r'"version":\s*"原版游戏"', src),
            "下载横条里 version 子页应叫「原版游戏」，文案引用需同步改")
        self.assertIsNotNone(
            re.search(r'"download":\s*\(FIF\.DOWNLOAD,\s*"下载"\)', src),
            "侧栏一级项 download 应叫「下载」，文案引用需同步改")
        self.assertIsNotNone(
            re.search(r'"mod":\s*"Mod"', src),
            "下载横条里 mod 子页应叫「Mod」，文案引用需同步改")


if __name__ == "__main__":
    unittest.main()
