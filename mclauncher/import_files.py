# -*- coding: utf-8 -*-
"""本地文件导入识别：整合包 / 模组 / 世界 / 资源包 / 光影包 / 数据包。

对标 PCL2「把文件拖进启动器窗口，自动识别类型并安装」。
识别只读 zip 中央目录（必要时读一个 manifest 成员），不解压。
识别结果交给既有安装通道（modpack / mods / worlds / content）落地，
这里不重复实现任何安装逻辑。
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

# kind -> 中文标签（UI 层再过 tr()）
KIND_LABELS = {
    "modpack": "整合包",
    "mod": "模组",
    "world": "世界",
    "resourcepack": "资源包",
    "shaderpack": "光影包",
    "datapack": "数据包",
    "unknown": "无法识别",
}

SUPPORTED_EXTS = (".mrpack", ".jar", ".litemod", ".zip")

_SHADER_EXTS = (".fsh", ".vsh", ".gsh", ".csh", ".glsl")


def _parts(name: str) -> list[str]:
    return [p for p in name.replace("\\", "/").split("/") if p]


def _read_member_json(z: zipfile.ZipFile, name: str):
    try:
        with z.open(name) as f:
            return json.loads(f.read(512 * 1024).decode("utf-8", "replace"))
    except (OSError, ValueError, KeyError, zipfile.BadZipFile):
        return None


def _is_cf_manifest(z: zipfile.ZipFile, name: str) -> bool:
    data = _read_member_json(z, name)
    if not isinstance(data, dict):
        return False
    if str(data.get("manifestType") or "") == "minecraftModpack":
        return True
    # 老包不写 manifestType：有 minecraft.version 也认
    mc = data.get("minecraft")
    return isinstance(mc, dict) and bool(mc.get("version"))


def _zip_kind(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist() if _parts(n)]
            entries = [(_parts(n), n) for n in names]

            # 1) 整合包标记（允许套一层目录）
            for parts, _raw in entries:
                if len(parts) <= 2 and parts[-1].lower() in (
                        "modrinth.index.json", "mmc-pack.json", "instance.cfg",
                        "mcbbs.packmeta"):
                    return "modpack"
            for parts, raw in entries:
                if len(parts) <= 2 and parts[-1].lower() == "manifest.json":
                    if _is_cf_manifest(z, raw):
                        return "modpack"
            # 展开好的 .minecraft 目录压缩包（versions/<id>/<id>.json）
            for parts, _raw in entries:
                low = [p.lower() for p in parts]
                if "versions" in low[:2] and parts[-1].lower().endswith(".json"):
                    idx = low.index("versions")
                    if len(parts) - idx == 3:
                        return "modpack"

            # 2) 世界（level.dat 允许套一层世界目录）
            for parts, _raw in entries:
                if len(parts) <= 2 and parts[-1].lower() == "level.dat":
                    return "world"

            # 3) pack.mcmeta：数据包 or 资源包
            for parts, _raw in entries:
                if len(parts) <= 2 and parts[-1].lower() == "pack.mcmeta":
                    prefix = parts[:-1]
                    has_data = any(
                        p2[:len(prefix)] == prefix and len(p2) > len(prefix)
                        and p2[len(prefix)].lower() == "data"
                        for p2, _r in entries)
                    return "datapack" if has_data else "resourcepack"

            # 4) 光影包（shaders/ 目录，允许套一层）
            for parts, _raw in entries:
                low = [p.lower() for p in parts]
                if "shaders" in low[:2] and parts[-1].lower().endswith(_SHADER_EXTS):
                    return "shaderpack"
    except (OSError, zipfile.BadZipFile):
        return "unknown"
    return "unknown"


def classify_file(path) -> dict:
    """识别一个本地文件。返回 {kind, name, label, path}；识别不了 kind="unknown"。"""
    p = Path(path)
    info = {"kind": "unknown", "name": p.name, "path": str(p)}
    if not p.is_file():
        info["error"] = "文件不存在"
        return info
    ext = p.suffix.lower()
    if ext == ".mrpack":
        info["kind"] = "modpack"
    elif ext in (".jar", ".litemod"):
        info["kind"] = "mod"
    elif ext == ".zip":
        info["kind"] = _zip_kind(p)
    info["label"] = KIND_LABELS.get(info["kind"], KIND_LABELS["unknown"])
    return info


def classify_files(paths) -> list[dict]:
    return [classify_file(p) for p in paths or []]
