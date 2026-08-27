# -*- coding: utf-8 -*-
"""Cleanroom 加载器自动安装（HMCL 3.7「支持自动安装 Cleanroom」同款）。

Cleanroom 是 Forge 1.12.2 的现代化分支（新 Java、新 LWJGL），1.12.2
老整合包玩家的常用选择。发行物挂在 GitHub Releases 上；安装器是 Forge
现代安装器（spec 0/1）的分支：install_profile.json + version.json、
没有处理器步骤。主构件 com.cleanroommc:cleanroom 不提供外网下载地址，
内嵌在安装器的 maven/ 目录里，要从包里解出来放进 libraries——官方
安装器和 HMCL 都是这么做的。

生成的 version.json 自带 javaVersion（majorVersion 25 起步），启动链
的 Java 自动选择 / 自动下载不需要任何特判。
"""
from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

from . import utils
from .downloader import DownloadManager

MC_VERSION = "1.12.2"
RELEASES_API = "https://api.github.com/repos/CleanroomMC/Cleanroom/releases"
INSTALLER_URL = ("https://github.com/CleanroomMC/Cleanroom/releases/download/"
                 "{tag}/cleanroom-{tag}-installer.jar")


def list_versions(dm: DownloadManager | None = None) -> list[dict]:
    """GitHub Releases 里带安装器的 Cleanroom 版本，新的在前。

    返回 [{"id", "label", "stable"}]，走 DownloadManager 的 GitHub
    镜像候选（大陆用户不用翻墙）。
    """
    dm = dm or DownloadManager(threads=2)
    rows = dm.fetch_json(f"{RELEASES_API}?per_page=100", timeout=30)
    out = []
    for r in rows or []:
        tag = str((r or {}).get("tag_name") or "").strip()
        if not tag:
            continue
        has_installer = any(
            str((a or {}).get("name") or "").endswith("-installer.jar")
            for a in (r.get("assets") or []))
        if not has_installer:
            continue
        out.append({
            "id": tag,
            "label": tag,
            "stable": not bool(r.get("prerelease")),
        })
    return out


def extract_embedded_maven(installer_jar, libs_dir, force=False) -> int:
    """把安装器 jar 内嵌的 maven/ 构件解压进 libraries，返回解出的文件数。"""
    libs_dir = Path(libs_dir)
    n = 0
    with zipfile.ZipFile(installer_jar) as zf:
        for name in zf.namelist():
            if not name.startswith("maven/") or name.endswith("/"):
                continue
            rel = name[len("maven/"):]
            parts = Path(rel).parts
            if not rel or ".." in parts or Path(rel).is_absolute():
                continue
            dest = libs_dir / rel
            if dest.is_file() and not force:
                n += 1
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(name) as src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out)
            n += 1
    return n


def install(installer, mc_version, version=None, force=False) -> str:
    """下载 Cleanroom 安装器并离线安装，返回生成的版本 id。

    installer 是 mclauncher.installer.Installer：复用其 Forge 现代安装
    流程（下原版、下依赖库、写版本 json），Cleanroom 安装器没有处理器，
    唯一的额外步骤是解出内嵌 maven/ 构件。
    """
    from .installer import InstallError, is_forge_installer_jar

    mc = (mc_version or "").strip() or MC_VERSION
    if mc != MC_VERSION:
        raise InstallError(
            f"Cleanroom 仅支持 Minecraft {MC_VERSION}（当前选择 {mc}）")
    tag = (version or "").strip()
    if not tag:
        rows = list_versions(installer.dm)
        if not rows:
            raise InstallError("拿不到 Cleanroom 版本列表（GitHub 不可达？）")
        tag = rows[0]["id"]

    cache = utils.ROOT / "cache"
    utils.ensure_dir(cache)
    jar = cache / f"cleanroom-{tag}-installer.jar"
    if force or not is_forge_installer_jar(jar):
        installer._note(f"下载 Cleanroom 安装器 {tag}")
        installer.dm.download(INSTALLER_URL.format(tag=tag), jar, force=force)
        if not is_forge_installer_jar(jar):
            raise InstallError(f"Cleanroom 安装器损坏或格式不对: {jar.name}")
    else:
        installer._note(f"使用缓存的 Cleanroom 安装器 {tag}")

    profile = installer._read_forge_install_profile(jar)
    if not profile or not (profile.get("json") or profile.get("processors")):
        raise InstallError("无法解析 Cleanroom 安装器（install_profile.json 缺失）")

    extracted = extract_embedded_maven(jar, installer.instance.libraries_dir(),
                                       force=force)
    if extracted:
        installer._note(f"已解出 {extracted} 个内嵌构件（Cleanroom 主 jar）")

    installer._note(f"离线安装 Cleanroom {tag}")
    vid = installer._install_forge_modern(jar, profile, mc, force=force)
    installer._note(f"Cleanroom {vid} 安装完成（游戏需要较新的 Java，启动时会自动匹配/下载）")
    return vid
