# -*- coding: utf-8 -*-
"""从模组 jar 读 Fabric/Quilt/Forge/NeoForge 元数据，扫冲突。"""

from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path

from mclauncher.instances import Instance
from mclauncher.mods import detect_loader, detect_mc_version, list_instance_mods


def _read_zip_text(zf: zipfile.ZipFile, name: str) -> str:
    try:
        return zf.read(name).decode("utf-8", errors="replace")
    except Exception:
        return ""


def _load_toml(text: str):
    if not text:
        return None
    try:
        import tomllib
        return tomllib.loads(text)
    except Exception:
        pass
    try:
        import tomli
        return tomli.loads(text)
    except Exception:
        pass
    return None


def _kv_line(line: str):
    if "=" not in line:
        return None, None
    k, v = line.split("=", 1)
    k = k.strip()
    v = v.strip().strip('"').strip("'")
    if v.lower() in ("true", "false"):
        v = v.lower() == "true"
    return k, v


def _parse_mods_toml_fallback(text: str) -> dict:
    mods = []
    deps = []
    mode = None
    cur = None
    loader = ""
    for raw in text.splitlines():
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("[[mods]]"):
            if cur and mode == "mod":
                mods.append(cur)
            cur = {}
            mode = "mod"
            continue
        m = re.match(r"\[\[dependencies\.([^\]]+)\]\]", s)
        if m:
            if cur and mode == "mod":
                mods.append(cur)
                cur = None
            deps.append({"owner": m.group(1)})
            mode = "dep"
            continue
        k, v = _kv_line(s)
        if k is None:
            continue
        if k == "modLoader" and mode != "dep":
            loader = str(v)
        if mode == "mod" and cur is not None:
            cur[k] = v
        elif mode == "dep" and deps:
            deps[-1][k] = v
    if cur and mode == "mod":
        mods.append(cur)
    return {"modLoader": loader, "mods": mods, "deps": deps}


def _from_forge_toml(text: str, flavor: str) -> dict:
    data = _load_toml(text)
    mods = []
    deps = []
    loader = flavor
    if data:
        loader = data.get("modLoader") or flavor
        for m in data.get("mods") or []:
            if isinstance(m, dict):
                mods.append(m)
        raw_deps = data.get("dependencies") or {}
        if isinstance(raw_deps, dict):
            for owner, items in raw_deps.items():
                if not isinstance(items, list):
                    items = [items]
                for item in items:
                    if isinstance(item, dict):
                        row = dict(item)
                        row["owner"] = owner
                        deps.append(row)
    else:
        fb = _parse_mods_toml_fallback(text)
        loader = fb.get("modLoader") or flavor
        mods = fb.get("mods") or []
        deps = fb.get("deps") or []
    primary = mods[0] if mods else {}
    depends = []
    breaks = []
    mid = str(primary.get("modId") or "")
    for d in deps:
        owner = str(d.get("owner") or mid)
        if mid and owner not in (mid, ""):
            continue
        rec = {
            "id": str(d.get("modId") or ""),
            "mandatory": bool(d.get("mandatory", True)),
            "version": str(d.get("versionRange") or d.get("version") or "*"),
        }
        dtype = str(d.get("type") or "").lower()
        if dtype in ("incompatible", "broke", "break", "breaks"):
            breaks.append(rec)
            continue
        if rec["mandatory"]:
            depends.append(rec)
    return {
        "id": str(primary.get("modId") or ""),
        "name": str(primary.get("displayName") or primary.get("modId") or ""),
        "version": str(primary.get("version") or ""),
        "loader": "neoforge" if "neo" in str(loader).lower() else "forge",
        "depends": depends,
        "breaks": breaks,
        "conflicts": [],
        "provides": [],
    }


def _from_fabric(data: dict, loader: str) -> dict:
    def _as_map(val):
        if isinstance(val, dict):
            return [{"id": str(k), "version": str(v)} for k, v in val.items()]
        if isinstance(val, list):
            out = []
            for item in val:
                if isinstance(item, dict):
                    out.append({"id": str(item.get("id") or item.get("mod") or ""),
                                "version": str(item.get("versions") or item.get("version") or "*")})
                elif item:
                    out.append({"id": str(item), "version": "*"})
            return out
        return []

    return {
        "id": str(data.get("id") or ""),
        "name": str(data.get("name") or data.get("id") or ""),
        "version": str(data.get("version") or ""),
        "loader": loader,
        "depends": _as_map(data.get("depends") or data.get("depends".upper()) or {}),
        "breaks": _as_map(data.get("breaks") or {}),
        "conflicts": _as_map(data.get("conflicts") or {}),
        "provides": list(data.get("provides") or []),
    }


def _from_quilt(data: dict) -> dict:
    ql = data.get("quilt_loader") if isinstance(data.get("quilt_loader"), dict) else data
    depends = []
    breaks = []
    conflicts = []
    for item in ql.get("depends") or []:
        if isinstance(item, dict):
            depends.append({"id": str(item.get("id") or ""), "version": str(item.get("versions") or "*")})
    for item in ql.get("breaks") or []:
        if isinstance(item, dict):
            breaks.append({"id": str(item.get("id") or ""), "version": str(item.get("versions") or "*")})
    return {
        "id": str(ql.get("id") or data.get("id") or ""),
        "name": str((ql.get("metadata") or {}).get("name") or ql.get("id") or ""),
        "version": str(ql.get("version") or ""),
        "loader": "quilt",
        "depends": depends,
        "breaks": breaks,
        "conflicts": conflicts,
        "provides": list(ql.get("provides") or []),
    }


def inspect_jar(path: Path) -> dict:
    info = {
        "file": path.name,
        "id": path.stem,
        "name": path.stem,
        "version": "",
        "loader": "unknown",
        "depends": [],
        "breaks": [],
        "conflicts": [],
        "provides": [],
        "enabled": not path.name.lower().endswith(".disabled"),
    }
    try:
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
            if "fabric.mod.json" in names:
                data = json.loads(_read_zip_text(zf, "fabric.mod.json") or "{}")
                info.update(_from_fabric(data, "fabric"))
            elif "quilt.mod.json" in names:
                data = json.loads(_read_zip_text(zf, "quilt.mod.json") or "{}")
                info.update(_from_quilt(data))
            elif "META-INF/neoforge.mods.toml" in names:
                info.update(_from_forge_toml(_read_zip_text(zf, "META-INF/neoforge.mods.toml"), "neoforge"))
            elif "META-INF/mods.toml" in names:
                info.update(_from_forge_toml(_read_zip_text(zf, "META-INF/mods.toml"), "forge"))
            elif "mcmod.info" in names:
                raw = json.loads(_read_zip_text(zf, "mcmod.info") or "[]")
                row = raw[0] if isinstance(raw, list) and raw else (raw if isinstance(raw, dict) else {})
                info.update({
                    "id": str(row.get("modid") or info["id"]),
                    "name": str(row.get("name") or info["name"]),
                    "version": str(row.get("version") or ""),
                    "loader": "forge",
                    "depends": [{"id": str(x), "version": "*"} for x in (row.get("requiredMods") or [])],
                })
    except Exception as exc:
        info["error"] = str(exc)
    info["file"] = path.name
    info["enabled"] = not path.name.lower().endswith(".disabled")
    return info


def _mod_files(instance: Instance, mods_dir=None) -> list[Path]:
    d = Path(mods_dir) if mods_dir else instance.path / "mods"
    if not d.is_dir():
        return []
    out = []
    for p in sorted(d.iterdir()):
        n = p.name.lower()
        if n.endswith(".jar") or n.endswith(".jar.disabled") or n.endswith(".disabled"):
            if p.is_file():
                out.append(p)
    if not out and not mods_dir:
        out = list_instance_mods(instance)
    return out


_SKIP_DEP = {
    "minecraft", "java", "forge", "neoforge", "fabricloader", "fabric-loader",
    "quilt_loader", "quilt-loader",
}


def scan_conflicts(instance: Instance, mods_dir=None) -> dict:
    """扫描模组冲突。mods_dir 指定时扫该目录（版本隔离的独立 mods）。"""
    loader = detect_loader(instance)
    mc = detect_mc_version(instance)
    files = _mod_files(instance, mods_dir=mods_dir)
    mods = [inspect_jar(p) for p in files]
    by_id = {}
    issues = []
    for m in mods:
        mid = (m.get("id") or "").lower()
        if not mid:
            continue
        by_id.setdefault(mid, []).append(m)

    for mid, group in by_id.items():
        enabled = [g for g in group if g.get("enabled")]
        if len(enabled) > 1:
            issues.append({
                "type": "duplicate_id",
                "severity": "error",
                "id": mid,
                "files": [g["file"] for g in enabled],
                "message": f"模组 {mid} 装了 {len(enabled)} 份",
            })

    present = set(by_id)
    for m in mods:
        if not m.get("enabled"):
            continue
        ml = (m.get("loader") or "unknown").lower()
        if loader and ml not in ("unknown", "") and ml != loader:
            if not (loader == "quilt" and ml == "fabric"):
                issues.append({
                    "type": "loader_mismatch",
                    "severity": "error",
                    "id": m.get("id"),
                    "file": m.get("file"),
                    "message": f"{m.get('file')} 是 {ml} 模组，当前实例是 {loader}",
                })
        for dep in m.get("depends") or []:
            did = str(dep.get("id") or "").lower()
            if not did or did in _SKIP_DEP:
                continue
            if did in ("fabric-api", "fabricapi", "fabric"):
                if "fabric-api" not in present and "fabricapi" not in present:
                    issues.append({
                        "type": "missing_dep",
                        "severity": "error",
                        "id": m.get("id"),
                        "need": "fabric-api",
                        "file": m.get("file"),
                        "message": f"{m.get('name') or m.get('file')} 需要 Fabric API",
                    })
                continue
            if did not in present:
                issues.append({
                    "type": "missing_dep",
                    "severity": "error",
                    "id": m.get("id"),
                    "need": did,
                    "file": m.get("file"),
                    "message": f"{m.get('name') or m.get('file')} 缺少依赖 {did}",
                })
        for br in (m.get("breaks") or []) + (m.get("conflicts") or []):
            bid = str(br.get("id") or "").lower()
            if bid and bid in present:
                issues.append({
                    "type": "breaks",
                    "severity": "error",
                    "id": m.get("id"),
                    "other": bid,
                    "file": m.get("file"),
                    "message": f"{m.get('id')} 与 {bid} 不兼容",
                })

    return {
        "instance": instance.name,
        "loader": loader,
        "mc_version": mc,
        "mod_count": len(mods),
        "enabled": sum(1 for m in mods if m.get("enabled")),
        "mods": [{
            "file": m.get("file"),
            "id": m.get("id"),
            "name": m.get("name"),
            "version": m.get("version"),
            "loader": m.get("loader"),
            "enabled": m.get("enabled"),
            "depends": [d.get("id") for d in (m.get("depends") or [])][:12],
        } for m in mods],
        "issues": issues,
        "issue_count": len(issues),
    }
