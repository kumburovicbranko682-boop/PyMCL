# -*- coding: utf-8 -*-
"""PyMCL 反馈中心。接收用户反馈与电脑配置心跳，SSE 实时推到开发者看板。

  copy .env.example .env
  python -m feedback_hub
  浏览器打开 http://127.0.0.1:8788
"""

from __future__ import annotations

import json
import os
import queue
import secrets
import sys
import threading
import time
import uuid
from collections import defaultdict, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
STATIC_DASH = ROOT / "dashboard.html"


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


def _ensure_admin_token() -> str:
    """看板必须有令牌。环境变量没配就自动生成并持久化到 data/admin_token.txt。

    只影响开发者看板端口；启动器上报口（ingest）从不需要令牌，
    旧客户端协议不受影响。显式设 NO_ADMIN_TOKEN=1 才允许无鉴权看板。
    """
    if (os.environ.get("NO_ADMIN_TOKEN") or "").strip() in ("1", "true", "yes"):
        return ""
    tok = (os.environ.get("ADMIN_TOKEN") or "").strip()
    if tok:
        return tok
    path = DATA_DIR / "admin_token.txt"
    try:
        if path.is_file():
            tok = path.read_text(encoding="utf-8").strip()
            if tok:
                return tok
        tok = secrets.token_urlsafe(24)
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(tok + "\n", encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return tok
    except OSError:
        # 磁盘只读等极端情况：本次进程用临时令牌，仍然强制鉴权
        return secrets.token_urlsafe(24)


BIND = os.environ.get("BIND", "0.0.0.0")
INGEST_BIND = os.environ.get("INGEST_BIND") or BIND
UI_BIND = os.environ.get("UI_BIND") or BIND
INGEST_PORT = int(os.environ.get("INGEST_PORT") or os.environ.get("PORT") or "18788")
UI_PORT = int(os.environ.get("UI_PORT") or "18789")
PORT = INGEST_PORT
ADMIN_TOKEN = _ensure_admin_token()
RATE_PER_MIN = int(os.environ.get("RATE_PER_MIN", "20") or 20)
RATE_PER_DAY = int(os.environ.get("RATE_PER_DAY", "200") or 200)
MAX_BODY = int(os.environ.get("MAX_BODY", str(512 * 1024)) or 512 * 1024)
MACHINE_TTL = int(os.environ.get("MACHINE_TTL", "90") or 90)
KEEP_FEEDBACK = int(os.environ.get("KEEP_FEEDBACK", "5000") or 5000)
MACHINE_KEEP_DAYS = int(os.environ.get("MACHINE_KEEP_DAYS", "30") or 30)
LOG_MAX_MB = int(os.environ.get("LOG_MAX_MB", "20") or 20)
VERBOSE = (os.environ.get("VERBOSE") or "").strip() in ("1", "true", "yes")
HOUSEKEEP_SEC = int(os.environ.get("HOUSEKEEP_SEC", "3600") or 3600)
CATEGORIES = {
    "bug", "crash", "download", "multiplayer", "ai", "ui", "suggest", "other",
}


class _Limiter:
    def __init__(self):
        self._lock = threading.Lock()
        self.minu = defaultdict(list)
        self.day = defaultdict(list)

    def allow(self, ip: str):
        now = time.time()
        with self._lock:
            self.minu[ip] = [t for t in self.minu[ip] if now - t < 60]
            self.day[ip] = [t for t in self.day[ip] if now - t < 86400]
            if len(self.minu[ip]) >= RATE_PER_MIN:
                return False
            if len(self.day[ip]) >= RATE_PER_DAY:
                return False
            self.minu[ip].append(now)
            self.day[ip].append(now)
            return True

    def prune(self):
        """丢掉超过 24h 没动静的 IP，键集合才不会只增不减。"""
        now = time.time()
        with self._lock:
            for table, ttl in ((self.minu, 60), (self.day, 86400)):
                dead = []
                for ip, stamps in table.items():
                    alive = [t for t in stamps if now - t < ttl]
                    if alive:
                        table[ip] = alive
                    else:
                        dead.append(ip)
                for ip in dead:
                    del table[ip]


class Hub:
    def __init__(self):
        self._lock = threading.Lock()
        self._subs = []

    def subscribe(self):
        q = queue.Queue(maxsize=200)
        with self._lock:
            self._subs.append(q)
        return q

    def unsubscribe(self, q):
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)

    def publish(self, event: str, data):
        raw = json.dumps({"event": event, "data": data}, ensure_ascii=False)
        dead = []
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(raw)
            except queue.Full:
                dead.append(q)
        for q in dead:
            self.unsubscribe(q)


class Store:
    def __init__(self, root: Path):
        self.root = root
        self.fb_file = root / "feedback.jsonl"
        self.mach_dir = root / "machines"
        self._lock = threading.Lock()
        self.feedback = deque(maxlen=max(100, KEEP_FEEDBACK))
        self.index = {}
        self.machines = {}
        self._load()

    def _load(self):
        self.root.mkdir(parents=True, exist_ok=True)
        self.mach_dir.mkdir(parents=True, exist_ok=True)
        if self.fb_file.is_file():
            try:
                lines = self.fb_file.read_text(encoding="utf-8").splitlines()
            except OSError:
                lines = []
            for line in lines[-KEEP_FEEDBACK:]:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if isinstance(row, dict) and row.get("id"):
                    self.feedback.append(row)
                    self.index[row["id"]] = row
        for path in self.mach_dir.glob("*.json"):
            try:
                row = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if isinstance(row, dict) and row.get("device_id"):
                self.machines[row["device_id"]] = row

    def add_feedback(self, row: dict) -> dict:
        with self._lock:
            self.feedback.append(row)
            self.index[row["id"]] = row
            with self.fb_file.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        return row

    def list_feedback(self, limit=80, category="", q=""):
        cat = (category or "").strip().lower()
        needle = (q or "").strip().lower()
        with self._lock:
            rows = list(self.feedback)
        rows.reverse()
        out = []
        for row in rows:
            if cat and row.get("category") != cat:
                continue
            if needle:
                blob = " ".join([
                    str(row.get("title") or ""),
                    str(row.get("body") or ""),
                    str(row.get("contact") or ""),
                    str(row.get("device_id") or ""),
                    str(((row.get("sysinfo") or {}).get("summary") or "")),
                ]).lower()
                if needle not in blob:
                    continue
            out.append(_public_feedback(row, brief=True))
            if len(out) >= limit:
                break
        return out

    def get_feedback(self, fid: str):
        with self._lock:
            return self.index.get(fid)

    def upsert_machine(self, row: dict) -> dict:
        did = row["device_id"]
        with self._lock:
            old = self.machines.get(did) or {}
            if not row.get("sysinfo") and old.get("sysinfo"):
                row["sysinfo"] = old.get("sysinfo")
            if not row.get("summary"):
                row["summary"] = ((row.get("sysinfo") or {}).get("summary")
                                  or old.get("summary") or "")
            self.machines[did] = row
            path = self.mach_dir / f"{did}.json"
            path.write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding="utf-8")
        return row

    def list_machines(self, include_offline=True):
        now = time.time()
        with self._lock:
            rows = list(self.machines.values())
        live = []
        for row in rows:
            last = float(row.get("last_seen") or 0)
            online = (row.get("status") != "offline") and (now - last <= MACHINE_TTL)
            item = dict(row)
            item["online"] = online
            item["ago_sec"] = int(max(0, now - last)) if last else None
            if (not include_offline) and (not online):
                continue
            live.append(_public_machine(item))
        live.sort(key=lambda r: (not r.get("online"), -(r.get("last_seen") or 0)))
        return live

    def snapshot(self):
        return {
            "feedback": self.list_feedback(limit=80),
            "machines": self.list_machines(include_offline=True),
            "server_time": time.time(),
        }

    def compact_feedback(self):
        """feedback.jsonl 只追加不清理会无限膨胀；超过 2 倍保留量时
        用内存里的 deque（恰好是最近 KEEP_FEEDBACK 条）重写文件。"""
        with self._lock:
            try:
                if not self.fb_file.is_file():
                    return False
                with self.fb_file.open("r", encoding="utf-8") as fh:
                    lines = sum(1 for _ in fh)
                if lines <= max(200, 2 * self.feedback.maxlen):
                    return False
                tmp = self.fb_file.with_suffix(".jsonl.tmp")
                with tmp.open("w", encoding="utf-8") as fh:
                    for row in self.feedback:
                        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                os.replace(tmp, self.fb_file)
                return True
            except OSError:
                return False

    def cleanup_machines(self, keep_days: int):
        """删掉太久没心跳的机器（内存 + 磁盘 json），离线设备不会永久堆积。"""
        if keep_days <= 0:
            return 0
        cutoff = time.time() - keep_days * 86400
        removed = 0
        with self._lock:
            for did in list(self.machines):
                row = self.machines[did] or {}
                last = float(row.get("last_seen") or 0)
                if last and last >= cutoff:
                    continue
                del self.machines[did]
                removed += 1
                try:
                    (self.mach_dir / f"{did}.json").unlink(missing_ok=True)
                except OSError:
                    pass
        return removed


def _public_feedback(row: dict, brief=False) -> dict:
    info = row.get("sysinfo") or {}
    out = {
        "id": row.get("id"),
        "ts": row.get("ts"),
        "iso": row.get("iso"),
        "category": row.get("category"),
        "title": row.get("title"),
        "contact": row.get("contact") or "",
        "device_id": row.get("device_id"),
        "client_ip": row.get("client_ip"),
        "app_version": row.get("app_version"),
        "summary": info.get("summary") or row.get("summary") or "",
        "hostname": info.get("hostname") or "",
    }
    if brief:
        body = str(row.get("body") or "")
        out["body_preview"] = body[:180]
        return out
    out["body"] = row.get("body") or ""
    out["sysinfo"] = info
    out["crash"] = row.get("crash")
    return out


def _public_machine(row: dict) -> dict:
    info = row.get("sysinfo") or {}
    return {
        "device_id": row.get("device_id"),
        "status": row.get("status") or "online",
        "online": bool(row.get("online")),
        "last_seen": row.get("last_seen"),
        "ago_sec": row.get("ago_sec"),
        "client_ip": row.get("client_ip"),
        "app_version": row.get("app_version"),
        "hostname": info.get("hostname") or row.get("hostname") or "",
        "summary": info.get("summary") or row.get("summary") or "",
        "sysinfo": info,
    }


def _clip_sysinfo(info):
    if not isinstance(info, dict):
        return {}
    raw = json.dumps(info, ensure_ascii=False)
    if len(raw.encode("utf-8")) <= 200_000:
        return info
    keep = dict(info)
    keep.pop("instances", None)
    keep["instances"] = []
    keep["truncated"] = True
    return keep


def _clip_crash(crash):
    """新版客户端的 crash 会附带日志尾部；逐字段截断，防止恶意超大字段。"""
    if not isinstance(crash, dict):
        return None
    out = {}
    for key, value in crash.items():
        if isinstance(value, str) and len(value) > 20_000:
            out[str(key)[:64]] = value[-20_000:]
        else:
            out[str(key)[:64]] = value
    return out


STORE = Store(DATA_DIR)
HUB = Hub()
LIMITER = _Limiter()


def _json_bytes(obj, code=200):
    return code, json.dumps(obj, ensure_ascii=False).encode("utf-8")


def _client_ip(handler: BaseHTTPRequestHandler) -> str:
    forwarded = (handler.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
    return forwarded or handler.client_address[0]


class _BaseHandler(BaseHTTPRequestHandler):
    server_version = "PyMCL-Feedback/1.0"
    protocol_version = "HTTP/1.1"
    role = "base"

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def log_request(self, code="-", size="-"):
        # 心跳 30 秒一拍，全量请求日志会把 hub.log 灌爆；
        # 默认只记错误响应，VERBOSE=1 恢复全量。
        try:
            numeric = int(str(code).split(" ")[0])
        except ValueError:
            numeric = 0
        if VERBOSE or numeric >= 400:
            super().log_request(code, size)

    def _cors(self):
        # 上报口的客户端是启动器（requests），不是浏览器，给通配 CORS 无害；
        # 看板口承载全部反馈数据，绝不反射任意 Origin —— 否则任何网页都能
        # 从开发者浏览器里跨域读走整个看板。看板是同源页面，不需要 CORS。
        if self.role == "ingest":
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type, X-PyMCL-Client")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def _send(self, code, body: bytes, content_type="application/json; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _err(self, code, msg):
        self._send(*_json_bytes({"ok": False, "error": msg}, code))

    def _query(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        return parsed.path, {k: (v[-1] if v else "") for k, v in qs.items()}

    def _admin_ok(self, qs=None):
        if not ADMIN_TOKEN:
            return True
        qs = qs or {}
        auth = self.headers.get("Authorization") or ""
        token = ""
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()
        token = token or qs.get("token") or self.headers.get("X-Admin-Token") or ""
        return token == ADMIN_TOKEN

    def _need_client(self):
        client = self.headers.get("X-PyMCL-Client") or ""
        return client.startswith("PyMCL/")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()


class UIHandler(_BaseHandler):
    """开发者看板，不接收用户上报。"""
    server_version = "PyMCL-FeedbackUI/1.0"
    role = "ui"

    def do_GET(self):
        path, qs = self._query()
        if path in ("/health", "/api/health"):
            self._send(*_json_bytes({
                "ok": True,
                "service": "pymcl-feedback-ui",
                "role": "ui",
                "feedback": len(STORE.feedback),
                "machines": len(STORE.machines),
            }))
            return
        if path in ("/", "/dashboard", "/dashboard.html"):
            self._serve_dashboard()
            return
        if not self._admin_ok(qs):
            self._err(401, "需要管理令牌")
            return
        if path == "/api/v1/snapshot":
            self._send(*_json_bytes({"ok": True, **STORE.snapshot()}))
            return
        if path == "/api/v1/feedback":
            try:
                limit = min(300, max(1, int(qs.get("limit") or 80)))
            except ValueError:
                limit = 80
            rows = STORE.list_feedback(
                limit=limit, category=qs.get("category") or "", q=qs.get("q") or "")
            self._send(*_json_bytes({"ok": True, "items": rows}))
            return
        if path.startswith("/api/v1/feedback/"):
            fid = path.rsplit("/", 1)[-1]
            row = STORE.get_feedback(fid)
            if not row:
                self._err(404, "找不到这条反馈")
                return
            self._send(*_json_bytes({"ok": True, "item": _public_feedback(row, brief=False)}))
            return
        if path == "/api/v1/machines":
            self._send(*_json_bytes({
                "ok": True,
                "items": STORE.list_machines(include_offline=qs.get("all") != "0"),
            }))
            return
        if path == "/api/v1/stream":
            self._sse()
            return
        self._err(404, "not found")

    def do_POST(self):
        self._err(404, "this port is ui-only")

    def _read_json(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        if n > MAX_BODY:
            self._err(413, "请求太大")
            return None
        if n <= 0:
            self._err(400, "请求格式不对")
            return None
        raw = self.rfile.read(n)
        try:
            body = json.loads(raw.decode("utf-8"))
        except Exception:
            self._err(400, "请求格式不对")
            return None
        if not isinstance(body, dict):
            self._err(400, "请求格式不对")
            return None
        return body

    def _ingest_feedback(self):
        if not self._need_client():
            self._err(403, "缺少客户端标识")
            return
        ip = _client_ip(self)
        if not LIMITER.allow(ip):
            self._err(429, "提交太频繁")
            return
        body = self._read_json()
        if body is None:
            return
        cat = str(body.get("category") or "other").strip().lower()
        if cat not in CATEGORIES:
            cat = "other"
        title = str(body.get("title") or "").strip()[:120]
        text = str(body.get("body") or "").strip()[:16000]
        if not title and not text:
            self._err(400, "请填写标题或内容")
            return
        if not title:
            title = text.splitlines()[0][:80]
        did = str(body.get("device_id") or "").strip()[:64] or uuid.uuid4().hex
        info = _clip_sysinfo(body.get("sysinfo"))
        now = time.time()
        row = {
            "id": "fb_" + uuid.uuid4().hex[:16],
            "ts": now,
            "iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now)),
            "category": cat,
            "title": title,
            "body": text,
            "contact": str(body.get("contact") or "").strip()[:120],
            "device_id": did,
            "client_ip": ip,
            "app_version": str(body.get("app_version") or "")[:32],
            "sysinfo": info,
            "crash": _clip_crash(body.get("crash")),
            "summary": info.get("summary") or "",
        }
        STORE.add_feedback(row)
        HUB.publish("feedback", _public_feedback(row, brief=True))
        self._send(*_json_bytes({"ok": True, "id": row["id"]}))

    def _ingest_heartbeat(self):
        if not self._need_client():
            self._err(403, "缺少客户端标识")
            return
        ip = _client_ip(self)
        if not LIMITER.allow(ip):
            self._err(429, "心跳太频繁")
            return
        body = self._read_json()
        if body is None:
            return
        did = str(body.get("device_id") or "").strip()[:64]
        if not did:
            self._err(400, "缺少 device_id")
            return
        status = str(body.get("status") or "online").strip().lower()
        if status not in ("online", "offline"):
            status = "online"
        info = _clip_sysinfo(body.get("sysinfo"))
        now = time.time()
        row = {
            "device_id": did,
            "status": status,
            "last_seen": now,
            "client_ip": ip,
            "app_version": str(body.get("app_version") or "")[:32],
            "sysinfo": info,
            "hostname": info.get("hostname") or "",
            "summary": info.get("summary") or "",
        }
        stored = STORE.upsert_machine(row)
        stored["online"] = status == "online"
        stored["ago_sec"] = 0
        HUB.publish("heartbeat", _public_machine(stored))
        self._send(*_json_bytes({"ok": True}))

    def _sse(self):
        q = HUB.subscribe()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self._cors()
        self.end_headers()
        try:
            snap = json.dumps({"event": "snapshot", "data": STORE.snapshot()}, ensure_ascii=False)
            self.wfile.write(f"event: snapshot\ndata: {snap}\n\n".encode("utf-8"))
            self.wfile.flush()
            while True:
                try:
                    item = q.get(timeout=15)
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    continue
                try:
                    obj = json.loads(item)
                    ev = obj.get("event") or "message"
                    payload = json.dumps(obj, ensure_ascii=False)
                except Exception:
                    ev = "message"
                    payload = item
                self.wfile.write(f"event: {ev}\ndata: {payload}\n\n".encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass
        finally:
            HUB.unsubscribe(q)

    def _serve_dashboard(self):
        if not STATIC_DASH.is_file():
            self._err(500, "缺少 dashboard.html")
            return
        html = STATIC_DASH.read_bytes()
        self._send(200, html, "text/html; charset=utf-8")


class IngestHandler(UIHandler):
    """只收启动器上报，不提供看板。给内网穿透用。"""
    server_version = "PyMCL-Ingest/1.0"
    role = "ingest"

    def do_GET(self):
        path, _qs = self._query()
        if path in ("/health", "/api/health"):
            self._send(*_json_bytes({
                "ok": True,
                "service": "pymcl-ingest",
                "role": "ingest",
            }))
            return
        self._err(404, "this port is ingest-only")

    def do_POST(self):
        path, _qs = self._query()
        if path == "/api/v1/feedback":
            self._ingest_feedback()
            return
        if path == "/api/v1/heartbeat":
            self._ingest_heartbeat()
            return
        self._err(404, "this port is ingest-only")


def make_ingest_httpd(bind=None, port=None):
    return ThreadingHTTPServer(
        (bind or INGEST_BIND, int(port if port is not None else INGEST_PORT)),
        IngestHandler,
    )


def make_ui_httpd(bind=None, port=None):
    return ThreadingHTTPServer(
        (bind or UI_BIND, int(port if port is not None else UI_PORT)),
        UIHandler,
    )


def make_httpd(bind=None, port=None):
    """兼容旧测试：默认起上报口。"""
    return make_ingest_httpd(bind, port)


def _rotate_hub_log():
    """start.sh 用 nohup >> 追加写 hub.log；超限时留尾部到 .1 再截断。
    O_APPEND 写入在 truncate 后自动回到文件头，进程不用重启。"""
    log_path = DATA_DIR / "hub.log"
    try:
        if not log_path.is_file() or log_path.stat().st_size <= LOG_MAX_MB * 1024 * 1024:
            return False
        with log_path.open("rb") as fh:
            fh.seek(-min(512 * 1024, log_path.stat().st_size), 2)
            tail = fh.read()
        (DATA_DIR / "hub.log.1").write_bytes(tail)
        with log_path.open("r+b") as fh:
            fh.truncate(0)
        return True
    except OSError:
        return False


def _housekeeping_loop(stop: threading.Event):
    while not stop.wait(HOUSEKEEP_SEC):
        try:
            compacted = STORE.compact_feedback()
            removed = STORE.cleanup_machines(MACHINE_KEEP_DAYS)
            LIMITER.prune()
            rotated = _rotate_hub_log()
            if compacted or removed or rotated:
                sys.stderr.write(
                    "[housekeeping] compact=%s machines_removed=%s log_rotated=%s\n"
                    % (compacted, removed, rotated))
        except Exception as exc:  # noqa: BLE001 清理失败不能拖垮服务
            sys.stderr.write("[housekeeping] error: %s\n" % exc)


def start_housekeeping() -> threading.Event:
    stop = threading.Event()
    threading.Thread(
        target=_housekeeping_loop, args=(stop,),
        name="pymcl-housekeeping", daemon=True,
    ).start()
    return stop


def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ingest = make_ingest_httpd()
    ui = make_ui_httpd()
    worker = threading.Thread(target=ingest.serve_forever, name="pymcl-ingest", daemon=True)
    worker.start()
    start_housekeeping()
    ingest_host = INGEST_BIND if INGEST_BIND != "0.0.0.0" else "127.0.0.1"
    ui_host = UI_BIND if UI_BIND != "0.0.0.0" else "127.0.0.1"
    token_hint = "off (NO_ADMIN_TOKEN=1)"
    if ADMIN_TOKEN:
        token_hint = "on"
        if not (os.environ.get("ADMIN_TOKEN") or "").strip():
            token_hint = "on (auto, see data/admin_token.txt)"
    sys.stderr.write(
        "PyMCL 上报口 http://%s:%s  (POST /api/v1/feedback|/heartbeat)\n"
        "PyMCL 看板   http://%s:%s  (WebUI, TTL=%ss, admin=%s)\n"
        % (ingest_host, INGEST_PORT, ui_host, UI_PORT, MACHINE_TTL, token_hint)
    )
    try:
        ui.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        ingest.shutdown()
        ui.server_close()
        ingest.server_close()


if __name__ == "__main__":
    main()
