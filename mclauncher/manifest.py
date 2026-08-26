# -*- coding: utf-8 -*-
"""Minecraft 版本清单：拉取、缓存、继承合并。"""
import re
import time

from . import utils
from .downloader import DownloadManager, DownloadError

VERSION_MANIFEST_URL = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
# 备用地址（Mojang 新旧域名均可）
VERSION_MANIFEST_URLS = [
    VERSION_MANIFEST_URL,
    "https://launchermeta.mojang.com/mc/game/version_manifest_v2.json",
]

MANIFEST_TTL = 4 * 3600          # 版本清单缓存 4 小时
VERSION_JSON_TTL = 24 * 3600     # 单个版本 JSON 缓存 24 小时


class VersionNotFound(Exception):
    pass


def get_version_manifest(dm: DownloadManager, force=False):
    """获取（并缓存）版本清单。"""
    cache_dir = utils.ROOT / "cache"
    cache_file = cache_dir / "version_manifest.json"
    meta_file = cache_dir / "version_manifest.meta"

    if not force:
        meta = utils.read_json(meta_file, None)
        if meta and cache_file.is_file():
            if time.time() - meta.get("fetched_at", 0) < MANIFEST_TTL:
                data = utils.read_json(cache_file, None)
                if data:
                    return data

    last_err = None
    from . import source
    for url in source.version_manifest_urls() or VERSION_MANIFEST_URLS:
        try:
            data = dm.fetch_json(url, timeout=20, expand=False)
            utils.ensure_dir(cache_dir)
            utils.write_json(cache_file, data)
            utils.write_json(meta_file, {"fetched_at": time.time(), "url": url})
            return data
        except Exception as e:
            last_err = e
            utils.log.warning("拉取版本清单失败 %s: %s", url, e)

    # 尝试旧缓存兜底
    cached = utils.read_json(cache_file, None)
    if cached:
        utils.log.warning("使用缓存中的旧版本清单。")
        return cached
    raise DownloadError(f"无法获取版本清单: {last_err}")


def list_remote_versions(dm: DownloadManager, force=False):
    manifest = get_version_manifest(dm, force=force)
    return {v["id"]: v for v in manifest.get("versions", [])}


def mc_version_tuple(version_id):
    """把 1.20.1 / 1.21 解析成可比较元组；无法解析返回 None。"""
    if not version_id:
        return None
    core = str(version_id).split("-")[0].split("+")[0].strip()
    if re.match(r"^\d{2}w\d{2}[a-z]$", core, re.I):
        return None
    parts = core.split(".")
    nums = []
    for p in parts:
        if not p.isdigit():
            return None
        nums.append(int(p))
    if not nums:
        return None
    while len(nums) < 3:
        nums.append(0)
    return tuple(nums[:3])


def looks_like_minecraft_version(version_id) -> bool:
    """排除整合包自身版本号（如 5.2.1b），识别官方 MC 版本。"""
    if not version_id:
        return False
    s = str(version_id).strip()
    if re.match(r"^\d{2}w\d{2}[a-z]$", s, re.I):
        return True
    t = mc_version_tuple(s)
    if not t:
        return False
    # 3.x–19.x 基本是整合包/模组版本，不是 Minecraft
    if 3 <= t[0] <= 19:
        return False
    return t[0] == 1 or t[0] >= 20


def closest_release(versions: dict, declared: str):
    """同一大版本里找最接近的正式版（优先不超过声明版本）。"""
    want = mc_version_tuple(declared)
    if not want:
        return None
    releases = []
    for vid, meta in (versions or {}).items():
        if isinstance(meta, dict) and meta.get("type") not in (None, "release"):
            continue
        vt = mc_version_tuple(vid)
        if not vt:
            continue
        if vt[0] == want[0] and vt[1] == want[1]:
            releases.append((vt, vid))
    if not releases:
        return None
    releases.sort()
    le = [x for x in releases if x[0] <= want]
    if le:
        return le[-1][1]
    return releases[0][1]


def resolve_playable_version(dm: DownloadManager, version_id):
    """把整合包声明的版本解析成官方列表里真实存在的 Minecraft 版本。

    找不到时返回 None（不要把 5.2.1b 这类包版本硬猜成 MC 版本）。
    """
    if not version_id:
        return None
    vid = str(version_id).strip()
    versions = list_remote_versions(dm)
    if vid in versions:
        return vid
    m = re.match(r"^(1\.\d+(?:\.\d+)?|\d{2}\.\d+(?:\.\d+)?)(?:[-_+].+)$", vid)
    if m:
        base = m.group(1)
        if base in versions:
            return base
        vid = base
    if vid in versions:
        return vid
    if not looks_like_minecraft_version(vid):
        return None
    return closest_release(versions, vid)


def get_version_json(dm: DownloadManager, version_id, force=False):
    """获取某个版本的 version JSON（带缓存）。"""
    cache_dir = utils.ROOT / "cache" / "versions"
    cache_file = cache_dir / f"{version_id}.json"

    if not force:
        cached = utils.read_json(cache_file, None)
        if cached and time.time() - cached.get("__pymcl_cached_at", 0) < VERSION_JSON_TTL:
            return cached

    versions = list_remote_versions(dm)
    entry = versions.get(version_id)
    if not entry:
        raise VersionNotFound(f"找不到版本 {version_id}，它可能不在官方版本列表中（例如整合包自定义版本）。")
    data = dm.fetch_json(entry["url"], timeout=60)
    data["__pymcl_cached_at"] = time.time()
    utils.ensure_dir(cache_dir)
    utils.write_json(cache_file, data)
    return data


def get_version_json_from(dm: DownloadManager, url, version_id):
    """从指定 URL 拉取 version JSON 并缓存。"""
    cache_dir = utils.ROOT / "cache" / "versions"
    cache_file = cache_dir / f"{version_id}.json"
    data = dm.fetch_json(url, timeout=60)
    data["__pymcl_cached_at"] = time.time()
    utils.ensure_dir(cache_dir)
    utils.write_json(cache_file, data)
    return data


# ---------------------------------------------------------------- 继承合并

def library_identity(lib: dict) -> str:
    """同一 group:artifact（及分类器/natives）视为一条库，用于 inheritsFrom 覆盖。

    Forge 1.7.10 用 guava:17.0 覆盖原版 15.0；不能把两条都放进 classpath，
    否则 JVM 会先加载 15.0，FML AccessTransformer 会 NoSuchMethodError。
    """
    name = lib.get("name") or ""
    parts = name.split(":")
    group = parts[0] if parts else ""
    artifact = parts[1] if len(parts) > 1 else name
    classifier = parts[3] if len(parts) > 3 else None
    if lib.get("natives"):
        return f"{group}:{artifact}:natives"
    if classifier:
        return f"{group}:{artifact}:{classifier}"
    return f"{group}:{artifact}"


def merge_libraries(parent_libs, child_libs):
    """子版本按 library_identity 覆盖父版本同名库，其余追加。"""
    merged = []
    index = {}
    for lib in parent_libs or []:
        key = library_identity(lib)
        index[key] = len(merged)
        merged.append(lib)
    for lib in child_libs or []:
        key = library_identity(lib)
        if key in index:
            merged[index[key]] = lib
        else:
            index[key] = len(merged)
            merged.append(lib)
    return merged


def _merge(base: dict, child: dict) -> dict:
    """按官方启动器语义合并 version JSON：child 覆盖 base。"""
    merged = dict(base)
    merged.update({k: v for k, v in child.items() if k != "inheritsFrom"})

    # libraries: 子级同名库覆盖父级（Forge 升级 Guava 等）
    merged["libraries"] = merge_libraries(base.get("libraries"), child.get("libraries"))

    # arguments：官方启动器是父级 + 子级拼接（Forge/Fabric 只声明增量）
    if isinstance(base.get("arguments"), dict) and isinstance(child.get("arguments"), dict):
        ba, ca = base["arguments"], child["arguments"]
        merged["arguments"] = {
            "game": list(ba.get("game") or []) + list(ca.get("game") or []),
            "jvm": list(ba.get("jvm") or []) + list(ca.get("jvm") or []),
        }
    elif "minecraftArguments" in child and "arguments" not in child:
        merged["minecraftArguments"] = child["minecraftArguments"]
        merged.pop("arguments", None)

    # downloads: 逐键子级优先
    downloads = dict(base.get("downloads") or {})
    downloads.update(child.get("downloads") or {})
    if downloads:
        merged["downloads"] = downloads

    for key in ("assetIndex", "assets", "javaVersion", "mainClass", "logging", "type", "releaseTime", "time"):
        if child.get(key) is not None:
            merged[key] = child[key]
    return merged


def resolve_inherits(version_json: dict, load_parent) -> dict:
    """
    递归合并 inheritsFrom 链，返回完整可用的 version JSON。
    load_parent: 回调 (parent_id) -> parent version JSON
    """
    merged = dict(version_json)
    seen = set()
    while merged.get("inheritsFrom"):
        pid = merged["inheritsFrom"]
        if pid in seen:
            raise DownloadError(f"版本继承链出现循环: {pid}")
        seen.add(pid)
        parent = load_parent(pid)
        if not parent:
            raise DownloadError(f"缺少被继承的父版本 {pid}，请先安装该版本。")
        merged = _merge(parent, merged)
    return merged


def is_legacy_version(version_json: dict) -> bool:
    """是否为 1.6 之前的远古版本（natives 目录与资源布局不同）。"""
    assets = version_json.get("assets")
    if assets == "pre-1.6":
        return True
    # 最古老的版本可能连 assets 都没有
    if not version_json.get("assetIndex") and not version_json.get("assets"):
        # 通过是否有 arguments 结构判断
        if "arguments" not in version_json:
            return True
    return False


# 年式版本号（2026 起）：正式版 26.2 / 26.1.1，预发布 26.3-snapshot-10 / 26.2-rc-2 / 26.2-pre-1
_YEAR_RELEASE_RE = re.compile(r"^\d{2}\.\d+(\.\d+)?$")
_YEAR_PRE_RE = re.compile(r"^\d{2}\.\d+(\.\d+)?-(snapshot|rc|pre)-?\d*$", re.I)
_WEEK_SNAPSHOT_RE = re.compile(r"^\d{2}w\d{2}[a-z]$", re.I)


def is_vanilla_id(version_id: str) -> bool:
    """粗略判断版本 ID 是否来自官方清单。"""
    if version_id in ("release", "snapshot"):
        return False
    s = str(version_id)
    if _WEEK_SNAPSHOT_RE.match(s):
        return True
    if _YEAR_RELEASE_RE.match(s) or _YEAR_PRE_RE.match(s):
        return True
    return "-" not in s or s.startswith(("1.", "0.", "c0.", "inf-", "rd-", "a1.", "b1."))


# 目录页版本筛选保底的经典版本
CLASSIC_CATALOG_VERSIONS = ("1.20.1", "1.19.2", "1.18.2", "1.16.5", "1.12.2")


def catalog_release_ids(manifest, limit: int = 8) -> list[str]:
    """从版本清单挑正式版 id 作筛选项：最新在前，经典版本保底。

    manifest 可以是完整清单 dict（含 versions），也可以是
    [{"version"/"id", "type"}] 行列表（backend.get_version_list 的返回值）。
    """
    if isinstance(manifest, dict):
        entries = manifest.get("versions") or []
    else:
        entries = manifest or []
    releases = []
    for v in entries:
        if not isinstance(v, dict) or v.get("type") != "release":
            continue
        vid = v.get("id") or v.get("version")
        if vid:
            releases.append(str(vid))
    out = []
    for vid in releases[:max(int(limit), 0)]:
        if vid not in out:
            out.append(vid)
    known = set(releases)
    for classic in CLASSIC_CATALOG_VERSIONS:
        if classic in known and classic not in out:
            out.append(classic)
    return out
