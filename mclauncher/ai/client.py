# -*- coding: utf-8 -*-
"""OpenAI 兼容客户端：公益网关 / 自定义 NewAPI。"""

from __future__ import annotations

import json
from typing import Iterator

import requests
from requests.exceptions import ReadTimeout, ChunkedEncodingError

from .defaults import (
    CLIENT_HEADER, DEFAULT_GATEWAY_URL, DEFAULT_MODEL, MAX_TOKENS,
    ONCE_TIMEOUT, STREAM_CONNECT_TIMEOUT, STREAM_READ_TIMEOUT,
)


class AIClientError(Exception):
    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = int(status or 0)

    def fatal(self) -> bool:
        if self.status in (401, 403, 429):
            return True
        msg = str(self)
        return any(key in msg for key in ("限制", "rate limit", "额度", "令牌无效", "unauthorized"))


class HttpCancel:
    """从 UI 线程关掉正在进行的 HTTP，让停止键真正生效。"""

    def __init__(self):
        self.flag = False
        self._resp = None
        self._sess = None

    def bind(self, session=None, resp=None):
        if session is not None:
            self._sess = session
        if resp is not None:
            self._resp = resp
        if self.flag:
            self.abort()

    def abort(self):
        self.flag = True
        for obj in (self._resp, self._sess):
            if obj is None:
                continue
            try:
                obj.close()
            except Exception:
                pass

    def cancelled(self) -> bool:
        return self.flag


def normalize_base(url: str) -> str:
    u = (url or "").strip().rstrip("/")
    if not u:
        return ""
    if u.endswith("/v1"):
        return u
    return u + "/v1"


_PRIVATE_HOST_PREFIXES = ("localhost", "127.", "0.0.0.0", "192.168.", "10.", "[::1]")


def _check_gateway_scheme(url: str):
    """公益网关必须走 HTTPS；本机/内网调试地址放行。"""
    low = url.lower()
    if low.startswith("https://"):
        return
    if low.startswith("http://"):
        host = low[len("http://"):].split("/", 1)[0].split(":", 1)[0]
        if host.startswith(_PRIVATE_HOST_PREFIXES) or host.endswith(".local"):
            return
        raise AIClientError(
            "公益网关地址必须是 HTTPS（内网调试地址除外）。"
            "明文 HTTP 会泄露对话内容，请给网关配好证书再填。")
        return
    raise AIClientError("网关地址要以 https:// 开头")


def resolve_endpoint(settings: dict) -> dict:
    """返回 {mode, url, headers, model, public}。

    公益模式只走网关（ai_gateway/server.py），客户端不再内嵌上游令牌。
    模型名允许用户在设置里改；网关侧有白名单，不在名单里会回落默认。
    """
    mode = (settings.get("ai_mode") or "public").strip().lower()
    model = (settings.get("ai_model") or DEFAULT_MODEL).strip() or DEFAULT_MODEL
    if mode in ("custom", "newapi", "自定义"):
        base = normalize_base(settings.get("ai_base_url") or "")
        key = (settings.get("ai_api_key") or "").strip()
        if not base:
            raise AIClientError("请在设置里填写自定义 NewAPI 地址（到 /v1 为止）")
        if not key:
            raise AIClientError("请在设置里填写 NewAPI 令牌")
        return {
            "mode": "custom",
            "url": base + "/chat/completions",
            "models_url": base + "/models",
            "headers": {
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            "model": model,
            "public": False,
        }
    gateway = (settings.get("ai_gateway_url") or DEFAULT_GATEWAY_URL or "").strip().rstrip("/")
    if not gateway:
        raise AIClientError(
            "公益接口需要网关地址：请在「设置 → AI 助手」填写公益网关（HTTPS），"
            "或切到「自定义 NewAPI」用自己的接口。")
    _check_gateway_scheme(gateway)
    return {
        "mode": "public",
        "url": gateway + "/pymcl/chat",
        "models_url": gateway + "/health",
        "headers": {
            "Content-Type": "application/json",
            "X-PyMCL-Client": CLIENT_HEADER,
        },
        "model": model,
        "public": True,
    }


def test_connection(settings: dict) -> str:
    ep = resolve_endpoint(settings)
    r = requests.get(
        ep["models_url"], headers=ep["headers"], timeout=15,
        proxies={"http": None, "https": None},
    )
    if r.status_code >= 400:
        raise AIClientError(_err_text(r), r.status_code)
    try:
        data = r.json()
    except Exception:
        return "已连通"
    if ep["public"]:
        if data.get("service"):
            allowed = data.get("models") or []
            want = ep.get("model") or DEFAULT_MODEL
            if allowed and want not in allowed:
                return (f"公益网关正常，但 {want} 不在白名单里"
                        f"（可用：{'、'.join(allowed[:4])}），会回落默认模型")
            return f"公益网关正常（{data.get('service')}），模型 {want}"
        return "公益网关已连通"
    models = data.get("data") or []
    names = [m.get("id") for m in models if isinstance(m, dict) and m.get("id")]
    if names:
        return f"NewAPI 正常，可用模型 {len(names)} 个，例如 {names[0]}"
    return "NewAPI 已连通"


def _err_text(resp) -> str:
    code = getattr(resp, "status_code", 0) or 0
    prefix = f"HTTP {code} " if code else ""
    try:
        resp.encoding = "utf-8"
    except Exception:
        pass
    def _out(text: str) -> str:
        return (prefix + _decode_sse_line(text or "接口错误"))[:800]

    try:
        data = resp.json()
    except Exception:
        return _out(resp.text or "接口错误")
    err = data.get("error") if isinstance(data, dict) else None
    if isinstance(err, dict):
        msg = err.get("message") or err.get("msg") or err.get("code") or ""
        extra = err.get("type") or err.get("code") or ""
        rid = ""
        for key in ("request_id", "requestId", "id"):
            if data.get(key) or err.get(key):
                rid = str(data.get(key) or err.get(key))
                break
        parts = [str(msg).strip()]
        if extra and str(extra) not in parts[0]:
            parts.append(str(extra))
        if rid and rid not in parts[0]:
            parts.append(f"request id: {rid}")
        text = " ".join(p for p in parts if p)
        return _out(text or json.dumps(err, ensure_ascii=False))
    if isinstance(err, str) and err.strip():
        return _out(err)
    if isinstance(data, dict):
        msg = data.get("message") or data.get("msg")
        if msg:
            return _out(str(msg))
    return _out(resp.text or "接口错误")


def _decode_sse_line(raw) -> str:
    """SSE 常不带 charset；Windows 上 decode_unicode 会按 latin-1 把中文解成乱码。"""
    if raw is None:
        return ""
    if isinstance(raw, str):
        if any("\u4e00" <= ch <= "\u9fff" for ch in raw):
            return raw
        try:
            return raw.encode("latin-1").decode("utf-8")
        except (UnicodeDecodeError, UnicodeEncodeError):
            return raw
    return raw.decode("utf-8", errors="replace")


def _args_complete(raw: str) -> bool:
    s = (raw or "").strip()
    if not s:
        return True
    try:
        json.loads(s)
        return True
    except json.JSONDecodeError:
        return False


def _flush_complete_tools(acc: dict) -> list | None:
    out = _flush_tools(acc)
    if not out:
        return None
    if all(_args_complete((t.get("function") or {}).get("arguments") or "") for t in out):
        return out
    return None


def _assemble_stream(resp) -> Iterator[dict]:
    tool_acc = {}
    got_delta = False
    resp.encoding = "utf-8"
    try:
        lines = resp.iter_lines(decode_unicode=False)
        for raw in lines:
            if not raw:
                continue
            line = _decode_sse_line(raw).strip()
            if line.startswith("data:"):
                line = line[5:].strip()
            if not line or line == "[DONE]":
                tools = _flush_complete_tools(tool_acc) if tool_acc else None
                if tools:
                    yield {"type": "tool_calls", "tool_calls": tools}
                    return
                if tool_acc:
                    yield {"type": "error", "message": "工具参数不完整，正在换一次非流式"}
                    return
                yield {"type": "done"}
                return
            try:
                chunk = json.loads(line)
            except json.JSONDecodeError:
                continue
            if chunk.get("error"):
                err = chunk["error"]
                msg = err.get("message") if isinstance(err, dict) else str(err)
                yield {"type": "error", "message": msg}
                return
            choices = chunk.get("choices") or []
            if not choices:
                continue
            choice = choices[0]
            delta = choice.get("delta") or {}
            text = delta.get("content")
            if text:
                got_delta = True
                yield {"type": "delta", "text": _decode_sse_line(text)}
            for tc in delta.get("tool_calls") or []:
                idx = tc.get("index", 0)
                slot = tool_acc.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                if tc.get("id"):
                    slot["id"] = tc["id"]
                fn = tc.get("function") or {}
                if fn.get("name"):
                    slot["name"] = fn["name"]
                if fn.get("arguments"):
                    slot["arguments"] += fn["arguments"]
            reason = choice.get("finish_reason")
            if reason == "tool_calls":
                tools = _flush_complete_tools(tool_acc)
                if tools:
                    yield {"type": "tool_calls", "tool_calls": tools}
                    return
                yield {"type": "error", "message": "工具参数不完整，正在换一次非流式"}
                return
            if reason in ("stop", "length"):
                yield {"type": "done", "finish_reason": reason}
                return
        tools = _flush_complete_tools(tool_acc) if tool_acc else None
        if tools:
            yield {"type": "tool_calls", "tool_calls": tools}
            return
        if tool_acc:
            yield {"type": "error", "message": "工具参数不完整，正在换一次非流式"}
            return
        if got_delta:
            yield {"type": "done"}
            return
        yield {"type": "error", "message": "接口没有返回内容"}
    except (ReadTimeout, ChunkedEncodingError):
        tools = _flush_complete_tools(tool_acc) if tool_acc else None
        if tools:
            yield {"type": "tool_calls", "tool_calls": tools}
            return
        yield {"type": "error", "message": "接口超时，正在换一次非流式"}
        return


def _flush_tools(acc: dict) -> list:
    out = []
    for idx in sorted(acc):
        item = acc[idx]
        out.append({
            "id": item.get("id") or f"call_{idx}",
            "type": "function",
            "function": {
                "name": item.get("name") or "",
                "arguments": item.get("arguments") or "{}",
            },
        })
    return out


def _http_error(exc, http_cancel):
    if http_cancel and http_cancel.cancelled():
        return AIClientError("已停止")
    return AIClientError(f"连不上接口: {exc}")


def chat_stream(settings: dict, messages: list, tools: list | None = None,
                temperature: float = 0.3, http_cancel=None,
                max_tokens: int = 0, model: str = "") -> Iterator[dict]:
    ep = resolve_endpoint(settings)
    body = {
        "model": model or ep["model"],
        "messages": messages,
        "temperature": temperature,
        "stream": True,
        "max_tokens": int(max_tokens or MAX_TOKENS),
    }
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    session = requests.Session()
    if http_cancel:
        http_cancel.bind(session)
        if http_cancel.cancelled():
            raise AIClientError("已停止")
    try:
        resp = session.post(
            ep["url"], headers=ep["headers"], json=body,
            stream=True, timeout=(STREAM_CONNECT_TIMEOUT, STREAM_READ_TIMEOUT),
            proxies={"http": None, "https": None},
        )
    except requests.RequestException as exc:
        raise _http_error(exc, http_cancel) from exc
    if http_cancel:
        http_cancel.bind(session, resp)
        if http_cancel.cancelled():
            raise AIClientError("已停止")
    if resp.status_code >= 400:
        raise AIClientError(_err_text(resp), resp.status_code)
    yield from _assemble_stream(resp)


def chat_once(settings: dict, messages: list, tools: list | None = None,
              temperature: float = 0.3, http_cancel=None,
              max_tokens: int = 0, model: str = "") -> dict:
    ep = resolve_endpoint(settings)
    body = {
        "model": model or ep["model"],
        "messages": messages,
        "temperature": temperature,
        "stream": False,
        "max_tokens": int(max_tokens or MAX_TOKENS),
    }
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    session = requests.Session()
    if http_cancel:
        http_cancel.bind(session)
        if http_cancel.cancelled():
            raise AIClientError("已停止")
    try:
        resp = session.post(
            ep["url"], headers=ep["headers"], json=body, timeout=ONCE_TIMEOUT,
            proxies={"http": None, "https": None},
        )
    except requests.RequestException as exc:
        raise _http_error(exc, http_cancel) from exc
    if resp.status_code >= 400:
        raise AIClientError(_err_text(resp), resp.status_code)
    resp.encoding = "utf-8"
    data = resp.json()
    choice = (data.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    return {
        "content": msg.get("content") or "",
        "tool_calls": msg.get("tool_calls") or [],
        "finish_reason": choice.get("finish_reason") or "stop",
    }
