# PyMCL 公益 AI 网关

启动器**不带**任何 NewAPI 令牌（旧版内嵌混淆令牌已退役）。把 `sk-` 只放在这台机器的环境变量里。

```bat
copy .env.example .env
:: 编辑 .env 填你的 NewAPI
python server.py
```

健康检查：`GET http://127.0.0.1:8787/health`（返回默认模型与白名单）
对话口：`POST /pymcl/chat`（必须带请求头 `X-PyMCL-Client: PyMCL/x.y.z`）

## 模型白名单

`NEWAPI_ALLOWED_MODELS=deepseek-v3.2,deepseek-r1` 之类的逗号分隔列表。
客户端请求 body 里的 `model` 在名单内（或等于默认模型）才放行，否则回落
`NEWAPI_MODEL`。启动器的「深度诊断」档会请求 `DIAGNOSE_MODEL`
（见 `mclauncher/ai/defaults.py`），想启用就把它加进白名单。

## 发布前的运维步骤（必须）

1. 找一台公网机器跑本网关（`python server.py`，建议 systemd/进程守护）。
2. **配 HTTPS**：客户端拒绝非内网的明文 HTTP 网关。用 Caddy / Nginx +
   Let's Encrypt 反代 `127.0.0.1:8787` 即可。
3. 把 HTTPS 地址填进 `mclauncher/ai/defaults.py` 的 `DEFAULT_GATEWAY_URL`
   （或打包时设置环境变量 `PYMCL_AI_GATEWAY`），不要带 `/v1` 或 `/pymcl/chat`。
4. 上游令牌轮换：旧版客户端内嵌过的令牌应在 NewAPI 侧作废。
