# -*- coding: utf-8 -*-
"""启动器自更新：查清单、下包、写替换脚本。"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from . import APP_VERSION, utils
from .config import CONFIG
from .downloader import DownloadManager

# 历史默认源 pymcl.dev 已失效（DNS 无法解析），默认改走 GitHub Releases。
LEGACY_DEAD_HOSTS = ("pymcl.dev",)
DEFAULT_URL = ""  # 空 = 使用 GitHub Releases
GITHUB_LATEST_API = (
    "https://api.github.com/repos/kumburovicbranko682-boop/PyMCL/releases/latest"
)
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def _parse(ver: str) -> tuple:
    bits = []
    for part in str(ver or "0").replace("-", ".").split("."):
        num = "".join(ch for ch in part if ch.isdigit())
        bits.append(int(num or 0))
    while len(bits) < 3:
        bits.append(0)
    return tuple(bits[:4])


def newer(remote: str, local: str = APP_VERSION) -> bool:
    return _parse(remote) > _parse(local)


def manifest_url() -> str:
    return str(CONFIG.get("update_url") or DEFAULT_URL).strip()


def is_dead_update_url(url: str) -> bool:
    """默认的 pymcl.dev 域名已失效：出现时直接走 GitHub 回退。"""
    from urllib.parse import urlparse
    host = urlparse(str(url or "")).netloc.lower()
    return any(host == dead or host.endswith("." + dead) for dead in LEGACY_DEAD_HOSTS)


def valid_sha256(value: str) -> bool:
    return bool(_SHA256_RE.fullmatch(str(value or "").strip()))


def _sha256_from_text(text: str) -> str:
    """从 release 描述里找 SHA-256：优先带 sha256 标注的，其次全文唯一的 64 位 hex。"""
    text = str(text or "")
    m = re.search(r"sha-?256[^0-9a-fA-F]{0,40}([0-9a-fA-F]{64})", text, re.I)
    if m:
        return m.group(1).lower()
    hexes = {h.lower() for h in re.findall(r"\b[0-9a-fA-F]{64}\b", text)}
    if len(hexes) == 1:
        return next(iter(hexes))
    return ""


def manifest_from_github_release(rel) -> dict | None:
    """GitHub /releases/latest 响应 -> 内部更新清单格式。

    sha256 取自资产 digest 字段或 release 描述；两处都没有时留空，
    check()/download() 的 SHA-256 门禁会拒绝自动下载（仅提示有新版本）。
    """
    if not isinstance(rel, dict):
        return None
    tag = str(rel.get("tag_name") or rel.get("name") or "").strip()
    if not tag:
        return None
    version = tag.lstrip("vV").strip()
    body = str(rel.get("body") or "")
    url = ""
    sha256 = ""
    for asset in rel.get("assets") or []:
        if not isinstance(asset, dict):
            continue
        name = str(asset.get("name") or "")
        if not name.lower().endswith((".exe", ".zip")):
            continue
        url = str(asset.get("browser_download_url") or "")
        digest = str(asset.get("digest") or "")
        if digest.lower().startswith("sha256:"):
            candidate = digest.split(":", 1)[1].strip()
            if valid_sha256(candidate):
                sha256 = candidate.lower()
        break
    if not sha256:
        sha256 = _sha256_from_text(body)
    return {
        "version": version,
        "notes": body[:4000],
        "url": url,
        "sha256": sha256,
    }


def _fetch_manifest(dm: DownloadManager):
    """自定义 update_url 优先（死域名跳过）；失败/缺失回退 GitHub Releases。"""
    url = manifest_url()
    last_err = None
    if url and not is_dead_update_url(url):
        try:
            data = dm.fetch_json(url, timeout=12)
            if isinstance(data, dict) and (data.get("version") or data.get("latest")):
                return data
        except Exception as exc:
            last_err = exc
    try:
        # fetch_json 默认 expand：api.github.com 会先套 github_candidates 代理，再直连
        rel = dm.fetch_json(GITHUB_LATEST_API, timeout=12)
        data = manifest_from_github_release(rel)
        if not data and isinstance(rel, dict) and (rel.get("version") or rel.get("latest")):
            data = rel  # 兼容直接返回清单格式的镜像
        if data:
            return data
        raise RuntimeError("GitHub release 响应缺少版本号")
    except Exception as exc:
        raise last_err or exc


def check(dm: DownloadManager | None = None) -> dict:
    dm = dm or DownloadManager(threads=2)
    try:
        data = _fetch_manifest(dm)
    except Exception as exc:
        return {
            "ok": False,
            "current": APP_VERSION,
            "latest": APP_VERSION,
            "has_update": False,
            "message": f"检查更新失败: {exc}",
            "notes": "",
            "url": "",
        }
    latest = str((data or {}).get("version") or (data or {}).get("latest") or "")
    has = bool(latest and newer(latest))
    sha256 = str((data or {}).get("sha256") or "").strip().lower()
    # Auto-update is a code-execution boundary. Refuse unsigned or malformed
    # manifests instead of downloading an arbitrary executable from the URL.
    signed_update = has and valid_sha256(sha256)
    integrity_error = has and not signed_update
    return {
        "ok": not integrity_error,
        "current": APP_VERSION,
        "latest": latest or APP_VERSION,
        "has_update": signed_update,
        "message": (
            f"发现 {latest}，但更新包缺少有效 SHA-256，已拒绝自动下载"
            if integrity_error else (f"发现 {latest}" if has else "已是最新版本")
        ),
        "notes": str((data or {}).get("notes") or (data or {}).get("changelog") or ""),
        "url": str((data or {}).get("url") or (data or {}).get("download") or ""),
        "sha256": sha256,
    }


def download(info: dict, dm: DownloadManager | None = None) -> str:
    url = str((info or {}).get("url") or "")
    if not url:
        raise RuntimeError("更新清单没有下载地址")
    sha256 = str((info or {}).get("sha256") or "").strip().lower()
    if not valid_sha256(sha256):
        raise RuntimeError("更新包缺少有效 SHA-256，已拒绝下载")
    dm = dm or DownloadManager(threads=4)
    dest = utils.ROOT / "cache" / f"PyMCL-{info.get('latest') or 'update'}.bin"
    dm.download(url, dest, sha256=sha256)
    # DownloadManager verifies while streaming. Keep an explicit final check
    # here as a guard for custom DownloadManager implementations.
    if utils.sha256_file(dest).lower() != sha256:
        try:
            dest.unlink(missing_ok=True)
        except OSError:
            pass
        raise RuntimeError("更新包 SHA-256 校验失败")
    return str(dest)


def apply_exe(package: str) -> str:
    """下载完成后写 bat，退出后替换当前 exe。"""
    src = Path(package)
    exe = Path(sys.argv[0]).resolve()
    if exe.suffix.lower() != ".exe":
        return "当前不是打包版，请用新压缩包覆盖源码目录。"
    bat = exe.with_name("pymcl-apply-update.bat")
    bat.write_text(
        "@echo off\n"
        "timeout /t 2 /nobreak >nul\n"
        f'copy /Y "{src}" "{exe}"\n'
        f'start "" "{exe}"\n'
        f'del "%~f0"\n',
        encoding="gbk",
        errors="replace",
    )
    return str(bat)
