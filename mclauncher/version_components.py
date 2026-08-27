# -*- coding: utf-8 -*-
"""已装版本的组件识别与加载器原地更换 / 移除（对标 HMCL「自动安装」页）。

components_of: 解析版本 JSON 继承链，识别 Minecraft 版本与加载器及其版本号。
switch_loader: 给已装版本换 / 装 / 移除加载器。复用 game_install 的安装分发，
安装出规范版本后把 JSON 原地写回原版本目录（id 重写），保留 pymcl.json、
mods、隔离数据；规范目录若是本次新建的则清掉，不留垃圾版本。
"""
from __future__ import annotations

import json
import re
import shutil
import zipfile
from pathlib import Path

from . import utils

# 原版 id：正式版 1.20.4（含 -pre/-rc）/ 快照 24w14a / 老远古 b1.7.3、a1.2.6、rd-132211
_VANILLA_ID = re.compile(
    r"^(\d+\.\d+(\.\d+)?(-(pre|rc)\d*)?|\d{2}w\d{2}[a-z~](_or_[a-z]+)?"
    r"|[ab]\d+\.\d+(\.\d+)?[a-z]?(_\d+)?|rd-\d+.*|c\d+\.\d+[a-z]?.*|inf-\d+.*)$",
    re.IGNORECASE,
)
_MC_IN_ID = re.compile(r"(\d+\.\d+(?:\.\d+)?)")

LOADER_LABELS = {
    "fabric": "Fabric",
    "quilt": "Quilt",
    "forge": "Forge",
    "neoforge": "NeoForge",
    "optifine": "OptiFine",
    "liteloader": "LiteLoader",
    "": "原版",
}


class ComponentError(Exception):
    pass


def _norm_loader(loader: str) -> str:
    s = (loader or "").strip().lower()
    if s in ("", "无", "none", "vanilla", "原版"):
        return ""
    return s


def _json_chain(instance, version_id: str) -> list[dict]:
    chain, seen, vid = [], set(), version_id
    while vid and vid not in seen:
        seen.add(vid)
        vjson = instance.version_json(vid)
        if not isinstance(vjson, dict):
            break
        chain.append(vjson)
        vid = vjson.get("inheritsFrom")
    return chain


def _loader_from_libs(chain: list[dict]) -> tuple[str, str]:
    """(loader, loader_version)。fabric/quilt/neoforge/forge 优先，其次 optifine/liteloader。"""
    found: dict[str, str] = {}
    for vjson in chain:
        for lib in vjson.get("libraries") or []:
            name = str(lib.get("name") or "")
            parts = name.split(":")
            if len(parts) < 3:
                continue
            ver = parts[2]
            if name.startswith("net.fabricmc:fabric-loader:"):
                found.setdefault("fabric", ver)
            elif name.startswith("org.quiltmc:quilt-loader:"):
                found.setdefault("quilt", ver)
            elif name.startswith("net.neoforged:neoforge:"):
                found.setdefault("neoforge", ver)
            elif name.startswith("net.neoforged:forge:"):
                # NeoForge 1.20.1 时代沿用 forge 坐标，版本形如 1.20.1-47.1.84
                found.setdefault("neoforge", ver.split("-", 1)[1] if "-" in ver else ver)
            elif name.startswith(("net.minecraftforge:forge:",
                                  "net.minecraftforge:fmlloader:",
                                  "net.minecraftforge:minecraftforge:")):
                # 1.20.4-49.0.30 → 49.0.30；1.7.10-10.13.4.1614-1.7.10 → 10.13.4.1614
                v = ver.split("-", 1)[1] if "-" in ver else ver
                segs = v.split("-")
                if len(segs) > 1 and _MC_IN_ID.fullmatch(segs[-1]):
                    v = "-".join(segs[:-1])
                found.setdefault("forge", v)
            elif name.startswith("optifine:OptiFine:"):
                # 1.20.4_HD_U_I6 → HD_U_I6
                v = ver.split("_", 1)[1] if "_" in ver else ver
                found.setdefault("optifine", v)
            elif name.startswith("com.mumfrey:liteloader:"):
                found.setdefault("liteloader", ver)
    for kind in ("fabric", "quilt", "neoforge", "forge", "optifine", "liteloader"):
        if kind in found:
            return kind, found[kind]
    return "", ""


def _mc_from_jar(instance, vid: str) -> str:
    """1.14+ 原版 jar 内带 version.json，可靠识别被复制/改名的原版。"""
    jar = instance.versions_dir() / vid / f"{vid}.jar"
    if not jar.is_file():
        return ""
    try:
        with zipfile.ZipFile(jar) as zf:
            data = json.loads(zf.read("version.json").decode("utf-8"))
        return str(data.get("id") or "")
    except Exception:
        return ""


def components_of(instance, version_id: str) -> dict:
    """{"mc": "1.20.4", "loader": "fabric", "loader_version": "0.16.9"}。

    识别不了 Minecraft 版本时 mc 为空字符串，由调用方决定是否报错。
    """
    chain = _json_chain(instance, version_id)
    if not chain:
        raise ComponentError(f"版本 {version_id} 未安装或缺少版本 JSON")
    loader, loader_version = _loader_from_libs(chain)
    root = chain[-1]
    root_id = str(root.get("id") or "")

    mc = ""
    if root.get("inheritsFrom"):
        # 继承链没走到底（父 JSON 缺失）：inheritsFrom 就是原版名
        mc = str(root["inheritsFrom"])
    elif _VANILLA_ID.match(root_id):
        mc = root_id
    if not mc:
        mc = _mc_from_jar(instance, root_id) or _mc_from_jar(instance, version_id)
    if not mc:
        m = _MC_IN_ID.search(root_id) or _MC_IN_ID.search(version_id)
        if m:
            mc = m.group(1)
    return {"mc": mc, "loader": loader, "loader_version": loader_version,
            "version": version_id}


def switch_loader(instance, version_id: str, loader: str, loader_version: str = "",
                  dm=None, on_progress=None, cancel=None, extra: dict | None = None) -> dict:
    """给已装版本换 / 装 / 移除加载器。

    - 版本目录名与原版号不同：原地重写该目录的 JSON，mods / 设置 / 存档全保留。
    - 版本目录名就是原版号（如 "1.20.4"）：不能自我继承，改为生成新版本并保留原版。
    返回 {"version": 最终版本 id, "in_place": bool, "loader": ..., "loader_version": ...}
    """
    from .game_install import install_game
    from .installer import Installer

    loader = _norm_loader(loader)
    comps = components_of(instance, version_id)
    mc = comps["mc"]
    if not mc:
        raise ComponentError(f"无法确定 {version_id} 对应的 Minecraft 版本，不能更换加载器")
    if not loader and not comps["loader"] and version_id == mc:
        raise ComponentError("该版本已是原版，无需移除加载器")

    vroot = instance.versions_dir()
    pre = {p.name for p in vroot.iterdir() if p.is_dir()} if vroot.is_dir() else set()

    installer = Installer(instance, dm, on_progress=on_progress, cancel=cancel)
    extra = dict(extra or {})
    lv = (loader_version or "").strip()
    if loader == "optifine" and lv:
        # 列表里的 id 形如 HD_U_I6：patch 是最后一段，其余是 type
        typ, _, patch = lv.rpartition("_")
        extra.setdefault("optifine_type", typ or lv)
        extra.setdefault("optifine_patch", patch if typ else "")
        lv = ""
    new_id = install_game(installer, mc, loader or "无", lv, extra)

    if new_id == version_id:
        after = components_of(instance, version_id)
        return {"version": version_id, "in_place": True,
                "loader": after["loader"], "loader_version": after["loader_version"]}

    if version_id == mc and loader:
        # 原版规范目录装加载器：新 JSON inheritsFrom 指回自己会成环，保留为新版本
        after = components_of(instance, new_id)
        return {"version": new_id, "in_place": False,
                "loader": after["loader"], "loader_version": after["loader_version"]}

    raw = instance.version_json(new_id)
    if not isinstance(raw, dict):
        raise ComponentError(f"安装结果异常：找不到 {new_id} 的版本 JSON")
    raw = dict(raw)
    raw["id"] = version_id
    vdir = vroot / version_id
    utils.ensure_dir(vdir)
    utils.write_json(vdir / f"{version_id}.json", raw)

    # jar：旧版 Forge 会往规范目录拷客户端 jar；移除加载器回原版时复用原版 jar，
    # 避免按 downloads.client 重下几十 MB。
    dest_jar = vdir / f"{version_id}.jar"
    src_jar = vroot / new_id / f"{new_id}.jar"
    if src_jar.is_file():
        shutil.copy2(src_jar, dest_jar)
    elif dest_jar.is_file() and not (raw.get("downloads") or {}).get("client"):
        # 换加载器后残留的旧 jar 会盖住父版本 jar，清掉
        dest_jar.unlink()

    if new_id not in pre and new_id != mc:
        utils.remove_tree(vroot / new_id)

    after = components_of(instance, version_id)
    return {"version": version_id, "in_place": True,
            "loader": after["loader"], "loader_version": after["loader_version"]}
