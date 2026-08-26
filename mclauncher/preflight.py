# -*- coding: utf-8 -*-
"""启动前预检：磁盘 / 版本文件 / 库与资源 / mods 冲突 / Java，对齐 PCL「先查再启」。"""
from __future__ import annotations

import shutil
from pathlib import Path

from . import java as java_mod
from . import utils
from .instances import Instance

# 预检扫库/资源时的上限，避免大整合包卡死 UI
_MAX_LIB_CHECK = 400
_MAX_ASSET_SAMPLE = 80
_MAX_MOD_JARS = 120


def check_launch(instance, version: str, *, memory_mb: int = 0, java_exe: str = "") -> dict:
    """返回 {ok, items:[{level, code, title, detail}, ...]}。ok=False 表示有 error 级项。"""
    items: list[dict] = []
    # 接受 Instance，也接受测试/桥接里的 duck-type（有 .path 即可）
    if isinstance(instance, Instance):
        inst = instance
    elif hasattr(instance, "path") and getattr(instance, "path", None) is not None:
        inst = instance
    else:
        inst = Instance(str(instance))
    version = (version or "").strip()
    root = Path(inst.path)

    if not root.is_dir():
        items.append(_item("error", "no_instance", "实例目录不存在", str(root)))
        return {"ok": False, "items": items}

    try:
        probe = root / ".pymcl_write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
    except OSError as exc:
        items.append(_item("error", "not_writable", "实例目录不可写", str(exc)))

    resolved = None
    if not version:
        items.append(_item("error", "no_version", "未选择版本", "请先到「下载 → 原版游戏」安装版本"))
    else:
        vjson = inst.version_json(version)
        if not vjson:
            items.append(_item("error", "no_version_json", "版本未安装", f"找不到 {version} 的版本 JSON"))
        else:
            jar = root / "versions" / version / f"{version}.jar"
            if hasattr(inst, "versions_dir"):
                try:
                    jar = Path(inst.versions_dir()) / version / f"{version}.jar"
                except Exception:
                    pass
            if not Path(jar).is_file():
                items.append(_item("warn", "no_client_jar", "客户端 jar 未直接找到",
                                   f"{jar}（若为继承版本，启动时会解析父版本）"))
            try:
                from . import manifest as manifest_mod
                resolved = manifest_mod.resolve_inherits(
                    vjson, lambda pid: inst.version_json(pid))
            except Exception:
                resolved = vjson

    try:
        usage = shutil.disk_usage(str(root))
        free_mb = usage.free // (1024 * 1024)
        if free_mb < 512:
            items.append(_item("error", "disk_low", "磁盘空间不足",
                               f"实例所在盘仅剩约 {free_mb} MB，至少需要 512 MB"))
        elif free_mb < 2048:
            items.append(_item("warn", "disk_warn", "磁盘空间偏低",
                               f"实例所在盘剩余约 {free_mb} MB，建议清理后再装大整合包"))
    except OSError:
        pass

    mods_path = _resolve_mods_dir(inst, version)
    if mods_path.is_dir():
        unzipped = [p.name for p in mods_path.iterdir()
                    if p.is_dir() and not p.name.startswith(".")]
        if unzipped:
            items.append(_item(
                "error", "mod_unzipped", "Mods 被解压成了文件夹",
                "直接放整个 .jar/.zip 即可。请删掉这些文件夹：\n - " + "\n - ".join(unzipped[:12])))
        jars = [p.name for p in mods_path.iterdir() if p.suffix.lower() == ".jar"]
        looks_loader = any(tok in version.lower() for tok in (
            "forge", "fabric", "quilt", "neoforge", "optifine", "liteloader"))
        if jars and version and not looks_loader:
            items.append(_item(
                "warn", "vanilla_mods", "原版版本不会加载模组",
                f"mods 里有 {len(jars)} 个 jar，但当前版本名像原版。请安装 Fabric/Forge 等加载器。"))

    if resolved and version:
        _check_libraries(inst, resolved, items)
        _check_assets(inst, resolved, items)
        _check_natives(inst, version, resolved, items)

    if mods_path.is_dir() and version:
        _check_mod_conflicts(inst, mods_path, items)

    if java_exe and java_exe not in ("自动选择", "auto", "default", ""):
        p = Path(java_exe)
        if not p.is_file():
            items.append(_item("error", "java_missing", "指定的 Java 不存在", java_exe))
        elif version:
            need_src = resolved or inst.version_json(version) or {}
            need = java_mod.required_java_major(need_src)
            got = java_mod.get_java_major(str(p))
            if need and got and int(got) < int(need):
                items.append(_item(
                    "error", "java_too_old", f"Java 版本过低（需要 {need}+）",
                    f"当前是 Java {got}：{p}"))

    mem = int(memory_mb or 0)
    if mem > 0:
        try:
            import ctypes
            if utils.IS_WINDOWS:
                class MEMORYSTATUSEX(ctypes.Structure):
                    _fields_ = [
                        ("dwLength", ctypes.c_ulong),
                        ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong),
                        ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong),
                        ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong),
                        ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                    ]
                stat = MEMORYSTATUSEX()
                stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
                if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                    avail_mb = int(stat.ullAvailPhys // (1024 * 1024))
                    if mem + 1024 > avail_mb:
                        items.append(_item(
                            "warn", "memory_high", "分配内存接近可用物理内存",
                            f"游戏 {mem} MB，系统当前可用约 {avail_mb} MB，可能触发交换卡顿"))
        except Exception:
            pass

    ok = not any(i["level"] == "error" for i in items)
    if not items:
        items.append(_item("ok", "ready", "预检通过", "未发现阻塞问题"))
    return {"ok": ok, "items": items}


def _resolve_mods_dir(inst: Instance, version: str) -> Path:
    if version:
        try:
            from . import version_settings as vs
            return Path(vs.mods_dir(inst, version))
        except Exception:
            pass
    return Path(inst.path) / "mods"


def _check_libraries(inst: Instance, resolved: dict, items: list[dict]) -> None:
    from .installer import select_native_classifier, subst_native_key

    libs_dir = Path(inst.libraries_dir())
    missing: list[str] = []
    bad_hash: list[str] = []
    checked = 0
    for lib in resolved.get("libraries") or []:
        if checked >= _MAX_LIB_CHECK:
            break
        if lib.get("clientreq") is False:
            continue
        if not utils.check_rules(lib.get("rules")):
            continue
        name = lib.get("name")
        if not name:
            continue
        downloads = lib.get("downloads") or {}
        artifact = downloads.get("artifact") or {}
        if artifact or (not downloads and not lib.get("natives")):
            path = artifact.get("path") or utils.maven_artifact_path(name)
            dest = libs_dir / path
            sha1 = artifact.get("sha1")
            size = artifact.get("size")
            checked += 1
            if not dest.is_file():
                missing.append(Path(path).name)
            elif sha1 or size is not None:
                if not utils.file_matches(dest, sha1, size):
                    bad_hash.append(Path(path).name)
        nkey = select_native_classifier(lib)
        if nkey and checked < _MAX_LIB_CHECK:
            classifiers = downloads.get("classifiers") or {}
            entry = classifiers.get(nkey) or {}
            if not entry:
                for k, v in classifiers.items():
                    if subst_native_key(k) == nkey:
                        entry = v or {}
                        break
            if entry or nkey:
                path = entry.get("path") or utils.maven_artifact_path(f"{name}:{nkey}")
                dest = libs_dir / path
                checked += 1
                if not dest.is_file():
                    missing.append(Path(path).name)
                elif entry.get("sha1") or entry.get("size") is not None:
                    if not utils.file_matches(dest, entry.get("sha1"), entry.get("size")):
                        bad_hash.append(Path(path).name)

    if missing:
        sample = "\n - ".join(missing[:10])
        more = f"\n…共缺 {len(missing)} 个" if len(missing) > 10 else ""
        level = "error" if len(missing) >= 3 else "warn"
        items.append(_item(
            level, "libs_missing", "依赖库缺失",
            f"缺少以下库文件，请到「下载 → 原版游戏」的已安装列表点「修复」：\n - {sample}{more}"))
    if bad_hash:
        sample = "\n - ".join(bad_hash[:8])
        items.append(_item(
            "warn", "libs_hash", "依赖库校验不一致",
            f"以下库 sha1/大小与清单不符，可能损坏：\n - {sample}\n建议修复该版本。"))


def _check_assets(inst: Instance, resolved: dict, items: list[dict]) -> None:
    idx = resolved.get("assetIndex") or {}
    if not idx:
        return
    assets_dir = Path(inst.assets_dir())
    index_id = idx.get("id") or ""
    index_file = assets_dir / "indexes" / f"{index_id}.json"
    if not index_file.is_file():
        items.append(_item(
            "error", "assets_index_missing", "资源索引缺失",
            f"找不到 assets/indexes/{index_id}.json，请修复该版本。"))
        return
    if idx.get("sha1") and not utils.file_matches(index_file, idx.get("sha1"), idx.get("size")):
        items.append(_item(
            "warn", "assets_index_hash", "资源索引校验失败",
            f"{index_id}.json 与清单不符，建议修复该版本。"))
    index = utils.read_json(index_file, None) or {}
    objects = index.get("objects") or {}
    if not objects:
        items.append(_item(
            "warn", "assets_index_empty", "资源索引为空",
            f"{index_id}.json 没有 objects，游戏可能缺材质/音效。"))
        return
    # 抽样检查：按 hash 字典序取前 N 个，统计缺失比例再外推
    keys = sorted(objects.keys())
    sample = keys[:_MAX_ASSET_SAMPLE]
    miss = 0
    for k in sample:
        obj = objects.get(k) or {}
        h = (obj.get("hash") or "").strip()
        if len(h) < 2:
            continue
        dest = assets_dir / "objects" / h[:2] / h
        if not dest.is_file():
            miss += 1
    if miss == 0:
        return
    ratio = miss / max(1, len(sample))
    est = int(round(ratio * len(objects)))
    if ratio >= 0.25 or miss >= 8:
        items.append(_item(
            "error", "assets_missing", "游戏资源大量缺失",
            f"抽样 {len(sample)} 个资源缺 {miss} 个（约估全量缺 {est}/{len(objects)}）。请修复该版本。"))
    else:
        items.append(_item(
            "warn", "assets_partial", "部分游戏资源缺失",
            f"抽样 {len(sample)} 个资源缺 {miss} 个（约估全量缺 {est}/{len(objects)}）。可先启动，异常再修复。"))


def _check_natives(inst: Instance, version: str, resolved: dict, items: list[dict]) -> None:
    from .installer import natives_present, select_native_classifier

    needs = False
    for lib in resolved.get("libraries") or []:
        if not utils.check_rules(lib.get("rules")):
            continue
        if select_native_classifier(lib):
            needs = True
            break
    if not needs:
        return
    try:
        ndir = Path(inst.natives_dir(version, resolved))
    except Exception:
        return
    if not natives_present(ndir):
        items.append(_item(
            "warn", "natives_missing", "本地库（natives）可能未解压",
            f"{ndir} 为空或不完整。启动时会尝试再解压；若仍黑屏请修复该版本。"))


def _check_mod_conflicts(inst: Instance, mods_path: Path, items: list[dict]) -> None:
    try:
        from .ai.conflict import inspect_jar
    except Exception:
        return
    jars: list[Path] = []
    for p in sorted(mods_path.iterdir()):
        n = p.name.lower()
        if p.is_file() and (n.endswith(".jar") or n.endswith(".jar.disabled")):
            jars.append(p)
        if len(jars) >= _MAX_MOD_JARS:
            break
    if not jars:
        return
    # 仅扫启用的 jar，.disabled 跳过冲突图
    enabled = [p for p in jars if not p.name.lower().endswith(".disabled")]
    if len(enabled) > _MAX_MOD_JARS:
        enabled = enabled[:_MAX_MOD_JARS]
    mods = []
    for p in enabled:
        try:
            mods.append(inspect_jar(p))
        except Exception:
            continue
    by_id: dict[str, list] = {}
    for m in mods:
        mid = (m.get("id") or "").lower()
        if mid:
            by_id.setdefault(mid, []).append(m)
    dups = []
    for mid, group in by_id.items():
        if len(group) > 1:
            dups.append(f"{mid} ×{len(group)}（" + ", ".join(g.get("file") or "?" for g in group[:3]) + "）")
    if dups:
        items.append(_item(
            "error", "mod_duplicate", "重复安装同一模组",
            "同 id 装了多份会导致启动失败：\n - " + "\n - ".join(dups[:8])))

    present = set(by_id)
    breaks = []
    missing_deps = []
    skip = {
        "minecraft", "java", "forge", "neoforge", "fabricloader", "fabric-loader",
        "quilt_loader", "quilt-loader",
    }
    for m in mods:
        for br in (m.get("breaks") or []) + (m.get("conflicts") or []):
            bid = str(br.get("id") or "").lower()
            if bid and bid in present:
                breaks.append(f"{m.get('id')} ↔ {bid}")
        for dep in m.get("depends") or []:
            did = str(dep.get("id") or "").lower()
            if not did or did in skip:
                continue
            if did in ("fabric-api", "fabricapi", "fabric"):
                if "fabric-api" not in present and "fabricapi" not in present:
                    missing_deps.append(f"{m.get('name') or m.get('file')} 需要 Fabric API")
                continue
            if did not in present:
                missing_deps.append(f"{m.get('name') or m.get('file')} 缺少 {did}")
    if breaks:
        items.append(_item(
            "error", "mod_breaks", "模组互相冲突",
            "元数据声明不兼容：\n - " + "\n - ".join(breaks[:8])))
    # 缺依赖很多时只 warn（用户可能靠全局 mods），明确 fabric-api 则 error
    fab = [x for x in missing_deps if "Fabric API" in x]
    other = [x for x in missing_deps if x not in fab]
    if fab:
        items.append(_item(
            "error", "mod_missing_fabric_api", "缺少 Fabric API",
            "\n - ".join(list(dict.fromkeys(fab))[:6])))
    if other:
        items.append(_item(
            "warn", "mod_missing_dep", "可能缺少模组依赖",
            "\n - ".join(list(dict.fromkeys(other))[:8]) + "\n（若实际由其它 jar 提供可忽略）"))


def _item(level: str, code: str, title: str, detail: str) -> dict:
    return {"level": level, "code": code, "title": title, "detail": detail}
