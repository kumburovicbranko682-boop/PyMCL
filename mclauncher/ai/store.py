# -*- coding: utf-8 -*-
"""多对话持久化：重启后还在。"""

from __future__ import annotations

import time
import uuid

from mclauncher import utils

STORE_FILE = utils.ROOT / "ai_chats.json"
MAX_CHATS = 40
MAX_MESSAGES = 24
MAX_NOTES = 20


def _empty():
    cid = _new_id()
    return {
        "active_id": cid,
        "chats": [_blank_chat(cid)],
    }


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def _blank_chat(cid: str | None = None) -> dict:
    now = int(time.time())
    return {
        "id": cid or _new_id(),
        "title": "新对话",
        "updated": now,
        "messages": [],
        # 每轮实际执行过的工具摘要：下一轮注入 system，模型不用靠气泡文字回忆
        "notes": [],
    }


def load() -> dict:
    data = utils.read_json(STORE_FILE, None)
    if not isinstance(data, dict) or not isinstance(data.get("chats"), list) or not data["chats"]:
        data = _empty()
        save(data)
        return data
    chats = []
    for raw in data["chats"]:
        if not isinstance(raw, dict) or not raw.get("id"):
            continue
        chats.append({
            "id": str(raw["id"]),
            "title": str(raw.get("title") or "对话")[:40],
            "updated": int(raw.get("updated") or 0),
            "messages": [
                {"role": m.get("role"), "content": m.get("content") or ""}
                for m in (raw.get("messages") or [])
                if isinstance(m, dict) and m.get("role") in ("user", "assistant", "error")
            ][-MAX_MESSAGES:],
            "notes": [str(n) for n in (raw.get("notes") or []) if str(n).strip()][-MAX_NOTES:],
        })
    if not chats:
        data = _empty()
        save(data)
        return data
    active = str(data.get("active_id") or "")
    if not any(c["id"] == active for c in chats):
        active = chats[0]["id"]
    return {"active_id": active, "chats": chats}


def save(data: dict):
    chats = list(data.get("chats") or [])[:MAX_CHATS]
    utils.write_json(STORE_FILE, {
        "active_id": data.get("active_id") or (chats[0]["id"] if chats else ""),
        "chats": chats,
    })


def new_chat(data: dict) -> dict:
    chat = _blank_chat()
    data["chats"] = [chat] + list(data.get("chats") or [])
    data["chats"] = data["chats"][:MAX_CHATS]
    data["active_id"] = chat["id"]
    save(data)
    return chat


def get_chat(data: dict, cid: str) -> dict | None:
    for c in data.get("chats") or []:
        if c.get("id") == cid:
            return c
    return None


def set_active(data: dict, cid: str) -> dict | None:
    chat = get_chat(data, cid)
    if not chat:
        return None
    data["active_id"] = cid
    save(data)
    return chat


def api_messages(messages: list) -> list:
    out = []
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        if role == "error":
            role = "assistant"
        if role not in ("user", "assistant"):
            continue
        out.append({"role": role, "content": m.get("content") or ""})
    return out


def append_notes(data: dict, cid: str, notes: list):
    """把本轮工具执行摘要挂到对话上（滚动上限，随 save 落盘）。"""
    chat = get_chat(data, cid)
    if not chat or not notes:
        return
    merged = list(chat.get("notes") or []) + [str(n) for n in notes if str(n).strip()]
    chat["notes"] = merged[-MAX_NOTES:]
    save(data)


def upsert_messages(data: dict, cid: str, messages: list, title: str | None = None):
    chat = get_chat(data, cid)
    if not chat:
        return
    chat["messages"] = list(messages or [])[-MAX_MESSAGES:]
    chat["updated"] = int(time.time())
    if title:
        chat["title"] = str(title)[:40]
    elif chat.get("title") in ("", "新对话"):
        for m in chat["messages"]:
            if m.get("role") == "user" and (m.get("content") or "").strip():
                chat["title"] = (m["content"].strip().replace("\n", " "))[:24]
                break
    data["chats"].sort(key=lambda c: c.get("updated") or 0, reverse=True)
    save(data)


def delete_chat(data: dict, cid: str) -> dict:
    data["chats"] = [c for c in (data.get("chats") or []) if c.get("id") != cid]
    if not data["chats"]:
        chat = _blank_chat()
        data["chats"] = [chat]
        data["active_id"] = chat["id"]
    elif data.get("active_id") == cid:
        data["active_id"] = data["chats"][0]["id"]
    save(data)
    return get_chat(data, data["active_id"])
