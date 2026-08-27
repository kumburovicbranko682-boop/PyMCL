# -*- coding: utf-8 -*-
"""PyMCL 公益网关。

启动器只打这里，NewAPI 的 sk 只放在本机环境变量，不进 exe。
小白日常用量碰不到防刷阈值；对外文案永远是「网络繁忙」，不提额度。

  set NEWAPI_BASE_URL=https://your-newapi.example/v1
  set NEWAPI_API_KEY=sk-...
  set NEWAPI_MODEL=pymcl-assistant
  python ai_gateway/server.py
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent


def _load_env():
    p = ROOT / ".env"
    if not p.is_file():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()

NEWAPI_BASE = os.environ.get("NEWAPI_BASE_URL", "").rstrip("/")
NEWAPI_KEY = os.environ.get("NEWAPI_API_KEY", "")
NEWAPI_MODEL = os.environ.get("NEWAPI_MODEL", "pymcl-assistant")
DEGRADE_MODEL = os.environ.get("NEWAPI_DEGRADE_MODEL", "") or NEWAPI_MODEL
# 客户端可请求的模型白名单（逗号分隔）。默认模型永远在名单里。
# 用途：AI 助手的「深度诊断」档可以切到更强的模型，其余请求回落默认。
ALLOWED_MODELS = [
    m.strip() for m in os.environ.get("NEWAPI_ALLOWED_MODELS", "").split(",")
    if m.strip()
]
BIND = os.environ.get("BIND", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8787") or 8787)
RATE_PER_MIN = int(os.environ.get("RATE_PER_MIN", "40"))
RATE_PER_DAY = int(os.environ.get("RATE_PER_DAY", "800"))
MAX_INFLIGHT = int(os.environ.get("MAX_INFLIGHT", "4"))
MAX_BODY = int(os.environ.get("MAX_BODY", str(512 * 1024)))
# 单次回复 token 上限：诊断类长报告需要 6k+，默认放到 8192，可用环境变量再调
MAX_TOKENS_CAP = int(os.environ.get("MAX_TOKENS_CAP", "8192"))
DEGRADE_AFTER = max(8, RATE_PER_MIN // 2)


def pick_model(requested: str, degrade: bool = False) -> str:
    """按白名单选模型：降级优先；白名单里才放行请求的模型，否则回默认。"""
    if degrade:
        return DEGRADE_MODEL
    req = (requested or "").strip()
    if req and (req == NEWAPI_MODEL or req in ALLOWED_MODELS):
        return req
    return NEWAPI_MODEL


class _Limiter:
    def __init__(self):
        self._lock = threading.Lock()
        self.minu = defaultdict(list)
        self.day = defaultdict(list)
        self.inflight = defaultdict(int)

    def allow(self, ip: str):
        now = time.time()
        with self._lock:
            self.minu[ip] = [t for t in self.minu[ip] if now - t < 60]
            self.day[ip] = [t for t in self.day[ip] if now - t < 86400]
            if self.inflight[ip] >= MAX_INFLIGHT:
                return False, "busy"
            if len(self.minu[ip]) >= RATE_PER_MIN:
                return False, "busy"
            if len(self.day[ip]) >= RATE_PER_DAY:
                return False, "busy"
            degrade = len(self.minu[ip]) >= DEGRADE_AFTER
            self.minu[ip].append(now)
            self.day[ip].append(now)
            self.inflight[ip] += 1
            return True, ("degrade" if degrade else "ok")

    def done(self, ip: str):
        with self._lock:
            self.inflight[ip] = max(0, self.inflight[ip] - 1)


LIMITER = _Limiter()


def _json_bytes(obj, code=200):
    raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    return code, raw


class Handler(BaseHTTPRequestHandler):
    server_version = "PyMCL-AI-Gateway/1.0"

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _send(self, code, body: bytes, content_type="application/json; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _err(self, code, msg="网络繁忙，请稍后再试"):
        payload, _ = None, None
        raw = json.dumps({"error": {"message": msg}}, ensure_ascii=False).encode("utf-8")
        self._send(code, raw)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/health", "/pymcl/health"):
            self._send(200, json.dumps({
                "ok": True,
                "service": "pymcl-ai-gateway",
                "model": NEWAPI_MODEL,
                "models": sorted({NEWAPI_MODEL, *ALLOWED_MODELS}),
            }, ensure_ascii=False).encode("utf-8"))
            return
        self._err(404, "not found")

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path != "/pymcl/chat":
            self._err(404, "not found")
            return
        client = self.headers.get("X-PyMCL-Client") or ""
        if not client.startswith("PyMCL/"):
            self._err(403)
            return
        if not NEWAPI_BASE or not NEWAPI_KEY:
            self._err(503, "网关未配置 NewAPI")
            return
        ip = self.client_address[0]
        ok, flag = LIMITER.allow(ip)
        if not ok:
            self._err(429)
            return
        try:
            self._proxy(flag == "degrade")
        finally:
            LIMITER.done(ip)

    def _proxy(self, degrade: bool):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        if n <= 0 or n > MAX_BODY:
            self._err(413, "请求太大")
            return
        raw = self.rfile.read(n)
        try:
            body = json.loads(raw.decode("utf-8"))
        except Exception:
            self._err(400, "请求格式不对")
            return
        if not isinstance(body, dict):
            self._err(400, "请求格式不对")
            return
        messages = body.get("messages")
        if not isinstance(messages, list) or not messages:
            self._err(400, "请求格式不对")
            return
        want_stream = bool(body.get("stream", True))
        out = {
            "model": pick_model(str(body.get("model") or ""), degrade),
            "messages": messages,
            "temperature": min(float(body.get("temperature") or 0.3), 0.8),
            "stream": want_stream,
            "max_tokens": min(int(body.get("max_tokens") or 4096), MAX_TOKENS_CAP),
        }
        if body.get("tools"):
            out["tools"] = body["tools"]
            out["tool_choice"] = body.get("tool_choice") or "auto"
        target = NEWAPI_BASE + "/chat/completions"
        req = Request(
            target,
            data=json.dumps(out, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": "Bearer " + NEWAPI_KEY,
                "Content-Type": "application/json",
            },
        )
        try:
            resp = urlopen(req, timeout=180)
        except HTTPError as exc:
            err = exc.read()[:400]
            try:
                msg = json.loads(err.decode("utf-8", errors="replace"))
                text = ((msg.get("error") or {}).get("message") if isinstance(msg, dict) else None) or "上游繁忙"
            except Exception:
                text = "上游繁忙"
            self._err(502, str(text)[:200])
            return
        except URLError:
            self._err(502, "连不上上游")
            return
        if not want_stream:
            ctype = resp.headers.get("Content-Type") or "application/json; charset=utf-8"
            try:
                raw = resp.read()
            finally:
                try:
                    resp.close()
                except Exception:
                    pass
            self._send(200, raw, ctype)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        try:
            while True:
                chunk = resp.read(4096)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
        except Exception:
            pass
        finally:
            try:
                resp.close()
            except Exception:
                pass


def main():
    if not NEWAPI_BASE or not NEWAPI_KEY:
        sys.stderr.write(
            "请先设置 NEWAPI_BASE_URL 与 NEWAPI_API_KEY（或写 ai_gateway/.env）\n"
        )
        sys.exit(2)
    httpd = ThreadingHTTPServer((BIND, PORT), Handler)
    sys.stderr.write("PyMCL AI 网关 http://%s:%s  →  %s  model=%s\n" % (
        BIND, PORT, NEWAPI_BASE, NEWAPI_MODEL))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
