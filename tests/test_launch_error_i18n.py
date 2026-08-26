# -*- coding: utf-8 -*-
"""启动失败的报错必须跟界面语言走，且要指出「现在该点哪里」。

钉住的行为：
- 英文界面下 build_launch_command 抛出的 LaunchError 是英文
  （之前 launcher.py 没接 i18n，英文用户启动失败只能看中文）；
- 报错指向真实侧栏名「下载 → 原版游戏 / 下载 → Java」，
  不再出现「命令含模块参数」这种没法照做的行话；
- launcher.py 里不再残留裸中文的 LaunchError 文案。

纯逻辑测试，临时目录，不联网、不弹窗。
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
import tokenize
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYMCL_HOME", tempfile.mkdtemp(prefix="pymcl_test_le_"))

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mclauncher import i18n  # noqa: E402
from mclauncher.launcher import LaunchError, build_launch_command  # noqa: E402


class _EmptyInst:
    """version_json 永远返回 None：模拟版本没装。"""

    def __init__(self, path):
        self.path = Path(path)

    def version_json(self, _v):
        return None


class LaunchErrorLanguageTests(unittest.TestCase):
    def tearDown(self):
        i18n.set_language("zh_CN")

    def _missing_version_error(self):
        inst = _EmptyInst(tempfile.mkdtemp(prefix="pymcl_le_inst_"))
        with self.assertRaises(LaunchError) as ctx:
            build_launch_command(inst, "1.20.1", {}, None)
        return str(ctx.exception)

    def test_missing_version_error_english(self):
        i18n.set_language("en")
        msg = self._missing_version_error()
        self.assertIn("1.20.1", msg)
        self.assertIn("Download", msg, "英文报错应指向英文侧栏名")
        self.assertNotIn("下载", msg, "英文界面不该混中文")

    def test_missing_version_error_chinese_points_to_sidebar(self):
        i18n.set_language("zh_CN")
        msg = self._missing_version_error()
        self.assertIn("1.20.1", msg)
        self.assertIn("下载 → 原版游戏", msg, "报错要说清现在该点哪里")


class LaunchErrorCopyTests(unittest.TestCase):
    """静态扫 launcher.py：LaunchError 不留裸中文，不留行话。"""

    def _source(self):
        p = Path(__file__).resolve().parent.parent / "mclauncher" / "launcher.py"
        return p.read_text("utf-8")

    def test_no_bare_chinese_launch_errors(self):
        src = self._source()
        toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
        bad = []
        for idx, tok in enumerate(toks):
            if tok.type != tokenize.STRING:
                continue
            if not any("\u4e00" <= ch <= "\u9fff" for ch in tok.string):
                continue
            # 找它前面最近的两个有效 token，若是 tr( 则算已翻译
            prev = [t for t in toks[:idx]
                    if t.type not in (tokenize.NL, tokenize.NEWLINE,
                                      tokenize.INDENT, tokenize.DEDENT,
                                      tokenize.COMMENT)][-2:]
            if len(prev) == 2 and prev[0].string == "tr" and prev[1].string == "(":
                continue
            # 上一个 token 是中文字符串（tr( 的续行）也算已翻译
            if prev and prev[-1].type == tokenize.STRING:
                continue
            line = src.splitlines()[tok.start[0] - 1]
            if "LaunchError" in line or "raise" in line:
                bad.append((tok.start[0], tok.string[:40]))
        self.assertEqual(bad, [], f"LaunchError 还有未翻译的中文文案: {bad}")

    def test_no_module_arg_jargon(self):
        src = self._source()
        self.assertNotIn("命令含模块参数", src,
                         "「命令含模块参数」是行话，用户没法照做")
        self.assertNotIn("Unrecognized option: -p", src,
                         "JVM 报错原文不该直接塞给用户")


if __name__ == "__main__":
    unittest.main(verbosity=2)
