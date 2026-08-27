# -*- coding: utf-8 -*-
"""LLM function-calling 工具：只包 BackendAPI / 诊断 / 冲突 / 配置。"""

from __future__ import annotations

import json
import os
import re

from mclauncher import mods as mods_mod
from mclauncher.config import CONFIG
from mclauncher.downloader import DownloadManager
from mclauncher.instances import Instance, unique_instance_name
from mclauncher.mods import detect_loader, detect_mc_version

from . import conflict as conflict_mod
from . import diagnose as diagnose_mod
from . import knowledge as knowledge_mod
from . import memory as memory_mod
from . import modconfig as modconfig_mod
from .defaults import MAX_TOOL_RESULT, WRITE_TOOLS


def _notify_ui(backend):
    """两个后端的 ui_changed 通知方式不同：Qt 门面是 Signal，桥接层走事件总线。
    以前直接 backend.ui_changed.emit()，在桥后端（EziApp/WinUI/WPF 的 AI）
    必抛 AttributeError——工具已生效却报失败。"""
    sig = getattr(backend, "ui_changed", None)
    if hasattr(sig, "emit"):
        sig.emit()
        return
    emitter = getattr(backend, "_emit", None)
    if callable(emitter):
        emitter("ui_changed", {})


def _schema(name, desc, props, required=None):
    params = {
        "type": "object",
        "properties": props,
    }
    if required:
        params["required"] = required
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": desc,
            "parameters": params,
        },
    }


TOOL_SCHEMAS = [
    _schema("ask_user",
            "向用户弹出结构化选择题。需要用户选实例、加载器、搜到多个结果、冲突留哪个时必须用这个，不要只在文字里问。"
            "界面会自动加「其他」让用户自己填。没选完之前不要调用 install_*。"
            "用户选完后你必须立刻再调对应工具，不能结束。",
            {
                "title": {"type": "string", "description": "可选，整组题的小标题"},
                "prompt": {"type": "string", "description": "单题时的问题"},
                "allow_multiple": {"type": "boolean", "description": "单题时是否可多选，默认 false"},
                "options": {
                    "type": "array",
                    "description": "单题选项。每项可以是字符串，或 {id, label}",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "label": {"type": "string"},
                        },
                    },
                },
                "questions": {
                    "type": "array",
                    "description": "多题时用。每题 {id, prompt, allow_multiple, options:[{id,label}]}，至少 2 个选项",
                    "items": {"type": "object"},
                },
            }),
    _schema("get_launcher_state", "查看实例、已装版本、Java、模组数量等当前状态", {}),
    _schema("list_instances", "列出全部实例", {}),
    _schema("list_installed_versions", "列出某实例已安装的游戏版本", {
        "instance": {"type": "string", "description": "实例名，空则用默认"},
    }),
    _schema("search_versions", "搜索可下载的 Minecraft 版本号", {
        "query": {"type": "string", "description": "如 1.20.1 或 25w"},
        "kind": {"type": "string", "description": "release / snapshot / all"},
    }, ["query"]),
    _schema("search_mods",
            "搜索模组（支持中文名）。同一轮用户请求只调用一次；搜完必须 ask_user 让用户选，禁止换词再搜。", {
        "query": {"type": "string"},
        "source": {"type": "string", "description": "全部 / Modrinth / CurseForge"},
    }, ["query"]),
    _schema("search_modpacks",
            "搜索整合包（支持中文名）。同一轮只调用一次，搜完 ask_user，禁止换词再搜。", {
        "query": {"type": "string"},
        "source": {"type": "string", "description": "全部 / Modrinth / CurseForge"},
    }, ["query"]),
    _schema("list_mods", "列出实例已装模组（含禁用）", {
        "instance": {"type": "string"},
    }),
    _schema("install_game",
            "真正开始下载/安装 Minecraft。用户已选定版本后必须调用这个，否则不会下载。"
            "纯原版 loader 填「无」。", {
        "version": {"type": "string", "description": "如 1.20.1"},
        "loader": {"type": "string", "description": "无 / Fabric / Forge / Quilt / NeoForge。纯原版必须填 无"},
        "loader_version": {"type": "string"},
        "instance": {"type": "string"},
    }, ["version"]),
    _schema("install_mod", "安装模组。优先传搜索结果里的 slug 或 id", {
        "name": {"type": "string", "description": "显示名或 slug"},
        "instance": {"type": "string"},
        "source": {"type": "string"},
        "slug": {"type": "string"},
        "id": {"type": "string", "description": "CurseForge 数字 id"},
    }, ["name"]),
    _schema("install_modpack", "安装整合包。建议先 create_instance", {
        "name": {"type": "string"},
        "instance": {"type": "string"},
        "source": {"type": "string"},
        "slug": {"type": "string"},
        "id": {"type": "string"},
    }, ["name"]),
    _schema("install_shader", "安装光影包", {
        "name": {"type": "string"}, "instance": {"type": "string"},
        "source": {"type": "string"}, "slug": {"type": "string"},
    }, ["name"]),
    _schema("install_resourcepack", "安装资源包", {
        "name": {"type": "string"}, "instance": {"type": "string"},
        "source": {"type": "string"}, "slug": {"type": "string"},
    }, ["name"]),
    _schema("install_datapack", "安装数据包", {
        "name": {"type": "string"}, "instance": {"type": "string"},
        "source": {"type": "string"}, "slug": {"type": "string"},
    }, ["name"]),
    _schema("create_instance", "新建隔离实例，装整合包前建议先建", {
        "name": {"type": "string"},
    }, ["name"]),
    _schema("delete_instance", "删除整个实例（危险）", {
        "name": {"type": "string"},
    }, ["name"]),
    _schema("delete_mod", "删除模组文件", {
        "filename": {"type": "string"}, "instance": {"type": "string"},
    }, ["filename"]),
    _schema("disable_mod", "禁用模组（改名为 .disabled，可恢复）", {
        "filename": {"type": "string"}, "instance": {"type": "string"},
    }, ["filename"]),
    _schema("enable_mod", "重新启用已禁用模组", {
        "filename": {"type": "string"}, "instance": {"type": "string"},
    }, ["filename"]),
    _schema("get_task_status", "查询下载/安装任务的状态。install_* 返回 task_id 后，"
            "想知道装没装完、失败原因，就用这个查。不传 task_id 则列出全部近期任务", {
        "task_id": {"type": "string", "description": "install_* 返回的任务 id，可空"},
    }),
    _schema("check_mod_updates", "检查实例里已装模组有没有新版本（只查不装）", {
        "instance": {"type": "string"},
    }),
    _schema("update_mods", "把实例里能更新的模组更新到新版本。可用 filenames 只更新指定文件", {
        "instance": {"type": "string"},
        "filenames": {
            "type": "array", "items": {"type": "string"},
            "description": "只更新这些文件；空则全部有更新的都更",
        },
    }),
    _schema("repair_version", "重新校验并补齐某版本缺失/损坏的文件（jar、库、资源）", {
        "version": {"type": "string", "description": "如 1.20.1"},
        "instance": {"type": "string"},
    }, ["version"]),
    _schema("set_instance_memory", "设置游戏内存（MB）。传 version 则只改该版本，"
            "否则改启动器全局默认", {
        "memory_mb": {"type": "integer", "description": "如 4096"},
        "instance": {"type": "string"},
        "version": {"type": "string", "description": "可空；空则改全局默认内存"},
    }, ["memory_mb"]),
    _schema("set_instance_java", "指定实例使用哪个 Java（路径来自 get_java_list，"
            "传「自动选择」恢复自动）", {
        "java": {"type": "string", "description": "Java 可执行文件路径或「自动选择」"},
        "instance": {"type": "string"},
    }, ["java"]),
    _schema("list_loader_versions", "列出某 MC 版本可用的加载器版本号", {
        "mc_version": {"type": "string", "description": "如 1.20.1"},
        "loader": {"type": "string", "description": "Fabric / Forge / Quilt / NeoForge"},
    }, ["mc_version", "loader"]),
    _schema("get_accounts", "查看已登录账号（只读：名字、类型、当前使用哪个；不含令牌）", {}),
    _schema("search_help", "检索启动器内置帮助/常见问题（启动失败、Java 选择、"
            "联机、账号这类启动器用法问题先查这里）", {
        "query": {"type": "string"},
    }, ["query"]),
    _schema("wiki_lookup", "查 Minecraft Wiki（游戏玩法/物品/机制问题用这个，"
            "不要凭记忆编游戏知识）", {
        "query": {"type": "string", "description": "如 下界合金 / 附魔台"},
    }, ["query"]),
    _schema("get_java_list", "列出已安装 Java", {}),
    _schema("download_java", "下载 Adoptium Java", {
        "major": {"type": "string", "description": "8 / 11 / 17 / 21"},
    }, ["major"]),
    _schema("launch_game", "启动游戏。不填则用默认实例和已装版本", {
        "instance": {"type": "string"},
        "version": {"type": "string"},
        "username": {"type": "string"},
        "memory_mb": {"type": "integer"},
    }),
    _schema("diagnose_launch", "分析启动失败：规则扫 latest.log 和崩溃报告", {
        "instance": {"type": "string"},
    }),
    _schema("get_latest_log", "读取 latest.log 末尾", {
        "instance": {"type": "string"},
    }),
    _schema("get_crash_report", "读取最新崩溃报告", {
        "instance": {"type": "string"},
    }),
    _schema("scan_mod_conflicts", "扫描模组冲突、缺依赖、加载器不匹配", {
        "instance": {"type": "string"},
    }),
    _schema("inspect_mod", "解析单个模组 jar 的元数据", {
        "filename": {"type": "string"}, "instance": {"type": "string"},
    }, ["filename"]),
    _schema("list_mod_configs", "列出实例 config 下的配置文件", {
        "instance": {"type": "string"},
        "prefix": {"type": "string", "description": "子目录或文件名前缀"},
    }),
    _schema("read_mod_config", "读取某个配置文件", {
        "path": {"type": "string", "description": "相对 config/ 的路径"},
        "instance": {"type": "string"},
    }, ["path"]),
    _schema("write_mod_config", "写入配置文件（会先备份 .bak）", {
        "path": {"type": "string"},
        "content": {"type": "string", "description": "完整文件内容"},
        "instance": {"type": "string"},
    }, ["path", "content"]),
]


def is_write_tool(name: str) -> bool:
    return name in WRITE_TOOLS


def is_ask_tool(name: str) -> bool:
    return name == "ask_user"


def normalize_ask_args(args: dict) -> list[dict]:
    """统一成 [{id, prompt, allow_multiple, options:[{id,label}]}]，「其他」永远在最后。"""
    raw = (args or {}).get("questions")
    if not raw:
        raw = [{
            "id": (args or {}).get("id") or "q1",
            "prompt": (args or {}).get("prompt") or (args or {}).get("title") or "请选择",
            "allow_multiple": bool((args or {}).get("allow_multiple")),
            "options": (args or {}).get("options") or [],
        }]
    out = []
    for i, q in enumerate(raw if isinstance(raw, list) else [raw]):
        if not isinstance(q, dict):
            continue
        opts = []
        for j, o in enumerate(q.get("options") or []):
            if isinstance(o, str):
                oid, label = f"opt_{j}", o.strip()
            elif isinstance(o, dict):
                oid = str(o.get("id") or f"opt_{j}")
                label = str(o.get("label") or o.get("name") or oid).strip()
            else:
                continue
            if not label:
                continue
            opts.append({"id": oid, "label": label})
        has_other = any(
            x["id"] == "other" or x["label"].rstrip("。.") in ("其他", "其它")
            for x in opts
        )
        if not has_other:
            opts.append({"id": "other", "label": "其他"})
        if len(opts) < 2:
            opts.insert(0, {"id": "skip", "label": "先不选"})
        out.append({
            "id": str(q.get("id") or f"q{i + 1}"),
            "prompt": str(q.get("prompt") or q.get("title") or "请选择"),
            "allow_multiple": bool(q.get("allow_multiple")),
            "options": opts,
        })
    if not out:
        out.append({
            "id": "q1",
            "prompt": str((args or {}).get("prompt") or "请选择"),
            "allow_multiple": False,
            "options": [
                {"id": "skip", "label": "先不选"},
                {"id": "other", "label": "其他"},
            ],
        })
    return out


def _clip(obj) -> str:
    if isinstance(obj, str):
        text = obj
    else:
        text = json.dumps(obj, ensure_ascii=False, indent=2)
    if len(text) > MAX_TOOL_RESULT:
        return text[:MAX_TOOL_RESULT] + "\n…(已截断)"
    return text


def _cjk(s: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in (s or ""))


def _inst_name(backend, args) -> str:
    name = (args.get("instance") or "").strip()
    return name or CONFIG.get("default_instance", "default") or "default"


def _inst(backend, args) -> Instance:
    return backend._instance(_inst_name(backend, args))


def _merge_cache(old, rows):
    out = list(old or [])
    seen = {(r.get("source"), r.get("id") or r.get("slug") or r.get("name")) for r in out}
    for r in rows or []:
        mark = (r.get("source"), r.get("id") or r.get("slug") or r.get("name"))
        if mark in seen:
            continue
        seen.add(mark)
        out.append(r)
    return out[-80:]


def _trim_hits(rows, n=12):
    out = []
    for r in rows[:n]:
        out.append({
            "name": r.get("name"),
            "source": r.get("source"),
            "slug": r.get("slug"),
            "id": r.get("id"),
            "author": r.get("author"),
            "downloads": r.get("downloads"),
            "description": (r.get("description") or "")[:160],
        })
    return out


def _count_mod_jars(inst: Instance) -> int:
    mods_dir = inst.path / "mods"
    n = 0
    try:
        with os.scandir(mods_dir) as it:
            for e in it:
                name = e.name.lower()
                if name.endswith(".jar") or name.endswith(".jar.disabled") or name.endswith(".disabled"):
                    n += 1
    except OSError:
        return 0
    return n


def _machine_context() -> list[str]:
    """本机硬件一行 + 内存详情：OOM/驱动类诊断没有这个只能瞎猜。"""
    try:
        from mclauncher import sysinfo
        info = sysinfo.collect(max_age=600)
    except Exception:
        return []
    lines = []
    if info.get("summary"):
        lines.append(f"本机: {info['summary']}")
    mem = info.get("memory") or {}
    total = int(mem.get("total_mb") or 0)
    if total:
        free = int(mem.get("free_mb") or 0)
        lines.append(f"物理内存: 共 {total}MB / 可用 {free}MB"
                     f"（启动器默认分配 {CONFIG.get('memory_mb') or 4096}MB）")
        memory_mod.remember_fact("机器内存MB", total)
    return lines


def _tasks_context(backend) -> list[str]:
    try:
        rows = backend.list_tasks() or []
    except Exception:
        return []
    running = [r for r in rows if r.get("status") == "running"]
    failed = [r for r in rows if r.get("status") == "failed"]
    lines = []
    if running:
        names = "、".join(str(r.get("title") or r.get("id")) for r in running[:5])
        lines.append(f"进行中的任务({len(running)}): {names}")
    if failed:
        last = failed[-1]
        lines.append(f"最近失败的任务: {last.get('title')}（{str(last.get('message') or '')[:120]}）")
    return lines


def _account_context(backend) -> list[str]:
    try:
        rows = backend.get_account_rows() or []
    except Exception:
        return []
    if not rows:
        return ["账号: 未登录（可用离线模式）"]
    active = next((r for r in rows if r.get("active")), None)
    kinds = {"offline": "离线", "microsoft": "微软正版"}
    if active:
        kind = kinds.get(active.get("type") or "", active.get("type") or "?")
        return [f"账号: 共 {len(rows)} 个，当前 {active.get('name')}（{kind}）"]
    return [f"账号: 共 {len(rows)} 个，未选中"]


def _ui_context_lines(backend) -> list[str]:
    lines = []
    ui = getattr(backend, "_ui_context", None) or {}
    if ui.get("source"):
        lines.append(f"用户从「{ui['source']}」发起了这次对话")
    if ui.get("page"):
        lines.append(f"用户当前所在页面: {ui['page']}")
    if ui.get("instance"):
        lines.append(f"页面上选中的实例: {ui['instance']}")
    prefs = getattr(backend, "_ui_launch", None) or {}
    if prefs.get("instance") or prefs.get("version"):
        lines.append(
            f"启动页当前选择: 实例 {prefs.get('instance') or '?'} / "
            f"版本 {prefs.get('version') or '?'} / 内存 {prefs.get('memory_mb') or '?'}MB")
    return lines


def runtime_context(backend) -> str:
    try:
        insts = backend.get_instances()
        default = CONFIG.get("default_instance", "default")
        javs = backend.get_java_list(False)
        lines = [f"默认实例: {default}", f"已装 Java: {len(javs)} 个"]
        for row in insts[:8]:
            name = row.get("name")
            n_mods = 0
            loader = None
            mc = row.get("mc_version") or None
            try:
                inst = backend._instance(name)
                n_mods = _count_mod_jars(inst)
                loader = detect_loader(inst)
                if not mc:
                    mc = detect_mc_version(inst)
            except Exception:
                pass
            lines.append(
                f"- {name}: MC {mc or '?'} / {loader or '原版'} / "
                f"版本{row.get('versions')}个 / 模组{n_mods}个"
                + (f" / 整合包 {row.get('pack')}" if row.get("pack") else "")
            )
        lines.extend(_machine_context())
        lines.extend(_account_context(backend))
        lines.extend(_tasks_context(backend))
        lines.extend(_ui_context_lines(backend))
        return "\n".join(lines)
    except Exception as exc:
        return f"状态读取失败: {exc}"


def exact_match_hit(rows, query: str) -> dict | None:
    """搜索结果里与用户查询「精确匹配且唯一」的那一条；没有则 None。

    命中条件：结果只有一条；或 name/slug 与查询词（大小写无关）相等的
    结果恰好一条。用来跳过没必要的 ask_user——用户已经点名要装什么了。
    """
    rows = [r for r in (rows or []) if isinstance(r, dict)]
    if not rows:
        return None
    if len(rows) == 1:
        return rows[0]
    q = (query or "").strip().lower()
    if not q:
        return None
    hits = [
        r for r in rows
        if (str(r.get("name") or "").strip().lower() == q
            or str(r.get("slug") or "").strip().lower() == q)
    ]
    return hits[0] if len(hits) == 1 else None


def _search_mods(backend, query, source):
    src = source or "全部"
    rows = []
    if _cjk(query):
        try:
            dm = DownloadManager(threads=2)
            hits = mods_mod.search_mods_chinese(
                dm, query, limit=20, api_key=CONFIG.get("curseforge_api_key"))
            for h in hits:
                rows.append({
                    "name": h.get("title") or h.get("name"),
                    "source": h.get("source"),
                    "slug": h.get("slug"),
                    "id": h.get("id"),
                    "author": h.get("author"),
                    "downloads": h.get("downloads"),
                    "description": h.get("description") or h.get("summary") or "",
                })
        except Exception:
            rows = []
    if rows:
        backend._mod_cache = _merge_cache(getattr(backend, "_mod_cache", None), rows)
        return _trim_hits(rows)
    if src in ("全部", "all", ""):
        seen = set()
        merged = []
        for s in ("Modrinth", "CurseForge"):
            for r in backend.search_mods(query, s):
                mark = (r.get("source"), r.get("id") or r.get("slug") or r.get("name"))
                if mark in seen:
                    continue
                seen.add(mark)
                merged.append(r)
        backend._mod_cache = _merge_cache(getattr(backend, "_mod_cache", None), merged)
        return _trim_hits(merged)
    rows = backend.search_mods(query, src)
    backend._mod_cache = _merge_cache(getattr(backend, "_mod_cache", None), rows)
    return _trim_hits(rows)


def _search_modpacks(backend, query, source):
    src = source or "全部"
    if src in ("全部", "all", ""):
        seen = set()
        merged = []
        for s in ("Modrinth", "CurseForge"):
            for r in backend.search_modpacks(query, s):
                mark = (r.get("source"), r.get("id") or r.get("slug") or r.get("name"))
                if mark in seen:
                    continue
                seen.add(mark)
                merged.append(r)
        return _trim_hits(merged)
    return _trim_hits(backend.search_modpacks(query, src))


def confirm_label(name: str, args: dict) -> str:
    inst = args.get("instance") or "默认实例"
    mapping = {
        "install_game": f"安装游戏 {args.get('version')} {args.get('loader') or ''} → {inst}",
        "install_mod": f"安装模组 {args.get('name')} → {inst}",
        "install_modpack": f"安装整合包 {args.get('name')} → {inst}",
        "install_shader": f"安装光影 {args.get('name')} → {inst}",
        "install_resourcepack": f"安装资源包 {args.get('name')} → {inst}",
        "install_datapack": f"安装数据包 {args.get('name')} → {inst}",
        "download_java": f"下载 Java {args.get('major')}",
        "launch_game": f"启动 {args.get('version') or '当前版本'} @ {inst}",
        "create_instance": f"新建实例 {args.get('name')}",
        "delete_instance": f"删除实例 {args.get('name')}（不可恢复）",
        "delete_mod": f"删除模组 {args.get('filename')} @ {inst}",
        "disable_mod": f"禁用模组 {args.get('filename')} @ {inst}",
        "enable_mod": f"启用模组 {args.get('filename')} @ {inst}",
        "write_mod_config": f"改配置 {args.get('path')} @ {inst}",
        "update_mods": f"更新模组 @ {inst}",
        "repair_version": f"修复版本 {args.get('version')} @ {inst}",
        "set_instance_memory": (
            f"内存改为 {args.get('memory_mb')}MB"
            + (f"（版本 {args.get('version')}）" if args.get("version") else "（全局默认）")),
        "set_instance_java": f"指定 Java: {args.get('java')} @ {inst}",
        "ask_user": (args.get("prompt") or args.get("title") or "请选择"),
    }
    return mapping.get(name, f"执行 {name}")


def execute_tool(backend, name: str, args: dict, wait=True, cancelled=None):
    args = args or {}
    inst_name = _inst_name(backend, args)

    if name == "ask_user":
        return "ask_user 由界面处理"
    if name == "get_launcher_state":
        return runtime_context(backend)
    if name == "list_instances":
        return backend.get_instances()
    if name == "list_installed_versions":
        return backend.get_installed_versions(inst_name)
    if name == "search_versions":
        q = (args.get("query") or "").lower()
        kind = (args.get("kind") or "all").lower()
        rows = backend.get_version_list() or []
        if not rows:
            rows = backend.fetch_version_list()
        out = []
        for r in rows:
            vid = str(r.get("version") or "")
            if q and q not in vid.lower():
                continue
            if kind not in ("", "all") and (r.get("type") or "") != kind:
                if not (kind == "release" and r.get("type") == "release"):
                    continue
            out.append(r)
            if len(out) >= 20:
                break
        return out or "没有匹配版本"
    if name == "search_mods":
        return _search_mods(backend, args.get("query") or "", args.get("source") or "全部")
    if name == "search_modpacks":
        rows = _search_modpacks(backend, args.get("query") or "", args.get("source") or "全部")
        backend._pack_cache = _merge_cache(getattr(backend, "_pack_cache", None), rows if isinstance(rows, list) else [])
        return rows
    if name == "list_mods":
        return mods_mod.list_instance_mod_entries(backend._instance(inst_name))
    if name == "install_game":
        tid = backend.install_game(
            args.get("version"), args.get("loader") or "无",
            args.get("loader_version") or "", inst_name)
        memory_mod.record_event(
            "install_game", version=args.get("version"),
            loader=args.get("loader"), instance=inst_name)
        return backend.wait_task(tid, cancelled=cancelled) if wait else {"task_id": tid, "queued": True}
    if name == "install_mod":
        extra = {
            "instance": inst_name,
            "source": args.get("source") or "",
            "slug": args.get("slug") or "",
            "id": args.get("id") or "",
            "name": args.get("name"),
        }
        tid = backend.install_mod(args.get("name"), inst_name, extra)
        memory_mod.record_event("install_mod", name=args.get("name"), instance=inst_name)
        return backend.wait_task(tid, cancelled=cancelled) if wait else {"task_id": tid, "queued": True}
    if name == "install_modpack":
        extra = {
            "instance": inst_name,
            "source": args.get("source") or "",
            "slug": args.get("slug") or "",
            "id": args.get("id") or "",
            "name": args.get("name"),
        }
        src = extra.get("source") or "Modrinth"
        tid = backend.install_modpack(args.get("name"), src, extra)
        memory_mod.record_event("install_modpack", name=args.get("name"), instance=inst_name)
        return backend.wait_task(tid, cancelled=cancelled) if wait else {"task_id": tid, "queued": True}
    if name == "install_shader":
        extra = {"instance": inst_name, "source": args.get("source") or "", "slug": args.get("slug") or ""}
        tid = backend.install_shader(args.get("name"), inst_name, extra)
        return backend.wait_task(tid, cancelled=cancelled) if wait else {"task_id": tid, "queued": True}
    if name == "install_resourcepack":
        extra = {"instance": inst_name, "source": args.get("source") or "", "slug": args.get("slug") or ""}
        tid = backend.install_resourcepack(args.get("name"), inst_name, extra)
        return backend.wait_task(tid, cancelled=cancelled) if wait else {"task_id": tid, "queued": True}
    if name == "install_datapack":
        extra = {"instance": inst_name, "source": args.get("source") or "", "slug": args.get("slug") or ""}
        tid = backend.install_datapack(args.get("name"), inst_name, extra)
        out = backend.wait_task(tid, cancelled=cancelled) if wait else {"task_id": tid, "queued": True}
        note = "数据包在实例 datapacks 目录。进游戏后还要拷进对应存档的 datapacks 才会生效。"
        if isinstance(out, dict):
            out["hint"] = note
            return out
        return f"{out}\n{note}"
    if name == "create_instance":
        raw = args.get("name") or "游戏"
        inst = Instance(unique_instance_name(raw))
        inst.create()
        _notify_ui(backend)
        memory_mod.record_event("create_instance", instance=inst.name)
        return f"已创建实例 {inst.name}"
    if name == "delete_instance":
        backend.delete_instance(args.get("name"))
        return f"已删除实例 {args.get('name')}"
    if name == "delete_mod":
        backend.delete_mod(inst_name, args.get("filename"))
        return f"已删除 {args.get('filename')}"
    if name == "disable_mod":
        # 走后端公开方法：两个后端都有，且各自负责发 ui_changed
        new = backend.disable_mod(inst_name, args.get("filename"))
        return f"已禁用 → {new}"
    if name == "enable_mod":
        new = backend.enable_mod(inst_name, args.get("filename"))
        return f"已启用 → {new}"
    if name == "get_task_status":
        rows = backend.list_tasks() or []
        tid = (args.get("task_id") or "").strip()
        if tid:
            hit = next((r for r in rows if str(r.get("id")) == tid), None)
            return hit or f"没有找到任务 {tid}（可能已过期）"
        return rows or "当前没有任务"
    if name == "check_mod_updates":
        from mclauncher import mod_update
        inst = backend._instance(inst_name)
        rows = mod_update.check_updates(
            inst, DownloadManager(threads=4),
            mc_version=detect_mc_version(inst) or "",
            loader=(detect_loader(inst) or "").lower())
        if not rows:
            return "所有模组都是最新的（或来源无法识别）"
        return [{
            "filename": r.get("filename"),
            "name": r.get("name"),
            "current": r.get("current"),
            "latest": r.get("latest"),
            "source": r.get("source"),
        } for r in rows]
    if name == "update_mods":
        from mclauncher import mod_update
        inst = backend._instance(inst_name)
        rows = mod_update.check_updates(
            inst, DownloadManager(threads=4),
            mc_version=detect_mc_version(inst) or "",
            loader=(detect_loader(inst) or "").lower())
        want = {str(f).strip() for f in (args.get("filenames") or []) if str(f).strip()}
        if want:
            rows = [r for r in rows if r.get("filename") in want]
        if not rows:
            return "没有需要更新的模组"
        done, failed = [], []
        for row in rows:
            if cancelled and cancelled():
                break
            try:
                new_name = mod_update.apply_update(inst, row)
                done.append(f"{row.get('name')} {row.get('current')} → {row.get('latest')}（{new_name}）")
            except Exception as exc:
                failed.append(f"{row.get('name')}: {exc}")
        _notify_ui(backend)
        out = ""
        if done:
            out += "已更新：\n" + "\n".join(done)
        if failed:
            out += ("\n" if out else "") + "失败：\n" + "\n".join(failed)
        return out or "没有可更新的模组"
    if name == "repair_version":
        tid = backend.repair_version(inst_name, args.get("version") or "")
        return backend.wait_task(tid, cancelled=cancelled) if wait else {"task_id": tid, "queued": True}
    if name == "set_instance_memory":
        mb = int(args.get("memory_mb") or 0)
        if mb < 512 or mb > 65536:
            return "内存要在 512 ~ 65536 MB 之间"
        version = (args.get("version") or "").strip()
        if version:
            data = backend.get_version_settings(inst_name, version) or {}
            data["memory_mb"] = mb
            backend.save_version_settings(inst_name, version, data)
            return f"已把 {inst_name}/{version} 的内存改为 {mb}MB"
        CONFIG.set("memory_mb", mb)
        CONFIG.save()
        _notify_ui(backend)
        return f"已把全局默认内存改为 {mb}MB"
    if name == "set_instance_java":
        backend.set_instance_java(inst_name, args.get("java") or "自动选择")
        return f"已为实例 {inst_name} 指定 Java: {args.get('java')}"
    if name == "list_loader_versions":
        rows = backend.list_loader_versions(
            args.get("mc_version") or "", args.get("loader") or "") or []
        return rows[:15] or "没有可用的加载器版本"
    if name == "get_accounts":
        rows = backend.get_account_rows() or []
        # 只给只读概要，绝不带令牌/uuid 之类敏感字段
        return [{
            "name": r.get("name"),
            "type": r.get("type"),
            "active": bool(r.get("active")),
        } for r in rows] or "还没有登录账号（可用离线模式）"
    if name == "search_help":
        return knowledge_mod.search_help(args.get("query") or "")
    if name == "wiki_lookup":
        return knowledge_mod.wiki_lookup(args.get("query") or "")
    if name == "get_java_list":
        return backend.get_java_list(False)
    if name == "download_java":
        tid = backend.download_java(str(args.get("major")))
        return backend.wait_task(tid, cancelled=cancelled) if wait else {"task_id": tid, "queued": True}
    if name == "launch_game":
        prefs = getattr(backend, "_ui_launch", None) or {}
        versions = backend.get_installed_versions(inst_name)
        version = args.get("version") or prefs.get("version") or (versions[-1] if versions else "")
        accounts = backend.get_accounts()
        account = args.get("account") or prefs.get("account") or ""
        if not account:
            ms = [a for a in accounts if a and a != "离线模式"]
            account = ms[0] if ms else "离线模式"
        username = args.get("username") or prefs.get("username") or "Player"
        memory = int(args.get("memory_mb") or prefs.get("memory_mb") or CONFIG.get("memory_mb") or 4096)
        width = int(prefs.get("width") or CONFIG.get("width") or 854)
        height = int(prefs.get("height") or CONFIG.get("height") or 480)
        java = prefs.get("java") or "自动选择"
        inst = args.get("instance") or prefs.get("instance") or inst_name
        tid = backend.launch_game(
            inst, version, account, username, memory, width, height, java,
        )
        memory_mod.record_event("launch_game", version=version, instance=inst)
        return f"已开始启动 {version}（任务 {tid}），日志在「启动」页"
    if name == "diagnose_launch":
        out = diagnose_mod.diagnose(_inst(backend, args))
        # 旁路给 UI 渲染一键修复卡：工具结果字符串可能被截断，不能靠它传结构
        try:
            backend._last_diagnose = {
                "actions": list(out.get("actions") or []),
                "report": {
                    "instance": out.get("instance") or inst_name,
                    "version": out.get("version") or "",
                    "direct_file": out.get("crash_path") or "",
                },
            }
        except Exception:
            pass
        return out
    if name == "get_latest_log":
        return diagnose_mod.log_excerpt(_inst(backend, args), "latest")
    if name == "get_crash_report":
        return diagnose_mod.log_excerpt(_inst(backend, args), "crash")
    if name == "scan_mod_conflicts":
        return conflict_mod.scan_conflicts(_inst(backend, args))
    if name == "inspect_mod":
        inst = _inst(backend, args)
        mods_dir = (inst.path / "mods").resolve()
        p = (mods_dir / (args.get("filename") or "")).resolve()
        if p.parent != mods_dir or not p.is_file():
            return f"找不到 {args.get('filename')}"
        return conflict_mod.inspect_jar(p)
    if name == "list_mod_configs":
        return modconfig_mod.list_configs(_inst(backend, args), args.get("prefix") or "")
    if name == "read_mod_config":
        return modconfig_mod.read_config(_inst(backend, args), args.get("path"))
    if name == "write_mod_config":
        return modconfig_mod.write_config(
            _inst(backend, args), args.get("path"), args.get("content") or "")
    return f"未知工具: {name}"


def run_tool(backend, name: str, raw_args, wait=True, cancelled=None) -> str:
    if isinstance(raw_args, str):
        try:
            args = json.loads(raw_args or "{}")
        except json.JSONDecodeError:
            args = {}
    else:
        args = dict(raw_args or {})
    try:
        result = execute_tool(backend, name, args, wait=wait, cancelled=cancelled)
        return _clip(result)
    except Exception as exc:  # noqa: BLE001
        return f"工具失败: {exc}"


def parse_args(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        # 模型有时吐出 key=value
        out = {}
        for m in re.finditer(r'"(\w+)"\s*:\s*"([^"]*)"', raw):
            out[m.group(1)] = m.group(2)
        return out
