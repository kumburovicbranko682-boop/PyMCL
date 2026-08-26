# -*- coding: utf-8 -*-
"""启动预检的标题与说明必须跟界面语言走。

钉住的行为：
- 英文界面下 check_launch 返回的 title/detail 是英文
  （之前整个 preflight.py 没接 i18n，英文用户被拦下时
  只能看中文原因）；
- 中文界面下文案不变，且「未选择版本」仍指向「下载 → 原版游戏」。

纯逻辑测试，临时目录，不联网、不弹窗。
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYMCL_HOME", tempfile.mkdtemp(prefix="pymcl_test_pf_"))

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mclauncher import i18n  # noqa: E402
from mclauncher.i18n import tr  # noqa: E402
from mclauncher.preflight import check_launch  # noqa: E402


class _FakeInst:
    def __init__(self, path):
        self.path = Path(path)

    def version_json(self, _v):
        return None


class PreflightI18nTests(unittest.TestCase):
    def tearDown(self):
        i18n.set_language("zh_CN")

    def _codes(self, result):
        return {i["code"]: i for i in result["items"]}

    def test_no_version_message_localized(self):
        home = tempfile.mkdtemp(prefix="pymcl_pf_inst_")
        for lang in ("zh_CN", "en"):
            i18n.set_language(lang)
            out = check_launch(_FakeInst(home), "")
            items = self._codes(out)
            self.assertIn("no_version", items)
            self.assertEqual(items["no_version"]["title"], tr("未选择版本"))
            self.assertEqual(items["no_version"]["detail"],
                             tr("请先到「下载 → 原版游戏」安装版本"))
        i18n.set_language("en")
        out = check_launch(_FakeInst(home), "")
        detail = self._codes(out)["no_version"]["detail"]
        self.assertNotIn("下载", detail, "英文界面不该混中文")
        self.assertIn("Download", detail, "英文提示应指向真实的英文侧栏名")

    def test_missing_instance_localized(self):
        for lang in ("zh_CN", "en"):
            i18n.set_language(lang)
            out = check_launch(_FakeInst("/nonexistent/pymcl-test-dir"), "1.20.1")
            items = self._codes(out)
            self.assertIn("no_instance", items)
            self.assertEqual(items["no_instance"]["title"], tr("实例目录不存在"))

    def test_version_not_installed_localized(self):
        home = tempfile.mkdtemp(prefix="pymcl_pf_inst2_")
        i18n.set_language("en")
        out = check_launch(_FakeInst(home), "1.20.1")
        items = self._codes(out)
        self.assertIn("no_version_json", items)
        self.assertEqual(items["no_version_json"]["title"], tr("版本未安装"))
        self.assertEqual(items["no_version_json"]["detail"],
                         tr("找不到 {0} 的版本 JSON").format("1.20.1"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
