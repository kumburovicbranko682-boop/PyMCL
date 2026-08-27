# -*- coding: utf-8 -*-
"""工具循环：流式输出 + 写操作确认。"""

from __future__ import annotations

import json

from . import memory as memory_mod
from .client import AIClientError, chat_once, chat_stream
from .defaults import (
    DANGEROUS_TOOLS, DIAGNOSE_MAX_TOKENS, DIAGNOSE_MODEL, DIAGNOSE_TOOLS,
    LONG_TOOLS, MAX_HISTORY, MAX_TOOL_ROUNDS,
)
from .prompt import system_prompt
from .tools import (
    TOOL_SCHEMAS, confirm_label, exact_match_hit, is_ask_tool, is_write_tool,
    normalize_ask_args, parse_args, run_tool, runtime_context,
)

SEARCH_TOOLS = {"search_mods", "search_modpacks", "search_versions"}


class AgentCancelled(Exception):
    pass


def _trim_history(history: list) -> list:
    if len(history) <= MAX_HISTORY:
        return list(history)
    return list(history[-MAX_HISTORY:])


def _system_messages(backend, settings: dict, chat_notes: list | None = None) -> list:
    ctx = runtime_context(backend)
    msgs = [
        {"role": "system", "content": system_prompt()},
        {"role": "system", "content": "当前启动器状态：\n" + ctx},
    ]
    mem = memory_mod.system_note()
    if mem:
        msgs.append({"role": "system", "content": mem})
    if chat_notes:
        recent = [str(n) for n in chat_notes[-8:] if str(n).strip()]
        if recent:
            msgs.append({"role": "system", "content":
                         "[本对话此前几轮实际执行过的操作]\n" + "\n".join(recent)})
    note = _permission_note(settings or {})
    if note:
        msgs.append({"role": "system", "content": note})
    return msgs


def _permission_note(settings: dict) -> str:
    """把用户权限设置同步给模型，避免它在免确认模式下还嘴上说「会弹确认」。"""
    if not bool(settings.get("ai_confirm_writes", True)):
        return (
            "[权限设置] 用户关闭了「变更前确认」：写操作会直接执行，不会弹确认。"
            "你仍要先用一句话说明将要做什么。"
        )
    if (settings.get("ai_permission_mode") or "standard") == "full":
        return (
            "[权限设置] 用户开启了「完全访问」：多数写操作直接执行；"
            "删除实例、删除模组、改配置仍会弹确认，要等用户点了才执行。"
        )
    return ""


def _confirm_policy(settings: dict, tname: str) -> bool:
    """写操作要不要弹确认：开关关了全不弹；完全访问只保破坏性操作。"""
    if not bool((settings or {}).get("ai_confirm_writes", True)):
        return False
    if ((settings or {}).get("ai_permission_mode") or "standard") == "full":
        return tname in DANGEROUS_TOOLS
    return True


def _round_kwargs(settings: dict, deep: bool) -> dict:
    """诊断轮：放宽 token；公益模式尝试切深度诊断模型（网关白名单不认就回落）。"""
    if not deep:
        return {}
    out = {"max_tokens": DIAGNOSE_MAX_TOKENS}
    if (settings.get("ai_mode") or "public").strip().lower() not in ("custom", "newapi", "自定义"):
        out["model"] = DIAGNOSE_MODEL
    return out


def run_agent(backend, settings: dict, history: list, user_text: str,
              on_delta=None, on_status=None, confirm_fn=None, ask_fn=None,
              cancelled=None, http_cancel=None, chat_notes: list | None = None):
    """
    on_delta(text)
    on_status(kind, payload)
    confirm_fn(tool_name, args, label) -> bool
    ask_fn(questions, title) -> dict | None
    cancelled() -> bool
    chat_notes: 本对话此前几轮的工具执行摘要（跨轮记忆）
    返回最终助手文本。
    """
    def _check():
        if cancelled and cancelled():
            raise AgentCancelled()

    messages = _system_messages(backend, settings, chat_notes) + _trim_history(history)
    messages.append({"role": "user", "content": user_text})

    final = ""
    need_followup = False
    followup_used = False
    search_done: dict = {}
    need_pick = False
    pick_nudged = False
    deep_round = False        # 本轮跑过诊断类工具：后续回复放宽 token/换诊断模型
    skip_confirm_next = False  # ask_user 刚选完：下一个非破坏性写操作免二次确认
    for _round in range(MAX_TOOL_ROUNDS):
        _check()
        if on_status:
            after = any(m.get("role") == "tool" for m in messages)
            on_status("think", {"after_tools": after})
        tool_calls = []
        text_parts = []
        truncated = False
        stream_failed = False
        round_kwargs = _round_kwargs(settings, deep_round)
        try:
            for ev in chat_stream(settings, messages, TOOL_SCHEMAS,
                                  http_cancel=http_cancel, **round_kwargs):
                _check()
                kind = ev.get("type")
                if kind == "delta":
                    piece = ev.get("text") or ""
                    text_parts.append(piece)
                    if on_delta and piece:
                        on_delta(piece)
                elif kind == "tool_calls":
                    tool_calls = ev.get("tool_calls") or []
                    break
                elif kind == "done":
                    truncated = ev.get("finish_reason") == "length"
                    break
                elif kind == "error":
                    raise AIClientError(ev.get("message") or "接口错误")
        except AIClientError as exc:
            if exc.fatal():
                raise
            stream_failed = True
        except Exception:
            stream_failed = True

        if not tool_calls and (stream_failed or not "".join(text_parts)):
            _check()
            data = chat_once(settings, messages, TOOL_SCHEMAS,
                             http_cancel=http_cancel, **round_kwargs)
            if not text_parts and data.get("content"):
                text_parts.append(data["content"])
                if on_delta:
                    on_delta(data["content"])
            if not tool_calls:
                tool_calls = data.get("tool_calls") or []
            truncated = truncated or data.get("finish_reason") == "length"

        content = "".join(text_parts)
        if truncated and content:
            content += "\n\n（回复被长度限制截断了，需要的话让我继续。）"
        final = content or final
        if not tool_calls:
            if need_followup and not followup_used:
                followup_used = True
                need_followup = False
                messages.append({
                    "role": "user",
                    "content": (
                        "选项已经选完。立刻调用对应工具执行："
                        "装游戏用 install_game（纯原版 loader 填「无」）。"
                        "禁止只说话，禁止说已经在装。"
                    ),
                })
                continue
            if need_pick and not pick_nudged:
                pick_nudged = True
                need_pick = False
                messages.append({
                    "role": "user",
                    "content": (
                        "搜索已经结束。立刻 ask_user 列出刚才的结果让用户选。"
                        "禁止用相同关键词再搜。"
                    ),
                })
                continue
            return content or final or "我这边没有更多要做的了。"

        assistant_msg = {"role": "assistant", "content": content or None, "tool_calls": tool_calls}
        messages.append(assistant_msg)

        ordered = sorted(
            tool_calls,
            key=lambda tc: 0 if is_ask_tool((tc.get("function") or {}).get("name") or "") else 1,
        )
        asked = False
        wrote = False
        for tc in ordered:
            _check()
            fn = tc.get("function") or {}
            tname = fn.get("name") or ""
            args = parse_args(fn.get("arguments"))
            label = confirm_label(tname, args)
            if on_status:
                on_status("tool", {"name": tname, "args": args, "label": label})
            if is_ask_tool(tname):
                questions = normalize_ask_args(args)
                title = args.get("title") or ""
                if ask_fn:
                    answered = ask_fn(questions, title)
                else:
                    answered = None
                if not answered:
                    result = "用户取消了选择"
                    if on_status:
                        on_status("tool_skip", {"name": tname, "label": label})
                else:
                    asked = True
                    skip_confirm_next = True
                    result = answered if isinstance(answered, str) else json.dumps(
                        answered, ensure_ascii=False)
                    result = (
                        f"{result}\n"
                        "[系统] 用户已选完。下一步必须调用对应工具："
                        "装游戏 → install_game（纯原版 loader=无）。不要结束对话。"
                        "刚选完的这件事不会再弹确认，直接执行。"
                    )
                    if on_status:
                        on_status("tool_done", {"name": tname, "label": "已选择", "result": str(result)[:400]})
            elif tname in SEARCH_TOOLS:
                qkey = (tname, str(args.get("query") or "").strip().lower())
                if qkey in search_done:
                    result = (
                        f"{search_done[qkey]}\n"
                        "[系统] 这一轮已经用相同关键词搜过，结果就是上面这些。"
                        "禁止再搜同一词。立刻 ask_user 让用户选，或说明没找到。"
                    )
                    if on_status:
                        on_status("tool_skip", {"name": tname, "label": "拦截重复搜索"})
                    need_pick = True
                else:
                    if on_status:
                        on_status("tool_run", {"name": tname, "label": label})
                    result = run_tool(backend, tname, args, wait=False, cancelled=cancelled)
                    hit = None
                    try:
                        rows = json.loads(result) if isinstance(result, str) else None
                        if isinstance(rows, list):
                            hit = exact_match_hit(rows, str(args.get("query") or ""))
                    except Exception:
                        hit = None
                    if hit is not None and tname != "search_versions":
                        # 用户点名的东西搜到了精确唯一命中：直接装，别再让用户点一遍
                        ident = hit.get("slug") or hit.get("id") or hit.get("name") or ""
                        hint = (
                            f"\n[系统] 结果精确唯一命中：{hit.get('name')}"
                            f"（source={hit.get('source')}, slug/id={ident}）。"
                            "直接调用对应 install_* 安装它，不要 ask_user。"
                        )
                        skip_confirm_next = False
                    else:
                        hint = (
                            "\n[系统] 搜索结束。下一动作必须是 ask_user 让用户从上述结果里选。"
                            "禁止用相同关键词再次调用该搜索。"
                        )
                        need_pick = True
                    result = f"{result}{hint}"
                    search_done[qkey] = result
                    if on_status:
                        on_status("tool_done", {"name": tname, "label": label, "result": str(result)[:400]})
            elif is_write_tool(tname):
                ok = True
                need_confirm = bool(confirm_fn) and _confirm_policy(settings, tname)
                if (need_confirm and skip_confirm_next
                        and tname not in DANGEROUS_TOOLS):
                    # ask_user 刚选完：这次写操作就是用户点的那件事，不再弹一次确认
                    need_confirm = False
                if need_confirm:
                    ok = bool(confirm_fn(tname, args, label))
                if not ok:
                    result = "用户取消了这次操作"
                    if on_status:
                        on_status("tool_skip", {"name": tname, "label": label})
                else:
                    wrote = True
                    skip_confirm_next = False
                    if on_status:
                        on_status("tool_run", {"name": tname, "label": label})
                    wait = tname not in LONG_TOOLS and tname != "launch_game"
                    result = run_tool(backend, tname, args, wait=wait, cancelled=cancelled)
                    extra = {}
                    try:
                        parsed = json.loads(result) if isinstance(result, str) and result.startswith("{") else {}
                        if isinstance(parsed, dict) and parsed.get("task_id"):
                            extra["task_id"] = parsed["task_id"]
                    except Exception:
                        extra = {}
                    if on_status:
                        payload = {"name": tname, "label": label, "result": result[:400]}
                        payload.update(extra)
                        on_status("tool_done", payload)
            else:
                if on_status:
                    on_status("tool_run", {"name": tname, "label": label})
                result = run_tool(backend, tname, args, wait=False, cancelled=cancelled)
                if tname in DIAGNOSE_TOOLS:
                    deep_round = True
                payload = {"name": tname, "label": label, "result": str(result)[:400]}
                if tname == "diagnose_launch":
                    # 一键修复动作走旁路（结果字符串可能被截断，不能靠解析它）
                    diag = getattr(backend, "_last_diagnose", None) or {}
                    if diag.get("actions"):
                        payload["actions"] = diag["actions"]
                        payload["report"] = diag.get("report") or {}
                if on_status:
                    on_status("tool_done", payload)
            messages.append({
                "role": "tool",
                "tool_call_id": tc.get("id") or "",
                "name": tname,
                "content": result,
            })
        need_followup = asked and not wrote

    return final or "步骤有点多，先停在这里。你再说一下接下来要哪一步。"
