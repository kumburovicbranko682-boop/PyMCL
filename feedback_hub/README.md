# PyMCL 反馈中心

两个端口，互不混用：

| 端口 | 用途 | 穿透 |
| --- | --- | --- |
| `INGEST_PORT` 默认 18788 | 启动器上报 `POST /api/v1/feedback` `POST /api/v1/heartbeat` | 给用户用，穿透这个 |
| `UI_PORT` 默认 18789 | 开发者看板 WebUI | 只给你自己，另开一条或走 SSH |

```bat
python -m feedback_hub
```

上报口不提供网页。看板：http://127.0.0.1:18789

看板默认强制鉴权：`ADMIN_TOKEN` 没配时会自动生成一个并写入
`data/admin_token.txt`，打开看板带 `?token=<令牌>`（或在登录框里粘贴）。
只有显式设 `NO_ADMIN_TOKEN=1` 才允许无鉴权（不建议）。

CORS：看板口不再反射任意 Origin（防止恶意网页跨域读走看板数据）；
上报口保持宽松，不影响启动器上报。

后台每小时自动清理：压缩 `feedback.jsonl`（超 2 倍保留量重写）、
删除超过 `MACHINE_KEEP_DAYS`（默认 30 天）无心跳的机器记录、
轮转超过 `LOG_MAX_MB` 的 `hub.log`、清理限流器里的陈旧 IP。

对旧客户端完全兼容：`POST /api/v1/feedback`、`POST /api/v1/heartbeat`
的请求字段、`X-PyMCL-Client` 头校验、响应格式（`{"ok":true,"id":...}`）
都没变，新字段（crash 日志尾部）只是可选附加。

启动器 `DEFAULT_FEEDBACK_URL` 必须指向上报口，不要填看板端口。

第一次打开启动器会弹窗，用户手动点「同意」后才会上传。
