# -*- coding: utf-8 -*-
"""AI 助手的跨会话记忆：小 JSON，注入 system。

只记「下次对话真的用得上」的少量偏好与近况：
- 用户常用实例 / 最近装过的加载器和 MC 版本（省得每轮都问）；
- 最近的安装/诊断动作（「装刚才那个」有据可查）；
- 本机内存等一次性事实（OOM 诊断要用）。
不记对话原文，不记任何账号信息。
"""

from __future__ import annotations

import time

from mclauncher import utils

STORE_FILE = utils.ROOT / "ai_memory.json"
MAX_RECENT = 12


def _empty() -> dict:
    return {"prefs": {}, "recent": [], "facts": {}}


def load() -> dict:
    data = utils.read_json(STORE_FILE, None)
    if not isinstance(data, dict):
        return _empty()
    return {
        "prefs": dict(data.get("prefs") or {}),
        "recent": list(data.get("recent") or [])[-MAX_RECENT:],
        "facts": dict(data.get("facts") or {}),
    }


def save(data: dict):
    utils.write_json(STORE_FILE, {
        "prefs": dict(data.get("prefs") or {}),
        "recent": list(data.get("recent") or [])[-MAX_RECENT:],
        "facts": dict(data.get("facts") or {}),
    })


def record_event(kind: str, **fields):
    """记录一次有记忆价值的动作。失败静默：记忆坏了不能影响正事。"""
    try:
        data = load()
        prefs = data["prefs"]
        if kind == "install_game":
            if fields.get("loader") and fields["loader"] not in ("无", "none", "None"):
                prefs["preferred_loader"] = str(fields["loader"])
            if fields.get("version"):
                prefs["last_mc_version"] = str(fields["version"])
        if fields.get("instance"):
            prefs["last_instance"] = str(fields["instance"])
        row = {"kind": str(kind), "t": int(time.time())}
        for key in ("name", "version", "loader", "instance"):
            if fields.get(key):
                row[key] = str(fields[key])
        data["recent"] = (data.get("recent") or []) + [row]
        save(data)
    except Exception:
        pass


def remember_fact(key: str, value):
    try:
        data = load()
        if data["facts"].get(str(key)) == value:
            return
        data["facts"][str(key)] = value
        save(data)
    except Exception:
        pass


def _age(ts: int) -> str:
    d = max(0, int(time.time()) - int(ts or 0))
    if d < 3600:
        return f"{d // 60}分钟前"
    if d < 86400:
        return f"{d // 3600}小时前"
    return f"{d // 86400}天前"


def system_note() -> str:
    """给 system 消息用的一段简短记忆。没内容返回空串。"""
    try:
        data = load()
    except Exception:
        return ""
    lines = []
    prefs = data.get("prefs") or {}
    if prefs.get("preferred_loader"):
        lines.append(f"用户常用加载器: {prefs['preferred_loader']}")
    if prefs.get("last_mc_version"):
        lines.append(f"最近装的 MC 版本: {prefs['last_mc_version']}")
    if prefs.get("last_instance"):
        lines.append(f"最近操作的实例: {prefs['last_instance']}")
    recent = data.get("recent") or []
    if recent:
        acts = []
        for r in recent[-5:]:
            desc = r.get("kind") or ""
            if r.get("name"):
                desc += f" {r['name']}"
            if r.get("instance"):
                desc += f" @ {r['instance']}"
            acts.append(f"{desc}（{_age(r.get('t'))}）")
        lines.append("最近动作: " + "；".join(acts))
    for key, val in (data.get("facts") or {}).items():
        lines.append(f"{key}: {val}")
    if not lines:
        return ""
    return "[记忆] 跨对话记住的信息（可能过时，仅供参考）：\n" + "\n".join(lines)
