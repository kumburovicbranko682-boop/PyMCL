# -*- coding: utf-8 -*-
"""反馈上报与心跳。启动器只打反馈中心，不带管理令牌。

上传路径统一过 crash.filter_secrets 脱敏；网络原因发送失败的反馈进入
本地待发队列（feedback_pending.json），心跳线程联网后自动补发。
"""

from __future__ import annotations

import threading
import time
import uuid
from pathlib import Path

import requests

from . import APP_VERSION, utils
from .config import CONFIG
from .crash import filter_secrets
from .feedback_defaults import (
    CATEGORIES, CLIENT_HEADER, DEFAULT_FEEDBACK_URL, HEARTBEAT_SEC,
)
from . import sysinfo as sysinfo_mod

DEVICE_FILE = utils.ROOT / "device_id"
HISTORY_FILE = utils.ROOT / "feedback_history.json"
PENDING_FILE = utils.ROOT / "feedback_pending.json"
MAX_HISTORY = 30
MAX_PENDING = 20
PENDING_TTL_SEC = 14 * 86400

_HB_LOCK = threading.Lock()
_HB_STOP = threading.Event()
_HB_THREAD = None
_LAST_HB = {"t": 0.0, "ok": False, "error": ""}
_PENDING_LOCK = threading.Lock()


class FeedbackError(Exception):
    pass


class FeedbackNetworkError(FeedbackError):
    """连不上服务器或服务器暂时不可用（5xx / 429），可稍后重试。"""


def category_label(key: str) -> str:
    for k, label in CATEGORIES:
        if k == key:
            return label
    return key or "其他"


def device_id() -> str:
    stored = (CONFIG.get("device_id") or "").strip()
    if stored:
        return stored
    try:
        if DEVICE_FILE.is_file():
            text = DEVICE_FILE.read_text(encoding="utf-8").strip()
            if text:
                return text
    except OSError:
        pass
    value = uuid.uuid4().hex
    try:
        utils.ensure_dir(DEVICE_FILE.parent)
        DEVICE_FILE.write_text(value, encoding="utf-8")
    except OSError as exc:
        utils.log.debug("device_id 文件写入失败: %s", exc)
    try:
        CONFIG.set("device_id", value)
        CONFIG.save()
    except Exception as exc:
        utils.log.warning("device_id 保存到配置失败: %s", exc)
    return value


def resolve_url() -> str:
    url = (CONFIG.get("feedback_url") or "").strip()
    if not url:
        import os
        url = (os.environ.get("PYMCL_FEEDBACK_URL") or DEFAULT_FEEDBACK_URL or "").strip()
    return url.rstrip("/")


def heartbeat_enabled() -> bool:
    return has_consent() and bool(CONFIG.get("feedback_heartbeat", True))


def consent_asked() -> bool:
    return CONFIG.get("feedback_consent") is not None


def has_consent() -> bool:
    return CONFIG.get("feedback_consent") is True


def set_consent(ok: bool) -> bool:
    CONFIG.set("feedback_consent", bool(ok))
    try:
        CONFIG.save()
    except Exception as exc:
        # 保存失败会导致下次启动重复弹同意框，必须留痕
        utils.log.warning("反馈同意状态保存失败: %s", exc)
    return bool(ok)


def _session():
    sess = requests.Session()
    try:
        from .net import apply_direct_to_session
        apply_direct_to_session(sess)
    except Exception as exc:
        utils.log.debug("反馈会话直连配置失败: %s", exc)
    return sess


def _headers() -> dict:
    return {
        "Content-Type": "application/json",
        "X-PyMCL-Client": CLIENT_HEADER,
        "User-Agent": CLIENT_HEADER,
    }


def _post(path: str, payload: dict, timeout=20) -> dict:
    base = resolve_url()
    if not base:
        raise FeedbackError("未配置反馈服务器。开发者请启动 feedback_hub，并在设置里填写地址。")
    url = base + path
    try:
        sess = _session()
        resp = sess.post(url, json=payload, headers=_headers(), timeout=timeout)
    except requests.RequestException as exc:
        raise FeedbackNetworkError(f"连不上反馈服务器: {exc}") from exc
    text = (resp.text or "")[:800]
    if resp.status_code >= 400:
        # 5xx / 429 是服务器暂时问题，稍后重试有意义；其余 4xx 重试也没用
        if resp.status_code >= 500 or resp.status_code == 429:
            raise FeedbackNetworkError(f"反馈服务器 HTTP {resp.status_code}: {text}")
        raise FeedbackError(f"反馈服务器 HTTP {resp.status_code}: {text}")
    try:
        data = resp.json()
    except Exception:
        raise FeedbackError("反馈服务器返回了无法解析的内容")
    if not isinstance(data, dict):
        raise FeedbackError("反馈服务器返回格式不对")
    if data.get("ok") is False:
        raise FeedbackError(str(data.get("error") or "提交失败"))
    return data


def _history_load() -> list:
    rows = utils.read_json(HISTORY_FILE, []) or []
    return rows if isinstance(rows, list) else []


def _history_add(row: dict):
    rows = _history_load()
    rows.insert(0, row)
    utils.write_json(HISTORY_FILE, rows[:MAX_HISTORY])


def history() -> list:
    return _history_load()


# ---------------------------------------------------------------- 离线待发队列

def _pending_load() -> list:
    rows = utils.read_json(PENDING_FILE, []) or []
    return rows if isinstance(rows, list) else []


def pending_count() -> int:
    return len(_pending_load())


def _pending_enqueue(payload: dict):
    now = time.time()
    with _PENDING_LOCK:
        rows = [r for r in _pending_load()
                if isinstance(r, dict) and now - float(r.get("ts") or 0) < PENDING_TTL_SEC]
        rows.append({"ts": now, "tries": 0, "payload": payload})
        utils.write_json(PENDING_FILE, rows[-MAX_PENDING:])
    utils.log.info("反馈已存入待发队列（%s 条），联网后自动补发", len(rows[-MAX_PENDING:]))


def flush_pending() -> int:
    """尝试补发队列里的反馈。返回成功条数；遇到网络错误立即停止等下次。"""
    if not has_consent() or not resolve_url():
        return 0
    with _PENDING_LOCK:
        rows = _pending_load()
        if not rows:
            return 0
        now = time.time()
        keep: list = []
        sent = 0
        offline = False
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("payload"), dict):
                continue
            if now - float(row.get("ts") or 0) >= PENDING_TTL_SEC:
                continue
            if offline:
                keep.append(row)
                continue
            try:
                data = _post("/api/v1/feedback", row["payload"], timeout=20)
                sent += 1
                _history_add({
                    "id": data.get("id") or "",
                    "ts": time.time(),
                    "category": row["payload"].get("category") or "other",
                    "title": row["payload"].get("title") or "",
                    "ok": True,
                    "queued": True,
                })
            except FeedbackNetworkError as exc:
                utils.log.debug("待发队列补发失败（网络）: %s", exc)
                row["tries"] = int(row.get("tries") or 0) + 1
                keep.append(row)
                offline = True
            except FeedbackError as exc:
                # 服务器明确拒绝（格式/内容问题），重试无意义，丢弃并留痕
                utils.log.warning("待发队列条目被服务器拒绝，已丢弃: %s", exc)
        utils.write_json(PENDING_FILE, keep[-MAX_PENDING:])
        if sent:
            utils.log.info("待发队列补发成功 %s 条", sent)
        return sent


def _sanitize_crash(crash: dict | None) -> dict | None:
    if not isinstance(crash, dict):
        return None
    out = {}
    for key, value in crash.items():
        out[key] = filter_secrets(value) if isinstance(value, str) else value
    return out


def submit(
    category: str,
    title: str,
    body: str,
    contact: str = "",
    include_sysinfo: bool = True,
    crash: dict | None = None,
    scan_system_java: bool = True,
    queue_on_network_error: bool = True,
) -> dict:
    if not has_consent():
        raise FeedbackError("需要先同意上传诊断数据。第一次打开启动器时会询问，也可在设置里开启。")
    cat = (category or "other").strip().lower()
    if cat not in {k for k, _ in CATEGORIES}:
        cat = "other"
    # 上传路径统一脱敏：正文/标题里可能粘贴了带 accessToken 的日志
    title = filter_secrets((title or "").strip())[:120]
    body = filter_secrets((body or "").strip())[:16000]
    contact = (contact or "").strip()[:120]
    if not title and not body:
        raise FeedbackError("请填写标题或内容")
    if not title:
        title = (body.splitlines()[0] if body else "未命名反馈")[:80]
    payload = {
        "device_id": device_id(),
        "category": cat,
        "title": title,
        "body": body,
        "contact": contact,
        "app_version": APP_VERSION,
        "crash": _sanitize_crash(crash),
    }
    if include_sysinfo:
        payload["sysinfo"] = sysinfo_mod.collect(force=True, scan_system_java=scan_system_java)
    try:
        data = _post("/api/v1/feedback", payload, timeout=25)
    except FeedbackNetworkError as exc:
        if queue_on_network_error:
            _pending_enqueue(payload)
            raise FeedbackNetworkError(
                f"{exc}\n这条反馈已保存到待发队列，联网后会自动补发。") from exc
        raise
    _history_add({
        "id": data.get("id") or "",
        "ts": time.time(),
        "category": cat,
        "title": title,
        "ok": True,
    })
    flush_pending()
    return data


def submit_crash(report: dict, extra: str = "") -> dict:
    report = report or {}
    title = (report.get("headline") or report.get("title") or report.get("summary") or "游戏崩溃")[:120]
    chunks = [
        extra.strip(),
        str(report.get("summary") or ""),
        str(report.get("detail") or report.get("output_tail") or "")[:8000],
    ]
    body = "\n\n".join(x for x in chunks if x).strip() or title
    crash = {
        "headline": report.get("headline") or "",
        "summary": report.get("summary") or "",
        "title": report.get("title") or "",
        "help": report.get("help") or "",
        "direct_file": report.get("direct_file") or "",
        "exit_code": report.get("exit_code"),
        "exit_hint": report.get("exit_hint") or "",
        # 附带脱敏后的日志尾部：分析不出原因的崩溃全靠这些定位。
        # 上限合计约 18KB，远低于服务端 512KB 请求体上限。
        "output_tail": str(report.get("output_tail") or "")[-8000:],
        "log_mc": str(report.get("log_mc") or "")[-4000:],
        "log_crash": str(report.get("log_crash") or "")[-4000:],
        "log_hs": str(report.get("log_hs") or "")[-2000:],
    }
    return submit("crash", title, body, include_sysinfo=True, crash=crash)


def heartbeat_once(status: str = "online", timeout: float = 12) -> dict:
    if not has_consent():
        raise FeedbackError("需要先同意上传诊断数据")
    payload = {
        "device_id": device_id(),
        "status": status if status in ("online", "offline") else "online",
        "app_version": APP_VERSION,
        # 心跳 30 秒一拍：快照用 10 分钟缓存，别每拍都起 PowerShell 探硬件。
        "sysinfo": sysinfo_mod.collect(scan_system_java=False, max_age=600)
        if status != "offline" else {},
    }
    return _post("/api/v1/heartbeat", payload, timeout=timeout)


def last_heartbeat() -> dict:
    return dict(_LAST_HB)


def _hb_loop(interval: float):
    while not _HB_STOP.is_set():
        if heartbeat_enabled() and resolve_url():
            try:
                heartbeat_once("online")
                _LAST_HB["t"] = time.time()
                _LAST_HB["ok"] = True
                _LAST_HB["error"] = ""
            except Exception as exc:
                _LAST_HB["t"] = time.time()
                _LAST_HB["ok"] = False
                _LAST_HB["error"] = str(exc)
                utils.log.debug("反馈心跳失败: %s", exc)
            else:
                # 心跳通了说明网络在线，顺手补发离线队列
                try:
                    flush_pending()
                except Exception as exc:
                    utils.log.debug("待发队列补发出错: %s", exc)
        _HB_STOP.wait(interval)


def start_heartbeat(interval: float | None = None):
    global _HB_THREAD
    sec = float(interval or HEARTBEAT_SEC)
    with _HB_LOCK:
        if _HB_THREAD and _HB_THREAD.is_alive():
            return
        _HB_STOP.clear()
        _HB_THREAD = threading.Thread(
            target=_hb_loop, args=(sec,), name="pymcl-feedback-hb", daemon=True)
        _HB_THREAD.start()


def stop_heartbeat(send_offline: bool = True, wait: float = 2.5):
    """停止心跳。离线心跳放到后台线程限时发送，最多阻塞 wait 秒，
    避免服务器不可达时关窗卡住十几秒。"""
    _HB_STOP.set()
    if not (send_offline and resolve_url() and heartbeat_enabled()):
        return

    def _bye():
        try:
            heartbeat_once("offline", timeout=max(1.0, min(wait, 5.0)))
        except Exception as exc:
            utils.log.debug("离线心跳发送失败: %s", exc)

    t = threading.Thread(target=_bye, name="pymcl-feedback-bye", daemon=True)
    t.start()
    t.join(max(0.0, wait))
