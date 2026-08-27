# -*- coding: utf-8 -*-
"""反馈系统自检：双端口隔离、采集、提交、快照、SSE。"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request

from mclauncher import sysinfo as sysinfo_mod
from mclauncher.feedback_defaults import CLIENT_HEADER
from feedback_hub.server import ADMIN_TOKEN, HUB, make_ingest_httpd, make_ui_httpd

TOKEN_QS = ("?token=" + ADMIN_TOKEN) if ADMIN_TOKEN else ""


def _req(url, data=None, headers=None, timeout=8):
    body = None if data is None else json.dumps(data, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers or {}, method="POST" if body else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            ctype = resp.headers.get("Content-Type") or ""
            if "json" in ctype or raw[:1] in (b"{", b"["):
                return resp.status, json.loads(raw.decode("utf-8"))
            return resp.status, raw
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except Exception:
            parsed = raw.decode("utf-8", errors="replace")
        return exc.code, parsed


def main():
    info = sysinfo_mod.collect(force=True, scan_system_java=False)
    assert isinstance(info, dict) and info.get("summary"), info
    print("[OK] sysinfo", info.get("summary"))

    ingest = make_ingest_httpd("127.0.0.1", 0)
    ui = make_ui_httpd("127.0.0.1", 0)
    threading.Thread(target=ingest.serve_forever, daemon=True).start()
    threading.Thread(target=ui.serve_forever, daemon=True).start()
    time.sleep(0.2)
    ingest_base = "http://%s:%s" % ingest.server_address
    ui_base = "http://%s:%s" % ui.server_address
    print("[OK] ingest", ingest_base)
    print("[OK] ui", ui_base)

    headers = {"Content-Type": "application/json", "X-PyMCL-Client": CLIENT_HEADER}

    code, page = _req(ingest_base + "/")
    assert code == 404, page
    print("[OK] ingest has no webui")

    code, posted = _req(ui_base + "/api/v1/feedback", {
        "device_id": "nope", "category": "bug", "title": "x", "body": "y",
    }, headers)
    assert code == 404, posted
    print("[OK] ui rejects upload")

    if ADMIN_TOKEN:
        code, denied = _req(ui_base + "/api/v1/snapshot")
        assert code == 401, denied
        print("[OK] ui api requires token")

    # 旧客户端兼容：最小报文（无 crash / 无新字段 / 旧版 UA 头）必须成功
    code, legacy = _req(ingest_base + "/api/v1/feedback", {
        "device_id": "legacy_device",
        "category": "bug",
        "title": "旧客户端报文",
        "body": "只有老字段",
        "contact": "",
        "app_version": "0.9.0",
        "sysinfo": {},
    }, {"Content-Type": "application/json", "X-PyMCL-Client": "PyMCL/0.9.0"})
    assert code == 200 and legacy.get("ok") and legacy.get("id"), legacy
    print("[OK] legacy client payload accepted", legacy.get("id"))

    # 新客户端：crash 附带日志尾部也要成功且被存储
    code, crashed = _req(ingest_base + "/api/v1/feedback", {
        "device_id": "test_device_verify",
        "category": "crash",
        "title": "崩溃带日志",
        "body": "分析结论",
        "app_version": "1.0.1",
        "crash": {
            "headline": "内存不足",
            "output_tail": "java.lang.OutOfMemoryError\n" * 20,
            "log_mc": "latest tail",
            "exit_code": 1,
        },
    }, headers)
    assert code == 200 and crashed.get("ok"), crashed
    print("[OK] crash payload with log tails", crashed.get("id"))

    code, hb = _req(ingest_base + "/api/v1/heartbeat", {
        "device_id": "test_device_verify",
        "status": "online",
        "app_version": "1.0.1",
        "sysinfo": info,
    }, headers)
    assert code == 200 and hb.get("ok"), hb
    print("[OK] heartbeat")

    code, fb = _req(ingest_base + "/api/v1/feedback", {
        "device_id": "test_device_verify",
        "category": "bug",
        "title": "验证反馈",
        "body": "自动化探测",
        "contact": "",
        "app_version": "1.0.1",
        "sysinfo": info,
    }, headers)
    assert code == 200 and fb.get("ok") and fb.get("id"), fb
    print("[OK] feedback", fb.get("id"))

    code, snap = _req(ui_base + "/api/v1/snapshot" + TOKEN_QS)
    assert code == 200 and snap.get("ok"), snap
    machines = snap.get("machines") or []
    items = snap.get("feedback") or []
    assert any(m.get("device_id") == "test_device_verify" for m in machines), machines
    assert any(x.get("id") == fb.get("id") for x in items), items
    crash_rows = [x for x in items if x.get("id") == crashed.get("id")]
    assert crash_rows, items
    code, detail = _req(ui_base + "/api/v1/feedback/" + crashed["id"] + TOKEN_QS)
    assert code == 200 and (detail.get("item") or {}).get("crash", {}).get("output_tail"), detail
    print("[OK] snapshot machines=%s feedback=%s crash_logs=stored" % (len(machines), len(items)))

    got = {"snapshot": False}

    def on_sse():
        req = urllib.request.Request(ui_base + "/api/v1/stream" + TOKEN_QS)
        with urllib.request.urlopen(req, timeout=8) as resp:
            start = time.time()
            chunks = []
            while time.time() - start < 6:
                line = resp.readline()
                if not line:
                    break
                chunks.append(line.decode("utf-8", errors="replace"))
                if "event: snapshot" in "".join(chunks):
                    got["snapshot"] = True
                    break

    t = threading.Thread(target=on_sse, daemon=True)
    t.start()
    time.sleep(0.3)
    HUB.publish("feedback", {"id": "sse_probe", "title": "sse"})
    t.join(timeout=7)
    assert got["snapshot"], got
    print("[OK] sse snapshot", got)

    ingest.shutdown()
    ui.shutdown()
    print("反馈系统自检通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
