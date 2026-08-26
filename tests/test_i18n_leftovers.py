# -*- coding: utf-8 -*-
"""英文界面不再夹生中文：剩余硬编码文案全部走 tr()。

钉住的行为：
- 底部下载条的「下载任务（N）」计数标题走翻译（之前是 f-string，
  切英文后还是中文）；
- 模组删除确认、设置页主目录/主题包提示、文件选择器的「任意」
  不再以硬编码形式存在；
- 新增的键在 en / zh_CN 两份语言文件里都有。

全程 offscreen，信号手动触发，不联网。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYMCL_HOME", tempfile.mkdtemp(prefix="pymcl_test_i18n_"))

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mclauncher import feedback as _fb  # noqa: E402

_fb.start_heartbeat = lambda *a, **k: None
_fb.stop_heartbeat = lambda *a, **k: None

from mclauncher.i18n import tr  # noqa: E402

_app = None


def setUpModule():
    global _app
    from PySide6.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication([])


class I18nLeftoverTests(unittest.TestCase):
    def test_locale_keys_exist(self):
        keys = [
            "下载任务（{0}）",
            "将删除模组文件「{0}」，不可恢复。",
            "启动器主目录: {0}",
            "主题包「{0}」已保存",
            "主题包「{0}」已应用",
            "主题包「{0}」已删除",
            "任意",
            "实例 {0} → exports/",
            "导入任务已启动: {0}",
            "内存已设为 {0} MB，保存设置后生效",
            "加载更多（还有 {0}）",
            "桌面快捷方式：\n{0}\n\n双击即可直接启动该版本。",
            "将卸载 {0} 个版本：\n",
            "将删除整个实例「{0}」及其文件，不可恢复。",
            "将永久删除世界「{0}」，其中的建筑与游戏进度都无法恢复。",
            "将删除「{0}」。",
            "无法打开: {0}",
            "已备份到 {0}",
            "账号不存在: {0}",
            "已安装 {0}",
            "已登录 {0}",
            "已更新 {0} 个模组",
            "已安装世界 {0}",
            "Java {0} ({1}) 安装完成",
            "已导入 {0} 个版本",
        ]
        for loc in ("en.json", "zh_CN.json"):
            data = json.loads((ROOT / "mclauncher" / "locales" / loc).read_text(encoding="utf-8"))
            for k in keys:
                self.assertIn(k, data, f"{loc} 缺少键：{k}")

    def test_hardcoded_forms_gone(self):
        cases = [
            ("app/pages/tasks_page.py", 'f"下载任务（'),
            ("app/pages/mod_page.py", 'f"将删除模组文件'),
            ("app/pages/settings_page.py", 'f"启动器主目录'),
            ("app/pages/settings_page.py", 'f"主题包「'),
            ("app/pages/settings_page.py", 'f"实例 {name}'),
            ("app/pages/settings_page.py", 'f"发现官方启动器目录'),
            ("app/pages/settings_page.py", 'f"导入任务已启动'),
            ("app/pages/settings_page.py", 'f"你的系统'),
            ("app/pages/settings_page.py", 'f"内存已设为'),
            ("app/pages/file_pick.py", "or '任意'"),
            ("app/pages/version_page.py", 'f"加载更多'),
            ("app/pages/version_page.py", 'f"桌面快捷方式'),
            ("app/pages/version_page.py", 'f"将卸载'),
            ("app/pages/version_page.py", 'f"{len(selected)} 个版本"'),
            ("app/pages/catalog_page.py", 'f"将删除整个实例'),
            ("app/pages/catalog_page.py", 'f"将永久删除世界'),
            ("app/pages/catalog_page.py", 'f"将删除「'),
            # 任务完成消息（任务卡「✔」行）与启动错误必须走 tr()
            ("app/backend.py", 'return f"已'),
            ("app/backend.py", 'raise LaunchError(f"'),
            ("app/backend.py", 'f"已保存 ('),
        ]
        for rel, needle in cases:
            src = (ROOT / rel).read_text(encoding="utf-8")
            self.assertNotIn(needle, src,
                             f"{rel} 里 {needle!r} 又变回硬编码了")

    def test_task_titles_translated(self):
        """任务标题显示在「下载任务」列表里，必须走 tr()。"""
        src = (ROOT / "app" / "backend.py").read_text(encoding="utf-8")
        self.assertNotIn('start_task(f"', src,
                         "backend.py 出现硬编码任务标题（start_task(f\"…\"））")
        for loc in ("en.json", "zh_CN.json"):
            data = json.loads((ROOT / "mclauncher" / "locales" / loc).read_text(encoding="utf-8"))
            for k in ("修复", "导出整合包", "检查模组更新", "安装世界"):
                self.assertIn(k, data, f"{loc} 缺少任务标题键：{k}")
        en = json.loads((ROOT / "mclauncher" / "locales" / "en.json").read_text(encoding="utf-8"))
        self.assertEqual(en["安装世界"], "Install World",
                         "英文翻译不能是挤在一起的 InstallWorld")

    def test_dock_count_title_translated(self):
        from app.backend import BackendAPI
        from app.pages.tasks_page import DownloadDock
        backend = BackendAPI()
        dock = DownloadDock(backend)
        self.addCleanup(dock.deleteLater)
        backend.task_added.emit("t1", "下载 Java 17")
        _app.processEvents()
        self.assertEqual(dock.title.text(), tr("下载任务（{0}）").format(1),
                         "下载条计数标题应经 tr() 渲染")
        backend.finished.emit("t1", True, "done")
        _app.processEvents()
        self.assertEqual(dock.title.text(), tr("下载任务"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
