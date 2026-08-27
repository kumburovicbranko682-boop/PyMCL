# -*- coding: utf-8 -*-
"""公益接口内嵌端点：已退役。

以前这里用 XOR 碎片藏了一个上游 NewAPI 的 sk 令牌直连地址。任何人拿到
发行包都能几行代码还原出完整令牌（明文等价），令牌一旦被刷爆或撤销，
所有已分发客户端的 AI 会同时失效，而且直连走的是明文 HTTP。

现在公益接口只走 ai_gateway/server.py：令牌只存在网关服务器的环境变量里，
客户端凭 X-PyMCL-Client 头访问网关，网关侧做限流与模型白名单。
发行前在 defaults.DEFAULT_GATEWAY_URL（或 PYMCL_AI_GATEWAY 环境变量）
填上网关的 HTTPS 公网地址即可。
"""

from __future__ import annotations

from .defaults import DEFAULT_MODEL


def public_model() -> str:
    return DEFAULT_MODEL
