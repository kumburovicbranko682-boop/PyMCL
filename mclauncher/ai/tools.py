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
from . import modconfig as modconfig_mod
from .defaults import MAX_TOOL_RESULT, WRITE_TOOLS


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
        return "\n".join(lines)
    except Exception as exc:
        return f"状态读取失败: {exc}"


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
    """确认卡片 / 工具状态行上给用户看的文案，必须跟随界面语言。"""
    from mclauncher.i18n import tr
    inst = args.get("instance") or tr("默认实例")
    mapping = {
        "install_game": tr("安装游戏 {0} {1} → {2}").format(
            args.get("version"), args.get("loader") or "", inst),
        "install_mod": tr("安装模组 {0} → {1}").format(args.get("name"), inst),
        "install_modpack": tr("安装整合包 {0} → {1}").format(args.get("name"), inst),
        "install_shader": tr("安装光影 {0} → {1}").format(args.get("name"), inst),
        "install_resourcepack": tr("安装资源包 {0} → {1}").format(args.get("name"), inst),
        "install_datapack": tr("安装数据包 {0} → {1}").format(args.get("name"), inst),
        "download_java": tr("下载 Java {0}").format(args.get("major")),
        "launch_game": tr("启动 {0} @ {1}").format(
            args.get("version") or tr("当前版本"), inst),
        "create_instance": tr("新建实例 {0}").format(args.get("name")),
        "delete_instance": tr("删除实例 {0}（不可恢复）").format(args.get("name")),
        "delete_mod": tr("删除模组 {0} @ {1}").format(args.get("filename"), inst),
        "disable_mod": tr("禁用模组 {0} @ {1}").format(args.get("filename"), inst),
        "enable_mod": tr("启用模组 {0} @ {1}").format(args.get("filename"), inst),
        "write_mod_config": tr("改配置 {0} @ {1}").format(args.get("path"), inst),
        "ask_user": (args.get("prompt") or args.get("title") or tr("请选择")),
    }
    return mapping.get(name, tr("执行 {0}").format(name))


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
        backend.ui_changed.emit()
        return f"已创建实例 {inst.name}"
    if name == "delete_instance":
        backend.delete_instance(args.get("name"))
        return f"已删除实例 {args.get('name')}"
    if name == "delete_mod":
        backend.delete_mod(inst_name, args.get("filename"))
        return f"已删除 {args.get('filename')}"
    if name == "disable_mod":
        new = mods_mod.set_mod_enabled(backend._instance(inst_name), args.get("filename"), False)
        backend.ui_changed.emit()
        return f"已禁用 → {new}"
    if name == "enable_mod":
        new = mods_mod.set_mod_enabled(backend._instance(inst_name), args.get("filename"), True)
        backend.ui_changed.emit()
        return f"已启用 → {new}"
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
        return f"已开始启动 {version}（任务 {tid}），日志在「启动」页"
    if name == "diagnose_launch":
        return diagnose_mod.diagnose(_inst(backend, args))
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
