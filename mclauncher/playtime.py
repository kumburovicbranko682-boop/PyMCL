# -*- coding: utf-8 -*-
"""游戏时长统计：按实例/版本累计游玩时间。

数据存储在 launcher_root/playtime.json 中，结构：
{
  "instances": {
    "default": {
      "total": 3600,          # 总秒数
      "versions": {
        "1.20.1": 1800,
        "1.21": 1800
      },
      "sessions": [
        {"start": 1700000000, "duration": 600, "version": "1.20.1"}
      ]
    }
  }
}
"""
from __future__ import annotations

import time
from pathlib import Path
from threading import Lock
from typing import Optional

from . import utils
from .config import CONFIG

PLAYTIME_FILE = "playtime.json"
_lock = Lock()


def _path() -> Path:
    return utils.ROOT / PLAYTIME_FILE


def _load() -> dict:
    return utils.read_json(_path(), {"instances": {}}) or {"instances": {}}


def _save(data: dict):
    with _lock:
        _write_safe(data)


def _write_safe(data: dict):
    utils.write_json(_path(), data)


def _ensure_instance(data: dict, instance_name: str) -> dict:
    if instance_name not in data["instances"]:
        data["instances"][instance_name] = {
            "total": 0,
            "versions": {},
            "sessions": [],
        }
    return data["instances"][instance_name]


def record_session(instance_name: str, version_id: str, duration: int):
    """记录一次游戏会话。duration 为秒数。"""
    if duration <= 0:
        return
    data = _load()
    inst = _ensure_instance(data, instance_name)
    inst["total"] = inst.get("total", 0) + duration
    versions = inst.get("versions", {})
    versions[version_id] = versions.get(version_id, 0) + duration
    inst["versions"] = versions
    sessions = inst.get("sessions", [])
    sessions.append({
        "start": int(time.time()) - duration,
        "duration": duration,
        "version": version_id,
    })
    # 保留最近 500 条会话记录
    if len(sessions) > 500:
        sessions = sessions[-500:]
    inst["sessions"] = sessions
    _save(data)


def get_playtime(instance_name: str) -> dict:
    """获取某个实例的时长统计。

    返回: {total, versions: {version_id: seconds}, sessions: [...]}
    """
    data = _load()
    inst = data.get("instances", {}).get(instance_name)
    if not inst:
        return {"total": 0, "versions": {}, "sessions": []}
    return {
        "total": inst.get("total", 0),
        "versions": dict(inst.get("versions", {})),
        "sessions": list(inst.get("sessions", [])),
    }


def get_all_playtime() -> dict:
    """获取所有实例的时长统计。"""
    data = _load()
    return data.get("instances", {})


def get_total_playtime() -> int:
    """获取所有实例的总游戏时长。"""
    data = _load()
    total = 0
    for inst in data.get("instances", {}).values():
        total += inst.get("total", 0)
    return total


def version_stats(instance_name: str) -> dict:
    """每版本统计（HMCL 游戏列表同款）：{版本id: {"seconds": 累计秒, "last": 最近一次游玩结束时间戳}}。

    "last" 从会话记录推导（只保留最近 500 条，够覆盖「最近玩过什么」）；
    累计秒数用 versions 总账，不受会话裁剪影响。
    """
    data = _load()
    inst = data.get("instances", {}).get(instance_name) or {}
    out = {}
    for vid, secs in (inst.get("versions") or {}).items():
        out[vid] = {"seconds": int(secs or 0), "last": 0}
    for s in inst.get("sessions") or []:
        vid = s.get("version") or "?"
        end = int(s.get("start") or 0) + int(s.get("duration") or 0)
        row = out.setdefault(vid, {"seconds": int(s.get("duration") or 0), "last": 0})
        if end > row["last"]:
            row["last"] = end
    return out


def format_last_played(epoch: int, now=None) -> str:
    """上次游玩的相对时间：刚刚 / n 分钟前 / n 小时前 / n 天前 / 具体日期。"""
    if not epoch or epoch <= 0:
        return ""
    now = time.time() if now is None else now
    diff = max(0, int(now) - int(epoch))
    if diff < 60:
        return "刚刚"
    if diff < 3600:
        return f"{diff // 60} 分钟前"
    if diff < 86400:
        return f"{diff // 3600} 小时前"
    if diff < 30 * 86400:
        return f"{diff // 86400} 天前"
    return time.strftime("%Y-%m-%d", time.localtime(int(epoch)))


def format_duration(seconds: int) -> str:
    """将秒数格式化为人类可读的时长。"""
    if seconds < 0:
        seconds = 0
    hours = seconds // 3600
    mins = (seconds % 3600) // 60
    secs = seconds % 60
    if hours > 0:
        return f"{hours} 小时 {mins} 分钟"
    if mins > 0:
        return f"{mins} 分钟 {secs} 秒"
    return f"{secs} 秒"


def clear_playtime(instance_name: str = "", version_id: str = ""):
    """清除时长统计。instance_name 为空则清除全部。"""
    data = _load()
    if not instance_name:
        data["instances"] = {}
    elif instance_name in data.get("instances", {}):
        if version_id:
            inst = data["instances"][instance_name]
            inst["total"] = 0
            inst["versions"] = {}
            # 重新计算
            sessions = inst.get("sessions", [])
            remaining = []
            for s in sessions:
                if s.get("version") != version_id:
                    remaining.append(s)
                    inst["total"] += s.get("duration", 0)
                    ver = s.get("version", "?")
                    inst["versions"][ver] = inst["versions"].get(ver, 0) + s.get("duration", 0)
            inst["sessions"] = remaining
        else:
            del data["instances"][instance_name]
    _save(data)


class PlaytimeTracker:
    """游戏时长追踪器，用于 GameProcess 外部包裹。"""

    def __init__(self, instance_name: str, version_id: str):
        self.instance_name = instance_name
        self.version_id = version_id
        self._started = 0.0

    def start(self):
        self._started = time.time()

    def stop(self) -> int:
        """停止计时并记录。返回本次游玩秒数。"""
        if self._started <= 0:
            return 0
        duration = int(time.time() - self._started)
        if duration > 0:
            record_session(self.instance_name, self.version_id, duration)
        return duration