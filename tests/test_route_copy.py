# -*- coding: utf-8 -*-
"""指路文案必须和真实界面一致。

侧栏里没有「版本页」，安装版本在「下载 → 原版游戏」；装 Mod 在
「下载 → Mod」；装 Java 在「下载 → Java」。报错里把人指到不存在的
页面就是死胡同。这里扫描源代码，禁止旧的错误指路回归，并验证
预检的真实输出。
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("PYMCL_HOME", tempfile.mkdtemp(prefix="pymcl_test_route_"))

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

# 用户会看到的旧指路：页面名在侧栏/下载横条里不存在或含糊
_FORBIDDEN = [
    "「版本」页",
    "请到版本页",
    "到「启动」页安装",
    "到「模组」页安装",
    "「Java」页下载",
    "稍后请在版本页选择",
    # 下载横条的分类叫「原版游戏 / Mod / 整合包 / 光影包」，
    # 空状态/提示里不得再用「版本 / 模组 / 光影」或含糊的「下载」页
    "「下载」页",
    "去下载板块里的版本",
]

_SCAN_DIRS = ["app", "mclauncher"]
_SKIP_PARTS = {"_app_backup_i18n", "locales", "__pycache__"}


def _iter_sources():
    for d in _SCAN_DIRS:
        for f in (_ROOT / d).rglob("*.py"):
            if any(part in _SKIP_PARTS for part in f.parts):
                continue
            yield f


class RouteCopyTests(unittest.TestCase):
    def test_no_stale_route_names_in_sources(self):
        offenders = []
        for f in _iter_sources():
            text = f.read_text(encoding="utf-8", errors="replace")
            for pat in _FORBIDDEN:
                if pat in text:
                    offenders.append(f"{f.relative_to(_ROOT)}: {pat}")
        self.assertEqual(offenders, [],
                         "以下文件仍把用户指到不存在/含糊的页面：\n" + "\n".join(offenders))

    def test_preflight_points_to_real_page(self):
        from mclauncher import preflight
        inst_dir = tempfile.mkdtemp(prefix="pymcl_test_inst_")
        result = preflight.check_launch(_FakeInstance(inst_dir), "")
        item = next(i for i in result["items"] if i["code"] == "no_version")
        self.assertIn("下载 → 原版游戏", item["detail"])

    def test_launch_error_points_to_real_page(self):
        from mclauncher.i18n import tr
        # backend 启动错误路径的 key 已改名；确认新 key 在两个语言包里都有着落
        key = "请先选择版本（到「下载 → 原版游戏」安装）"
        self.assertIn("下载 → 原版游戏", tr(key))
        self.assertIn("Download → Vanilla", tr(key, "en"))
        key2 = "请先到「下载 → 原版游戏」安装一个版本。"
        self.assertIn("Download → Vanilla", tr(key2, "en"))


class _FakeInstance:
    def __init__(self, path):
        self.path = Path(path)

    def version_json(self, _vid):
        return None

    def versions_dir(self):
        return self.path / "versions"


if __name__ == "__main__":
    unittest.main(verbosity=2)
