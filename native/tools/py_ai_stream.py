# -*- coding: utf-8 -*-
"""C 桥的 AI 流式助手：跑一次 ai_send，把事件总线逐行打成 JSON 供 C 侧转发。

一次性 py_rpc 对 ai_send 无能为力：它返回 {"started": true} 后进程立刻退出，
流式线程被杀死，UI 一个事件都收不到。本助手保持进程存活直到对话真正结束。

用法:
  python native/tools/py_ai_stream.py --root <repo> --params <in.json>

stdout: 每行 {"event": "ai.xxx", "data": {...}}；ai.done / ai.fail 后退出。
stdin:  每行一个控制命令:
        {"op": "stop"} / {"op": "confirm", "ok": true} / {"op": "answer", "result": ...}
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import sys
import threading
from pathlib import Path


def _prepare(root: Path):
    root = root.resolve()
    os.environ["PYMCL_HOME"] = str(root)
    os.chdir(root)
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))


def _emit_line(event: str, data) -> None:
    print(json.dumps({"event": event, "data": data}, ensure_ascii=False, default=str),
          flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--params", required=True)
    args = ap.parse_args()
    _prepare(Path(args.root))

    params_path = Path(args.params)
    try:
        params = json.loads(params_path.read_text(encoding="utf-8") or "{}")
    except Exception as exc:  # noqa: BLE001
        # 事件形状对齐 bridge/api.py 的 ai.fail：{"text", "stopped"}。
        # UI（EziApp/WinUI）只读 text 字段，以前发 message 会显示成
        # 兜底的「AI 请求失败/出错了」，真实原因被吞掉。
        _emit_line("ai.fail", {"text": f"参数解析失败: {exc}", "stopped": False})
        return 1
    try:
        params_path.unlink()
    except OSError:
        pass
    if not isinstance(params, dict):
        params = {}

    try:
        from bridge.api import BackendAPI, EventBus
    except Exception as exc:  # noqa: BLE001
        _emit_line("ai.fail", {"text": f"无法加载 Python 后端: {exc}", "stopped": False})
        return 1

    bus = EventBus()
    api = BackendAPI(bus)
    q = bus.subscribe()

    def stdin_loop():
        for line in sys.stdin:
            try:
                cmd = json.loads(line)
            except Exception:  # noqa: BLE001
                continue
            op = cmd.get("op")
            if op == "stop":
                api.ai_stop()
            elif op == "confirm":
                api.ai_confirm(bool(cmd.get("ok")))
            elif op == "answer":
                api.ai_answer(cmd.get("result"))
        # C 端关闭 stdin 意味着桥要退出：视同用户停止，别让 agent 悬着
        api.ai_stop()

    threading.Thread(target=stdin_loop, daemon=True, name="ai-stdin").start()

    r = api.ai_send(params.get("text") or "", params.get("chat_id") or "",
                    params.get("launch") or None)
    if not (isinstance(r, dict) and r.get("ok")):
        msg = (r or {}).get("message") if isinstance(r, dict) else None
        _emit_line("ai.fail", {"text": msg or "AI 无法启动", "stopped": False})
        return 1

    while True:
        try:
            item = q.get(timeout=3600)
        except queue.Empty:
            _emit_line("ai.fail", {"text": "AI 超时无响应", "stopped": False})
            return 1
        ev = str(item.get("event") or "")
        # ui_changed 也要透传：AI 工具建实例/装模组后靠它让前端刷新列表，
        # 以前被 ai.* 过滤器吞掉，WinUI 列表在 C 桥下一直是旧的。
        # 任务类事件（task_added/progress/finished）故意不转发：task_id 活在
        # 本子进程里，转发出去会让任务页出现一个永远取消不掉的假任务。
        if not (ev.startswith("ai.") or ev == "ui_changed"):
            continue
        _emit_line(ev, item.get("data") or {})
        if ev in ("ai.done", "ai.fail"):
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
