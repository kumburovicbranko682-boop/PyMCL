# -*- coding: utf-8 -*-
"""AI 默认值。

公益接口只走网关（ai_gateway/server.py）：
- 打包发行前把 DEFAULT_GATEWAY_URL 改成你部署的公益网关 HTTPS 地址，
  或在构建环境里设置 PYMCL_AI_GATEWAY 环境变量（优先级更高）。
- 客户端里不再内嵌任何上游令牌；令牌只存在于网关服务器的环境变量里。
"""

import os

from mclauncher import APP_VERSION

# 例: "https://ai.your-domain.com"  不要带 /v1 或 /pymcl/chat
DEFAULT_GATEWAY_URL = os.environ.get("PYMCL_AI_GATEWAY", "").strip() or ""
DEFAULT_MODEL = "deepseek-v4-flash"
# 深度诊断档：网关白名单里有才生效（见 ai_gateway NEWAPI_ALLOWED_MODELS）
DIAGNOSE_MODEL = "deepseek-v3.2"
CLIENT_HEADER = f"PyMCL/{APP_VERSION}"

# 写操作必须二次确认
WRITE_TOOLS = {
    "install_game",
    "install_mod",
    "install_modpack",
    "install_shader",
    "install_resourcepack",
    "install_datapack",
    "download_java",
    "launch_game",
    "create_instance",
    "delete_instance",
    "delete_mod",
    "disable_mod",
    "enable_mod",
    "write_mod_config",
    "update_mods",
    "repair_version",
    "set_instance_memory",
    "set_instance_java",
}

# 「完全访问」下仍然必须确认的破坏性操作：删了就找不回来或会覆盖用户手改的内容
DANGEROUS_TOOLS = {
    "delete_instance",
    "delete_mod",
    "write_mod_config",
}

MAX_TOOL_ROUNDS = 10
MAX_HISTORY = 24
MAX_TOOL_RESULT = 8000

# 普通回复放宽到 4096；诊断类（读日志/崩溃/冲突）再放宽，避免报告被截断
MAX_TOKENS = 4096
DIAGNOSE_MAX_TOKENS = 6144
DIAGNOSE_TOOLS = {
    "diagnose_launch", "get_latest_log", "get_crash_report", "scan_mod_conflicts",
}

# 这些会进下载任务栏，对话里不要卡到结束
LONG_TOOLS = {
    "install_game",
    "install_mod",
    "install_modpack",
    "install_shader",
    "install_resourcepack",
    "install_datapack",
    "download_java",
    "repair_version",
}

STREAM_CONNECT_TIMEOUT = 15
STREAM_READ_TIMEOUT = 90
ONCE_TIMEOUT = 90
