# -*- coding: utf-8 -*-
"""原版 + 加载器组合安装（Forge+OptiFine / LiteLoader）。"""
from __future__ import annotations

from .installer import InstallError, Installer


def parse_optifine_token(raw: str) -> tuple[str, str]:
    s = (raw or "").strip().replace("-", "_")
    if not s:
        return "", ""
    parts = [p for p in s.replace(" ", "_").split("_") if p]
    if len(parts) >= 2:
        return parts[0], "_".join(parts[1:])
    return s, ""


def install_game(installer: Installer, version: str, loader: str = "无",
                 loader_version: str = "", extra: dict | None = None) -> str:
    extra = dict(extra or {})
    installer.skip_assets = bool(extra.get("skip_assets"))
    mc = (version or "").strip()
    if not mc:
        raise InstallError("缺少 Minecraft 版本")
    # 自定义版本名（HMCL/PCL2 安装新游戏同款）：先预检重名，装完统一改名
    custom = str(extra.get("custom_name") or "").strip()
    if custom:
        from . import version_ops as vops
        try:
            custom = vops.sanitize_id(custom)
        except vops.VersionOpError as exc:
            raise InstallError(str(exc)) from exc
        if (installer.instance.versions_dir() / custom).exists():
            raise InstallError(f"已存在版本 {custom}，换个名字")
    primary = (loader or extra.get("loader") or "无").strip().lower()
    want_of = bool(extra.get("optifine")) or primary == "optifine"
    want_ll = bool(extra.get("liteloader")) or primary == "liteloader"
    if primary in ("optifine", "liteloader"):
        primary = "无"
    lv = extra.get("loader_version") or loader_version or None
    of_typ = extra.get("optifine_type") or ""
    of_patch = extra.get("optifine_patch") or ""
    if extra.get("optifine_version") and not (of_typ or of_patch):
        of_typ, of_patch = parse_optifine_token(str(extra.get("optifine_version")))

    vid = mc
    if primary == "fabric":
        vid = installer.install_fabric(mc, extra.get("fabric_version") or lv)
    elif primary == "quilt":
        vid = installer.install_quilt(mc, extra.get("quilt_version") or lv)
    elif primary == "forge":
        vid = installer.install_forge(mc, extra.get("forge_version") or lv)
    elif primary == "neoforge":
        vid = installer.install_neoforge(mc, extra.get("neoforge_version") or lv)
    elif primary == "cleanroom":
        vid = installer.install_cleanroom(mc, extra.get("cleanroom_version") or lv)
    elif primary not in ("", "无", "none"):
        raise InstallError(f"未知加载器: {loader}")
    else:
        installer.install_version(mc)
        vid = mc

    if want_ll:
        try:
            ll_id = installer.install_liteloader(mc)
            if primary in ("", "无", "none"):
                vid = ll_id
            else:
                installer._note(f"LiteLoader 版本已写入: {ll_id}")
        except Exception as exc:
            installer._note(f"LiteLoader 安装失败: {exc}")
            if primary in ("", "无", "none"):
                raise

    if want_of:
        if primary in ("fabric", "quilt", "neoforge", "cleanroom"):
            installer._note("OptiFine 不能与 Fabric / Quilt / NeoForge / Cleanroom 同装，已跳过")
        elif primary == "forge":
            from . import optifine as optifine_mod
            mods = installer.instance.path / "mods"
            name = optifine_mod.install_as_mod(installer, mc, mods, typ=of_typ, patch=of_patch)
            installer._note(f"OptiFine 已作为 Forge 模组放入 mods/{name}")
        else:
            vid = installer.install_optifine(mc, typ=of_typ, patch=of_patch)

    # Fabric API / QSL 随装（HMCL 安装页同款可选组件）。
    # 失败只提示不炸游戏安装：加载器已装好，前置可以稍后手动补。
    if extra.get("fabric_api"):
        if primary in ("fabric", "quilt"):
            from . import mods as mods_mod
            slug = "fabric-api" if primary == "fabric" else "qsl"
            try:
                info = mods_mod.install_modrinth_mod(
                    installer.dm, slug, installer.instance,
                    mc_version=mc, loader=primary)
                installer._note(
                    f"{slug} {info.get('version') or ''} 已放入 mods（{', '.join(info.get('files') or [])}）")
            except Exception as exc:
                installer._note(f"{slug} 安装失败（可稍后到下载页手动安装）: {exc}")
        else:
            installer._note("Fabric API 只适用于 Fabric / Quilt，已跳过")

    if custom and custom != vid:
        from . import version_ops as vops
        try:
            vid = vops.rename_version(installer.instance, vid, custom)
            installer._note(f"版本已命名: {vid}")
        except vops.VersionOpError as exc:
            # 命名失败不吞掉安装成果：保留自动名并提示
            installer._note(f"自定义版本名失败（保留 {vid}）: {exc}")
    return vid
