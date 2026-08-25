# -*- coding: utf-8 -*-
"""Loopback-only HTTP JSON-RPC + SSE bridge for the PyMCL local UI."""

from __future__ import annotations

import argparse
import hmac
import inspect
import ipaddress
import json
import os
import queue
import secrets
import sys
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


TOKEN_HEADER = "X-PyMCL-Bridge-Token"
MAX_REQUEST_BYTES = 4 * 1024 * 1024
LOOPBACK_HOST = "127.0.0.1"


def _prepare_root(root: Path):
    root = root.resolve()
    os.environ["PYMCL_HOME"] = str(root)
    os.chdir(root)
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    return root


def _json_default(obj):
    if isinstance(obj, Path):
        return str(obj)
    raise TypeError(type(obj).__name__)


def _is_loopback_address(address: str) -> bool:
    try:
        return ipaddress.ip_address(address.split("%", 1)[0]).is_loopback
    except ValueError:
        return False


def _validate_bind_host(host: str) -> str:
    # Do not resolve host names here. A name such as ``localhost`` can be
    # changed outside this process, while this fixed literal is unambiguous.
    if host != LOOPBACK_HOST:
        raise ValueError(f"bridge may only bind to {LOOPBACK_HOST}")
    return host


def _normalize_origin(origin: str) -> str:
    parsed = urlparse(origin)
    if (
        parsed.scheme != "http"
        or parsed.hostname != LOOPBACK_HOST
        or parsed.username
        or parsed.password
        or parsed.path not in ("", "/")
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(f"allowed origin must be a {LOOPBACK_HOST} HTTP origin: {origin!r}")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"invalid allowed origin port: {origin!r}") from exc
    if port is not None and not 1 <= port <= 65535:
        raise ValueError(f"invalid allowed origin port: {origin!r}")
    return f"http://{LOOPBACK_HOST}" + (f":{port}" if port is not None else "")


class BridgeState:
    def __init__(self, api, bus, *, token: str | None = None, allowed_origins=()):
        token = token or secrets.token_urlsafe(32)
        if not isinstance(token, str) or len(token) < 32:
            raise ValueError("bridge token must contain at least 32 characters")
        self.api = api
        self.bus = bus
        self.token = token
        self.allowed_origins = frozenset(_normalize_origin(origin) for origin in allowed_origins)


class LoopbackThreadingHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def verify_request(self, request, client_address):
        return _is_loopback_address(client_address[0])


def create_http_server(host: str, port: int, state: BridgeState) -> LoopbackThreadingHTTPServer:
    """Create a bridge server that can never listen on a network interface."""
    return LoopbackThreadingHTTPServer((_validate_bind_host(host), port), make_handler(state))


def make_handler(state: BridgeState):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            # The SSE token is carried in a query parameter. Never put the raw
            # request target into logs, because BaseHTTPRequestHandler would.
            sys.stderr.write(f"[bridge] {self.command} {urlparse(self.path).path}\n")

        def _send(self, code: int, body: bytes, content_type: str, *, origin: str | None = None, headers=None):
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            if origin:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            if body:
                self.wfile.write(body)

        def _send_json(self, code: int, obj, *, origin: str | None = None):
            raw = json.dumps(obj, ensure_ascii=False, default=_json_default).encode("utf-8")
            self._send(code, raw, "application/json; charset=utf-8", origin=origin)

        def _request_origin(self) -> tuple[bool, str | None]:
            if not _is_loopback_address(self.client_address[0]):
                self._send_json(403, {"error": "loopback clients only"})
                return False, None
            origin = self.headers.get("Origin")
            if origin is None:
                return True, None
            if origin not in state.allowed_origins:
                self._send_json(403, {"error": "origin not allowed"})
                return False, None
            return True, origin

        def _authorize(self, *, allow_sse_query: bool = False) -> tuple[bool, str | None]:
            permitted, origin = self._request_origin()
            if not permitted:
                return False, None

            supplied = self.headers.get(TOKEN_HEADER, "")
            if not supplied and allow_sse_query:
                values = parse_qs(urlparse(self.path).query, keep_blank_values=True).get("token", [])
                supplied = values[0] if len(values) == 1 else ""
            if not supplied or not hmac.compare_digest(supplied, state.token):
                self._send_json(401, {"error": "authentication required"}, origin=origin)
                return False, None
            return True, origin

        def do_OPTIONS(self):
            path = urlparse(self.path).path
            if path not in ("/rpc", "/events", "/health", "/"):
                self._send_json(404, {"error": "not found"})
                return
            permitted, origin = self._request_origin()
            # A browser preflight cannot carry the application token. It is
            # safe to answer only for the explicitly configured local UI.
            if not permitted or origin is None:
                if permitted:
                    self._send_json(403, {"error": "origin required"})
                return
            self._send(
                204,
                b"",
                "text/plain; charset=utf-8",
                origin=origin,
                headers={
                    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                    "Access-Control-Allow-Headers": f"Content-Type, {TOKEN_HEADER}",
                    "Access-Control-Max-Age": "600",
                },
            )

        def do_GET(self):
            path = urlparse(self.path).path
            if path in ("/health", "/"):
                authorized, origin = self._authorize()
                if authorized:
                    self._send_json(200, {"ok": True, "name": "pymcl-bridge"}, origin=origin)
                return
            if path == "/events":
                authorized, origin = self._authorize(allow_sse_query=True)
                if authorized:
                    self._sse(origin)
                return
            permitted, origin = self._request_origin()
            if permitted:
                self._send_json(404, {"error": "not found"}, origin=origin)

        def do_POST(self):
            path = urlparse(self.path).path
            if path not in ("/rpc", "/"):
                permitted, origin = self._request_origin()
                if permitted:
                    self._send_json(404, {"error": "not found"}, origin=origin)
                return
            authorized, origin = self._authorize()
            if not authorized:
                return
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                self._send_json(400, {"error": "invalid content length"}, origin=origin)
                return
            if n < 0 or n > MAX_REQUEST_BYTES:
                self._send_json(413, {"error": "request body too large"}, origin=origin)
                return
            raw = self.rfile.read(n) if n > 0 else b"{}"
            try:
                req = json.loads(raw.decode("utf-8") or "{}")
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                self._send_json(400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(exc)}}, origin=origin)
                return
            if not isinstance(req, dict):
                self._send_json(400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "request must be an object"}}, origin=origin)
                return
            rid = req.get("id")
            method = req.get("method")
            params = req.get("params") or {}
            if not method or not isinstance(method, str):
                self._send_json(400, {"jsonrpc": "2.0", "id": rid, "error": {"code": -32600, "message": "method required"}}, origin=origin)
                return
            if method.startswith("_"):
                self._send_json(200, {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "hidden method"}}, origin=origin)
                return
            fn = getattr(state.api, method, None)
            if not callable(fn):
                self._send_json(200, {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"unknown method: {method}"}}, origin=origin)
                return
            try:
                if isinstance(params, list):
                    result = fn(*params)
                elif isinstance(params, dict):
                    result = _call_kwargs(fn, params)
                else:
                    raise TypeError("params must be object or array")
            except Exception as exc:  # noqa: BLE001
                self._send_json(200, {"jsonrpc": "2.0", "id": rid, "error": {"code": -32000, "message": str(exc)}}, origin=origin)
                return
            self._send_json(200, {"jsonrpc": "2.0", "id": rid, "result": result}, origin=origin)

        def _sse(self, origin: str | None):
            q = state.bus.subscribe()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-store")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            if origin:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
            self.end_headers()
            try:
                hello = json.dumps({"ok": True}, ensure_ascii=False)
                self.wfile.write(f"event: hello\ndata: {hello}\n\n".encode("utf-8"))
                self.wfile.flush()
                while True:
                    try:
                        payload = q.get(timeout=15)
                    except queue.Empty:
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
                        continue
                    ev = payload.get("event") or "message"
                    data = json.dumps(payload.get("data") or {}, ensure_ascii=False, default=_json_default)
                    self.wfile.write(f"event: {ev}\ndata: {data}\n\n".encode("utf-8"))
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                pass
            finally:
                state.bus.unsubscribe(q)

    return Handler


def _call_kwargs(fn, params: dict):
    sig = inspect.signature(fn)
    accepted = {}
    for name, p in sig.parameters.items():
        if name == "self":
            continue
        if name in params:
            accepted[name] = params[name]
        elif p.kind == inspect.Parameter.VAR_KEYWORD:
            accepted.update({k: v for k, v in params.items() if k not in accepted})
    return fn(**accepted)


def _write_ready_file(path: Path, *, host: str, port: int, token: str):
    """Atomically publish connection details for a parent launcher process."""
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        if os.name != "nt":
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"rpc_url": f"http://{host}:{port}", "token": token}, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def main(argv=None):
    parser = argparse.ArgumentParser(description="PyMCL loopback JSON-RPC bridge")
    parser.add_argument("--root", required=True, help="启动器根目录，与 Qt 版共用 .minecraft/java/config.json")
    parser.add_argument("--host", default=LOOPBACK_HOST, help=f"仅支持 {LOOPBACK_HOST}")
    parser.add_argument("--port", type=int, default=0, help="0 = 自动分配")
    parser.add_argument("--token", help="当前启动唯一的 Bridge 令牌；优先于 PYMCL_BRIDGE_TOKEN")
    parser.add_argument("--allowed-origin", action="append", default=[], help="允许访问 Bridge 的本机 UI Origin，可重复")
    parser.add_argument("--ready-file", type=Path, help="原子写入 {rpc_url, token} 的就绪文件")
    args = parser.parse_args(argv)

    try:
        host = _validate_bind_host(args.host)
    except ValueError as exc:
        parser.error(str(exc))
    if not 0 <= args.port <= 65535:
        parser.error("port must be between 0 and 65535")

    root = _prepare_root(Path(args.root))
    from mclauncher.guard import install as install_guard
    install_guard(root / "pymcl-error.log")
    from bridge.api import BackendAPI, EventBus  # noqa: WPS433

    token = args.token or os.environ.get("PYMCL_BRIDGE_TOKEN") or secrets.token_urlsafe(32)
    bus = EventBus()
    api = BackendAPI(bus)
    try:
        state = BridgeState(api, bus, token=token, allowed_origins=args.allowed_origin)
    except ValueError as exc:
        parser.error(str(exc))
    httpd = create_http_server(host, args.port, state)
    _, port = httpd.server_address[:2]
    if args.ready_file:
        _write_ready_file(args.ready_file, host=host, port=port, token=state.token)
    banner = f"PYMCL_BRIDGE port={port} host={host} root={root} auth=token\n"
    sys.stdout.write(banner)
    sys.stdout.flush()
    sys.stderr.write(banner)
    # 与 Qt 主窗口一致：用户此前已同意上传诊断数据的话，桥进程也要拉起心跳，
    # 否则 WinUI/EziApp 用户看着设置里开着「定时上报」，实际上从来没跑过。
    from mclauncher import feedback as fb
    try:
        if fb.heartbeat_enabled():
            fb.start_heartbeat()
    except Exception:
        pass
    try:
        httpd.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        pass
    finally:
        fb.stop_heartbeat()
        httpd.server_close()
        if args.ready_file:
            try:
                args.ready_file.unlink()
            except FileNotFoundError:
                pass


if __name__ == "__main__":
    main()
