# -*- coding: utf-8 -*-
"""One-shot RPC helper: C bridge calls this for methods not (yet) native.

Usage:
  python native/tools/py_rpc.py --root <repo> --method <name> --params <in.json> --out <out.json>
Exit 0 + out.json {"ok":true,"result":...} or {"ok":false,"error":"..."}

Protocol on stdout (consumed line-by-line by the C bridge):
  EVENT {"event":"progress","data":{...}}   -- re-emitted to SSE in real time
  RESULT {"ok":true,"result":...}           -- printed as soon as the result exists

start_task 型方法（返回 task-N）以前在这里是假成功：进程写完结果就退出，
daemon 工作线程（登录 / 自更新 / 修复）当场被杀，事件也没人转发。
现在：先打印 RESULT 让 C 端尽快把 task id 还给 UI，然后留在这里等任务真正
跑完（事件持续经 EVENT 行转发），再退出。
"""
from __future__ import annotations

import argparse
import inspect
import json
import os
import re
import sys
import threading
from pathlib import Path

_TASK_ID_RE = re.compile(r"^task-\d+$")
_STDOUT_LOCK = threading.Lock()
# 等任务完成的上限。C 端读线程跟着子进程活，这里兜底别挂死。
_TASK_WAIT_SEC = 3600


def _prepare(root: Path):
    root = root.resolve()
    os.environ["PYMCL_HOME"] = str(root)
    os.chdir(root)
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    return root


def _emit_line(prefix: str, payload: dict):
    try:
        text = json.dumps(payload, ensure_ascii=False, default=str)
    except Exception:
        return
    with _STDOUT_LOCK:
        sys.stdout.write(prefix + " " + text + "\n")
        sys.stdout.flush()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--method", required=True)
    ap.add_argument("--params", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    root = _prepare(Path(args.root))
    out_path = Path(args.out)

    def write(payload: dict) -> int:
        out_path.write_text(json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8")
        _emit_line("RESULT", payload)
        return 0 if payload.get("ok") else 1

    try:
        from bridge.api import BackendAPI, EventBus

        params = json.loads(Path(args.params).read_text(encoding="utf-8") or "{}")
        if not isinstance(params, dict):
            params = {}
        bus = EventBus()
        # 把每个事件同步打到 stdout，C 端逐行转发到 SSE。
        orig_emit = bus.emit

        def emit_and_stream(event: str, data: dict):
            orig_emit(event, data)
            _emit_line("EVENT", {"event": event, "data": data or {}})

        bus.emit = emit_and_stream
        api = BackendAPI(bus)
        fn = getattr(api, args.method, None)
        if fn is None or not callable(fn):
            return write({"ok": False, "error": f"unknown method: {args.method}"})
        sig = inspect.signature(fn)
        kwargs = {}
        for name, p in sig.parameters.items():
            if name == "self":
                continue
            if name in params:
                kwargs[name] = params[name]
            elif p.default is inspect.Parameter.empty and p.kind in (
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                inspect.Parameter.KEYWORD_ONLY,
            ):
                # required missing — leave; call may raise
                pass
        # also accept flat params for single-arg helpers
        if not kwargs and len(sig.parameters) <= 2 and params:
            # try mapping common aliases
            for name in sig.parameters:
                if name == "self":
                    continue
                if name in params:
                    kwargs[name] = params[name]
        result = fn(**kwargs)
        rc = write({"ok": True, "result": result})
        if isinstance(result, str) and _TASK_ID_RE.match(result):
            # 结果是任务 id：留下来陪任务跑完（daemon 线程不能没人管）。
            try:
                api.wait_task(result, timeout=_TASK_WAIT_SEC)
            except Exception:
                pass
        return rc
    except Exception as exc:  # noqa: BLE001
        return write({"ok": False, "error": str(exc)})


if __name__ == "__main__":
    raise SystemExit(main())
