# -*- coding: utf-8 -*-
"""代码里用到的每个配置键都必须在 DEFAULT_CONFIG 声明。

Config.load() 只回读 DEFAULT_CONFIG 里有的键：漏声明的键写进
config.json 后，下次启动会被静默丢掉。「关掉界面动画重启又开了」
「拖好的侧栏顺序重启全没了」都是这一类缺陷。这里静态扫描钉死。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _default_keys() -> set[str]:
    src = (ROOT / "mclauncher" / "config.py").read_text(encoding="utf-8")
    body = re.search(r"DEFAULT_CONFIG = \{(.*?)\n\}", src, re.S).group(1)
    return set(re.findall(r'"([a-z0-9_]+)":', body))


def _used_keys() -> set[str]:
    used: set[str] = set()
    for d in ("app", "mclauncher", "bridge"):
        for py in (ROOT / d).rglob("*.py"):
            if "_app_backup" in str(py):
                continue
            text = py.read_text(encoding="utf-8", errors="replace")
            used.update(re.findall(r'CONFIG\.(?:get|set)\(\s*"([a-z0-9_]+)"', text))
    return used


class ConfigKeysPersistTest(unittest.TestCase):
    def test_every_used_key_is_declared(self):
        missing = sorted(_used_keys() - _default_keys())
        self.assertEqual(
            missing, [],
            "这些配置键没进 DEFAULT_CONFIG，用户设置会在重启时被静默丢掉: "
            + ", ".join(missing))

    def test_ui_state_survives_reload(self):
        """写一份带界面自定义的 config.json，冷启动 Config 后必须原样读回。"""
        stored = {
            "ui_motion": False,
            "ui_nav_order": ["tasks", "launch", "download", "ai", "more"],
            "ui_nav_pinned": ["settings"],
            "ui_section_members": {"download": ["version"], "more": ["settings"]},
            "ui_sidebar_width": 200,
        }
        script = (
            "import json, os\n"
            "from mclauncher.config import CONFIG\n"
            "print(json.dumps({k: CONFIG.get(k) for k in "
            + repr(sorted(stored)) + "}))\n"
        )
        with tempfile.TemporaryDirectory(prefix="pymcl_test_") as home:
            Path(home, "config.json").write_text(
                json.dumps(stored), encoding="utf-8")
            env = dict(os.environ)
            env["PYMCL_HOME"] = home
            proc = subprocess.run(
                [sys.executable, "-c", script], cwd=str(ROOT),
                env=env, capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            got = json.loads(proc.stdout.strip().splitlines()[-1])
            for key, want in stored.items():
                self.assertEqual(got.get(key), want,
                                 f"{key} 重启后没有还原（读到 {got.get(key)!r}）")


if __name__ == "__main__":
    unittest.main()
