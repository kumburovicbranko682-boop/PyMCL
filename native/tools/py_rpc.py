# -*- coding: utf-8 -*-
"""One-shot RPC helper: C bridge calls this for methods not (yet) native.

Usage:
  python native/tools/py_rpc.py --root <repo> --method <name> --params <in.json> --out <out.json>
Exit 0 + out.json {"ok":true,"result":...} or {"ok":false,"error":"..."}
"""
from __future__ import annotations

import argparse
import inspect
import json
import os
import sys
from pathlib import Path


def _prepare(root: Path):
    root = root.resolve()
    os.environ["PYMCL_HOME"] = str(root)
    os.chdir(root)
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    return root


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
        return 0 if payload.get("ok") else 1

    try:
        from bridge.api import BackendAPI, EventBus

        params = json.loads(Path(args.params).read_text(encoding="utf-8") or "{}")
        if not isinstance(params, dict):
            params = {}
        bus = EventBus()
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
        # start_* 一类方法只把任务丢进后台线程就立刻返回 task id。本进程是
        # 一次性的：直接退出线程就死、任务假成功。等它真正跑完再带回最终结果。
        if isinstance(result, str) and (
            result in getattr(api, "_workers", {}) or result in getattr(api, "_task_results", {})
        ):
            done = api.wait_task(result)
            if not done.get("ok"):
                return write({"ok": False, "error": done.get("message") or "任务失败"})
            result = done.get("message") or "任务完成"
        return write({"ok": True, "result": result})
    except Exception as exc:  # noqa: BLE001
        return write({"ok": False, "error": str(exc)})


if __name__ == "__main__":
    raise SystemExit(main())
