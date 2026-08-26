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
        # 任务型方法（install_world / repair_version / start_* …）只返回 task id，
        # 真正的工作在守护线程里。一次性进程立刻退出会把线程杀死：UI 拿到
        # task id 显示“已排队”，下载却已经静默夭折。这里等任务真正结束，
        # 把最终成败作为本次调用的结果返回。
        if isinstance(result, str) and result in getattr(api, "_titles", {}):
            done = api.wait_task(result, timeout=7200)
            if done.get("ok"):
                return write({"ok": True, "result": done.get("message") or result})
            return write({"ok": False, "error": done.get("message") or "任务失败"})
        return write({"ok": True, "result": result})
    except Exception as exc:  # noqa: BLE001
        return write({"ok": False, "error": str(exc)})


if __name__ == "__main__":
    raise SystemExit(main())
