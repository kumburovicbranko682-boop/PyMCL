# -*- coding: utf-8 -*-
"""测试会话全局设置。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 禁掉中文名数据库的后台预热线程：它会真联网下载 1.7MB 数据、写 cache/，
# 还会在用例之间污染模块级缓存（比如 test_fail_backoff 偶发失败）。
from mclauncher import mod_translate  # noqa: E402

mod_translate._warmed = True
