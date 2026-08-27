# -*- coding: utf-8 -*-
"""临时探针：验证反馈客户端的脱敏上传 / 离线待发队列 / 限时退出心跳。"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="pymcl_fb_probe_"))
os.environ["PYMCL_HOME"] = str(TMP)
sys.path.insert(0, str(Path(__file__).resolve().parent))

from feedback_hub.server import STORE, make_ingest_httpd  # noqa: E402
from mclauncher import feedback as fb  # noqa: E402


def check(name, ok, extra=""):
    print(("  PASS  " if ok else "  FAIL  ") + name, extra)
    if not ok:
        raise SystemExit(1)


fb.set_consent(True)

# 1. 断网：提交进入待发队列，异常类型可区分
os.environ["PYMCL_FEEDBACK_URL"] = "http://127.0.0.1:1"  # 必然拒绝
try:
    fb.submit("bug", "断网测试", "body with accessToken: SECRET_TOKEN_ABC123")
    check("断网应抛异常", False)
except fb.FeedbackNetworkError as exc:
    check("断网抛 FeedbackNetworkError", "待发队列" in str(exc))
except fb.FeedbackError:
    check("断网异常类型", False)

check("待发队列有 1 条", fb.pending_count() == 1)
rows = json.loads(fb.PENDING_FILE.read_text(encoding="utf-8"))
queued_body = rows[0]["payload"]["body"]
check("队列内容已脱敏", "SECRET_TOKEN_ABC123" not in queued_body and "***" in queued_body,
      repr(queued_body))

# 2. 联网：flush_pending 补发成功并清空队列
ingest = make_ingest_httpd("127.0.0.1", 0)
threading.Thread(target=ingest.serve_forever, daemon=True).start()
os.environ["PYMCL_FEEDBACK_URL"] = "http://%s:%s" % ingest.server_address
sent = fb.flush_pending()
check("补发 1 条", sent == 1)
check("队列清空", fb.pending_count() == 0)
stored = list(STORE.feedback)[-1]
check("服务端存储已脱敏", "SECRET_TOKEN_ABC123" not in (stored.get("body") or ""))

# 3. submit_crash 附带脱敏日志尾部
report = {
    "headline": "内存不足",
    "summary": "OOM",
    "detail": "分析结论文本",
    "output_tail": "--accessToken supersecret123 --uuid deadbeef\njava.lang.OutOfMemoryError",
    "log_mc": "latest tail",
    "log_crash": "crash tail",
    "log_hs": "",
    "exit_code": 1,
    "exit_hint": "退出码 1：一般错误",
}
data = fb.submit_crash(report)
check("crash 提交成功", bool(data.get("id")))
stored = list(STORE.feedback)[-1]
crash = stored.get("crash") or {}
check("crash 带日志尾部", "OutOfMemoryError" in (crash.get("output_tail") or ""))
check("crash 日志已脱敏", "supersecret123" not in json.dumps(crash, ensure_ascii=False)
      and "deadbeef" not in json.dumps(crash, ensure_ascii=False),
      repr(crash.get("output_tail")))
check("crash 带退出码", crash.get("exit_code") == 1)

# 4. stop_heartbeat 限时：URL 指向黑洞地址也必须在 ~3s 内返回
fb.start_heartbeat(interval=999)
os.environ["PYMCL_FEEDBACK_URL"] = "http://10.255.255.1:9"  # 不可达，连接会挂起
t0 = time.time()
fb.stop_heartbeat(send_offline=True, wait=2.0)
dt = time.time() - t0
check("退出心跳限时返回", dt < 3.5, f"{dt:.2f}s")

ingest.shutdown()
print("ALL PASS")
