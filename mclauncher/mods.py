# -*- coding: utf-8 -*-
"""模组（单个 Mod）下载管理。

支持：
- Modrinth 在线搜索 / 按 MC 版本与加载器自动匹配版本 / 下载（含必需依赖）
- CurseForge 模组文件链接下载（自动解析 projectID）
- 本地 .jar 模组导入
- 实例 mods 目录管理（列表 / 删除）
"""
import json
import re
import shutil
from pathlib import Path
from urllib.parse import quote

from . import APP_NAME, APP_VERSION, utils
from .downloader import DownloadManager
from .instances import Instance

MODRINTH_API = "https://api.modrinth.com/v2"
CURSEFORGE_DOWNLOAD = "https://www.curseforge.com/api/v1/mods/{project_id}/files/{file_id}/download"
# PCL2 方案：Modrinth 官方 CDN 与 API 的国内镜像（MCIM）
MCIM_MIRROR = "https://mod.mcimirror.top"
MODRINTH_CDN = "https://cdn.modrinth.com"


def mirror_modrinth_url(url: str) -> str:
    from . import source
    return source.rewrite_to_mcim(url) or url


def modrinth_download_urls(urls) -> list:
    from . import source
    return source.modrinth_file_urls(urls)


def install_modrinth_mod(dm: DownloadManager, slug, instance: Instance,
                         mc_version=None, loader=None, on_progress=None,
                         use_mirror=True, version_id=None, mods_dir=None):
    """安装单个 Modrinth 模组：自动匹配 MC 版本与加载器，含必需依赖。

    use_mirror=True 时优先走 MCIM 国内镜像；镜像失败会自动回退官方 CDN。
    version_id 指定时安装该版本，不再自动挑最新。
    mods_dir 指定安装目录（版本隔离时是 versions/<id>/mods），缺省用实例 mods。
    """
    inst = instance
    inst.ensure_standard_dirs()
    dest_dir = _resolve_mods_dir(inst, mods_dir)
    if not mc_version:
        mc_version = detect_mc_version(inst)
    if loader is None:
        loader = detect_loader(inst)
    if version_id:
        try:
            version = dm.fetch_json(f"{MODRINTH_API}/version/{version_id}", timeout=API_TIMEOUT)
        except Exception as e:
            raise ModError(f"获取模组版本 {version_id} 失败: {e}") from e
        if not isinstance(version, dict):
            raise ModError(f"模组版本 {version_id} 无效")
    else:
        version = _pick_version(dm, slug, mc_version, loader)

    seen = set()
    seen_projects = set()
    downloaded = []

    def _download(v, depth=0):
        vid = v.get("id")
        if not vid or vid in seen or depth > 3:
            return
        seen.add(vid)
        if v.get("project_id"):
            seen_projects.add(v["project_id"])
        f = _primary_file(v)
        if not f or not f.get("url"):
            return
        dest = dest_dir / f["filename"]
        if on_progress:
            on_progress(f"下载模组 {f['filename']}", 0, 1)
        url = f["url"]
        tried = modrinth_download_urls(url) if use_mirror else [url]
        dm.download(tried[0], dest, sha1=f.get("sha1"), size=f.get("size"),
                    sha512=f.get("sha512"), urls=tried)
        downloaded.append(dest)
        # 必需依赖（如 Fabric API）递归下载。Modrinth 的依赖大多只给
        # project_id（version_id 为空），这类要按实例 MC 版本 + 加载器
        # 现场挑一个版本，和 PCL2 / HMCL 行为一致。
        for dep in v.get("dependencies") or []:
            if dep.get("dependency_type") != "required":
                continue
            dep_vid = dep.get("version_id")
            dep_pid = dep.get("project_id")
            try:
                if dep_vid:
                    dep_version = dm.fetch_json(f"{MODRINTH_API}/version/{dep_vid}", timeout=60)
                elif dep_pid and dep_pid not in seen_projects:
                    seen_projects.add(dep_pid)
                    dep_version = _pick_version(dm, dep_pid, mc_version, loader)
                else:
                    continue
                _download(dep_version, depth + 1)
            except Exception as e:
                utils.log.warning("下载依赖 %s 失败: %s",
                                  dep.get("file_name") or dep_vid or dep_pid, e)

    _download(version)
    if not downloaded:
        raise ModError(f"模组 {slug} 下载失败")
    return {
        "slug": slug,
        "version": version.get("version_number"),
        "files": [p.name for p in downloaded],
    }


class ModError(Exception):
    pass


# ================================================================ 搜索

# 统一排序键（下载页排序下拉，PCL2/HMCL 同款）→ (Modrinth index, CurseForge sortField)
# CurseForge ModsSearchSortField: 2=Popularity 3=LastUpdated 6=TotalDownloads 11=ReleasedDate
SORT_KEYS = {
    "relevance": ("relevance", 2),
    "downloads": ("downloads", 6),
    "updated": ("updated", 3),
    "newest": ("newest", 11),
    "follows": ("follows", 2),   # CF 没有关注数，退回人气
}


def mr_sort_index(sort, query="") -> str:
    """Modrinth search 的 index 参数；sort 留空保持旧行为（有词按相关度，无词按下载量）。"""
    mr = SORT_KEYS.get(str(sort or "").strip().lower(), ("", 0))[0]
    if mr:
        return mr
    return "relevance" if (query or "").strip() else "downloads"


def cf_sort_field(sort) -> int:
    """CurseForge search 的 sortField；sort 留空保持旧行为（按人气）。"""
    return SORT_KEYS.get(str(sort or "").strip().lower(), ("", 2))[1] or 2


def _mr_facets(project_type, game_version=None, categories=None):
    facets = [[f"project_type:{project_type}"]]
    if game_version:
        facets.append([f"versions:{game_version}"])
    cats = [c for c in (categories or []) if c]
    if cats:
        facets.append([f"categories:{c}" for c in cats])
    return json.dumps(facets)


def search_mods(dm: DownloadManager, query, limit=30, game_version=None, categories=None,
                sort="", offset=0):
    """搜索 Modrinth 模组（project_type:mod），官方与镜像短超时轮询。

    中文关键词先经 mcmod.cn 中文名数据库翻成英文名再搜（HMCL/PCL2 同款）。
    sort/offset：下载页排序与「加载更多」分页。
    """
    from . import mod_translate
    rec = mod_translate.best_cn_match(query, "mod", dm=dm)
    if rec:
        query = rec.get("subname") or rec.get("curseforge") or rec.get("name") or query
    params = {
        "query": query or " ",
        "facets": _mr_facets("mod", game_version, categories),
        "limit": limit,
        "index": mr_sort_index(sort, query),
    }
    if offset:
        params["offset"] = int(offset)
    last_err = None
    from . import source
    for base in source.modrinth_api_bases():
        try:
            data = dm.fetch_json(
                f"{base}/search", params=params, timeout=API_TIMEOUT, expand=False,
            )
            break
        except Exception as e:
            last_err = e
            data = None
    else:
        raise ModError(f"搜索模组失败（官方+镜像均不可用）: {last_err}")
    rows = [
        {
            "slug": h.get("slug"),
            "title": h.get("title", h.get("slug")),
            "description": (h.get("description") or "")[:120],
            "author": h.get("author", "?"),
            "downloads": h.get("downloads", 0),
            "tags": [str(c) for c in (h.get("display_categories") or h.get("categories") or [])[:6]],
            "updated": str(h.get("date_modified") or "")[:10],
            "source": "modrinth",
            "icon_url": h.get("icon_url") or "",
        }
        for h in data.get("hits", [])
    ]
    return mod_translate.annotate(rows, "mod")


def list_versions(dm: DownloadManager, slug, game_version=None, loaders=None):
    """列出模组版本（可按 MC 版本 / 加载器过滤），含依赖信息。"""
    params = {}
    if game_version:
        params["game_versions"] = json.dumps([game_version])
    if loaders:
        params["loaders"] = json.dumps(loaders)
    try:
        data = dm.fetch_json(
            f"{MODRINTH_API}/project/{slug}/version", params=params, timeout=API_TIMEOUT)
    except Exception as e:
        raise ModError(f"获取模组版本失败: {e}")
    result = []
    for v in data:
        files = []
        for f in v.get("files", []):
            files.append({
                "url": f.get("url"),
                "filename": f.get("filename"),
                "primary": f.get("primary", False),
                "size": f.get("size", 0),
                "sha1": (f.get("hashes") or {}).get("sha1"),
                "sha512": (f.get("hashes") or {}).get("sha512"),
            })
        result.append({
            "id": v.get("id"),
            "name": v.get("name"),
            "version_number": v.get("version_number"),
            "version_type": v.get("version_type") or "release",
            "game_versions": v.get("game_versions", []),
            "loaders": v.get("loaders", []),
            "files": files,
            "dependencies": v.get("dependencies", []),
            "date_published": str(v.get("date_published") or "")[:19],
            "downloads": v.get("downloads") or 0,
            "changelog": (v.get("changelog") or "")[:400],
        })
    return result


def _primary_file(version):
    if not version.get("files"):
        return None
    for f in version["files"]:
        if f.get("primary"):
            return f
    return version["files"][0]


# ================================================================ 中文搜索（别名目录 + 多源）

def search_mods_chinese(dm: DownloadManager, query, limit=30, api_key=None):
    """中文搜索模组：优先命中内置中文别名目录，其次按别名到多源搜索。

    返回结果统一为:
    {
        "source": "modrinth" | "curseforge",
        "slug" / "id",
        "title", "author", "downloads", "description"
    }
    """
    from . import catalog

    q = query.strip()
    if not q:
        return []
    hits = []

    # 1) 精确别名命中：详情 404 当没命中，继续走全文搜索，不把死 slug 当结果
    dead_slugs = set()
    slug, cf_id, title = catalog.lookup_mod_alias(q)
    if slug:
        got = _alias_to_modrinth_hits(dm, slug, title, limit)
        if got:
            hits.extend(got)
        else:
            dead_slugs.add(slug)
            utils.log.warning("别名 slug 无效，改走全文搜索: %s", slug)
    if cf_id:
        try:
            hits.extend(_alias_to_cf_hits(dm, cf_id, title, api_key))
        except Exception as e:
            utils.log.warning("别名命中后 CurseForge 查询失败 %s: %s", cf_id, e)
    if hits:
        return hits[:limit]

    # 2) 模糊匹配：跳过已经 404 的 slug，最多 3 条
    fuzzy = catalog.fuzzy_match_mod(q)
    seen_slug, seen_cf = set(), set()
    for slug, cf_id, title, alias in fuzzy[:3]:
        if slug and slug not in seen_slug and slug not in dead_slugs:
            seen_slug.add(slug)
            got = _alias_to_modrinth_hits(dm, slug, title, 1)
            if got:
                hits.extend(got)
            else:
                dead_slugs.add(slug)
        if cf_id and cf_id not in seen_cf and len(hits) < 3:
            seen_cf.add(cf_id)
            try:
                hits.extend(_alias_to_cf_hits(dm, cf_id, title, api_key))
            except Exception as e:
                utils.log.warning("模糊匹配 CurseForge 查询失败 %s: %s", cf_id, e)
        if len(hits) >= 4:
            break
    if hits:
        return hits[:limit]

    # 3) 别名未命中：回退到 Modrinth（英文原文）+ CurseForge 全文
    try:
        hits.extend(search_mods(dm, q, limit=limit))
    except Exception as e:
        utils.log.warning("中文搜索回退 Modrinth 失败: %s", e)
    try:
        hits.extend(search_curseforge(dm, q, limit=limit, api_key=api_key,
                                      class_id=CF_CLASS_MOD))
    except Exception as e:
        utils.log.warning("中文搜索回退 CurseForge 失败: %s", e)
    return hits[:limit]


def _alias_to_modrinth_hits(dm: DownloadManager, slug, title=None, limit=30):
    """按别名解析出的 Modrinth slug 生成搜索条目。"""
    hits = []

    def _add(sl):
        try:
            data = dm.fetch_json(f"{MODRINTH_API}/project/{sl}", timeout=API_TIMEOUT)
            hits.append({
                "source": "modrinth",
                "slug": data.get("slug", sl),
                "title": data.get("title") or title or sl,
                "author": (data.get("author") or "?"),
                "downloads": data.get("downloads", 0),
                "description": (data.get("description") or "")[:120],
                "matched_alias": True,
                "icon_url": data.get("icon_url") or "",
            })
        except Exception as e:
            utils.log.warning("Modrinth 项目 %s 查询失败: %s", sl, e)
    _add(slug)
    return hits


def _alias_to_cf_hits(dm: DownloadManager, cf_id, title=None, api_key=None):
    """按别名解析出的 CurseForge addonId 生成搜索条目。"""
    try:
        mod = cf_detail(dm, cf_id, api_key=api_key)
        return [{
            "source": "curseforge",
            "id": mod.get("id"),
            "title": mod.get("name") or title or str(cf_id),
            "author": ", ".join(a.get("name", "") for a in (mod.get("authors") or [])) or "?",
            "downloads": mod.get("downloadCount") or 0,
            "description": (mod.get("summary") or "")[:120],
            "matched_alias": True,
        }]
    except Exception as e:
        utils.log.warning("CurseForge 项目 %s 查询失败: %s", cf_id, e)
        return []


# ================================================================ 实例信息检测

def detect_loader(instance: Instance):
    """从已安装版本名推断加载器。"""
    for vid in instance.installed_ids():
        low = vid.lower()
        if "fabric" in low:
            return "fabric"
        if "quilt" in low:
            return "quilt"
        if "neoforge" in low:
            return "neoforge"
        if "forge" in low:
            return "forge"
    return None


def _mc_version_from_text(text):
    if not text:
        return None
    s = str(text).strip()
    if re.fullmatch(r"1\.\d+(\.\d+)?", s):
        return s
    m = re.search(r"(1\.\d+(?:\.\d+)?)", s)
    return m.group(1) if m else None


def detect_mc_version(instance: Instance):
    """优先用实例/整合包元数据，再取已装版本里最高的 1.x。"""
    meta = instance.meta() or {}
    pack = meta.get("modpack") if isinstance(meta.get("modpack"), dict) else {}
    for cand in (pack.get("mc_version"), meta.get("mc_version")):
        hit = _mc_version_from_text(cand)
        if hit:
            return hit
    found = []
    for vid in instance.installed_ids():
        hit = _mc_version_from_text(vid)
        if not hit:
            continue
        parts = tuple(int(x) for x in hit.split("."))
        found.append((parts, hit))
    if found:
        found.sort()
        return found[-1][1]
    return None


def list_instance_mods(instance: Instance):
    """列出实例 mods 目录中的 .jar。"""
    return [Path(r["path"]) for r in list_mod_entries_at(instance.path / "mods") if r.get("enabled")]


def list_instance_mod_entries(instance: Instance) -> list:
    """已装模组，含 .jar.disabled。"""
    return list_mod_entries_at(instance.path / "mods")


def list_mod_entries_at(mods_dir) -> list:
    folder = Path(mods_dir)
    if not folder.is_dir():
        return []
    rows = []
    for p in sorted(folder.iterdir()):
        if not p.is_file():
            continue
        low = p.name.lower()
        if low.endswith(".jar"):
            rows.append({"filename": p.name, "enabled": True, "bytes": p.stat().st_size, "path": str(p)})
        elif low.endswith(".jar.disabled") or low.endswith(".disabled"):
            rows.append({"filename": p.name, "enabled": False, "bytes": p.stat().st_size, "path": str(p)})
    return rows


def _mod_file_at(mods_dir, filename: str) -> Path:
    folder = Path(mods_dir).resolve()
    p = (folder / filename).resolve()
    if p.parent != folder:
        raise ModError(f"非法模组路径: {filename}")
    return p


def _mod_file(instance: Instance, filename: str) -> Path:
    return _mod_file_at(instance.path / "mods", filename)


def delete_mod(instance: Instance, filename: str, mods_dir=None):
    p = _mod_file_at(mods_dir or (instance.path / "mods"), filename)
    if not p.is_file():
        raise ModError(f"模组文件不存在: {filename}")
    from . import trash
    trash.trash_or_delete(p)


def set_mod_enabled(instance: Instance, filename: str, enabled: bool, mods_dir=None) -> str:
    """启用/禁用模组：foo.jar <-> foo.jar.disabled。"""
    folder = Path(mods_dir) if mods_dir else instance.path / "mods"
    p = _mod_file_at(folder, filename)
    if not p.is_file():
        alt_name = filename + ("" if filename.lower().endswith(".disabled") else ".disabled")
        alt = _mod_file_at(folder, alt_name)
        if enabled and alt.is_file():
            p = alt
        else:
            raise ModError(f"模组文件不存在: {filename}")
    name = p.name
    if enabled:
        if name.lower().endswith(".jar.disabled"):
            dest = p.with_name(name[: -len(".disabled")])
        elif name.lower().endswith(".disabled"):
            dest = p.with_name(name[: -len(".disabled")])
        else:
            return name
        if dest.exists():
            raise ModError(f"启用失败，已存在: {dest.name}")
        p.rename(dest)
        return dest.name
    if name.lower().endswith(".disabled"):
        return name
    dest = p.with_name(name + ".disabled")
    if dest.exists():
        raise ModError(f"禁用失败，已存在: {dest.name}")
    p.rename(dest)
    return dest.name


# ================================================================ 安装

def _pick_version(dm, slug, mc_version, loader):
    """选择最匹配实例 MC 版本与加载器的模组版本。"""
    versions = list_versions(dm, slug, game_version=mc_version,
                             loaders=[loader] if loader else None)
    if not versions and mc_version:
        utils.log.warning("%s 没有与 MC %s + %s 完全匹配的版本，尝试仅按 MC 版本匹配",
                          slug, mc_version, loader or "任意加载器")
        versions = list_versions(dm, slug, game_version=mc_version)
    if not versions:
        raise ModError(f"模组 {slug} 没有支持 MC {mc_version} 的版本")
    if loader:
        for v in versions:
            if loader in v.get("loaders", []):
                return v
        utils.log.warning("%s 的最新版本未声明支持 %s，可能不兼容", slug, loader)
    return versions[0]  # API 按时间倒序，第一个是最新


_CF_RE = re.compile(r"curseforge\.com/minecraft/(mc-mods|modpacks)/[^/]+/files/(\d+)")
_CF_SLUG_RE = re.compile(r"curseforge\.com/minecraft/(mc-mods|modpacks)/([^/?#]+)")

# ================================================================
# CurseForge 数据源（HMCL 官方 API 方案）
# 搜索/元数据：官方 API api.curseforge.com/v1（需 x-api-key，默认 key 已嵌入配置）
# 文件下载：  API 返回 downloadUrl 优先，否则 edge.forgecdn.net 直链
# 参考：https://docs.curseforge.com/rest-api
# ================================================================
CF_OFFICIAL_API = "https://api.curseforge.com/v1"
CF_WEB_API = "https://www.curseforge.com/api/v1/mods"   # 官网内部 API（无需 key，伪装浏览器兜底）
# 官方不可达时走 MCIM。BMCLAPI /curseforge/v1 实测 404，不再列入。
def cf_api_bases():
    from . import source
    return source.cf_api_bases()


API_TIMEOUT = (3, 8)

CF_CLASS_MOD = 6          # Mods
CF_CLASS_MODPACK = 4471   # Modpacks
CF_CLASS_RESOURCEPACK = 12  # Texture Packs
CF_CLASS_SHADER = 6552
CF_CLASS_DATAPACK = 6945
CF_CLASS_WORLD = 17


def _cf_api_headers(api_key=None):
    """官方 API 标准请求头。"""
    headers = {
        "Accept": "application/json",
        "User-Agent": f"{APP_NAME}/{APP_VERSION}",
    }
    if api_key:
        headers["x-api-key"] = api_key
    return headers


def _cf_file_cdn_url(file_id: int, filename: str, host="edge.forgecdn.net") -> str:
    """按 HMCL 规则拼 CurseForge CDN 直链。

    https://{host}/files/{fileId//1000}/{fileId%1000}/{fileName}
    """
    name = quote(str(filename), safe="._-+()[]")
    return f"https://{host}/files/{int(file_id) // 1000}/{int(file_id) % 1000}/{name}"


def cf_mod_download_urls(addon_id, file_id, filename=None, download_url=None):
    """CurseForge 文件下载候选（HMCL/PCL 同款：CDN 优先，官网 download 放最后）。

    www.curseforge.com/api/v1/.../download 常被 Cloudflare 直接 403，不能当主源。
    """
    urls = []
    if download_url:
        urls.append(download_url)
    if filename:
        from . import source
        for host in ("mediafilez.forgecdn.net", "edge.forgecdn.net"):
            cdn = _cf_file_cdn_url(file_id, filename, host=host)
            urls.append(cdn)
            mirrored = source.rewrite_to_mcim(cdn)
            if mirrored:
                urls.append(mirrored)
    urls.append(f"{CF_OFFICIAL_API}/mods/{addon_id}/files/{file_id}/download")
    urls.append(f"https://mod.mcimirror.top/curseforge/v1/mods/{addon_id}/files/{file_id}/download")
    urls.append(f"{CF_WEB_API}/{addon_id}/files/{file_id}/download")
    seen, out = set(), []
    for u in urls:
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _candidate_cf_urls(addon_id, file_id, filename=None):
    """向后兼容：旧调用不传 downloadUrl。"""
    return cf_mod_download_urls(addon_id, file_id, filename=filename)


def _cf_items(data):
    """从官方/镜像 JSON 里取出 data 列表。"""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        items = data.get("data")
        if isinstance(items, list):
            return items
    return []


def _cf_normalize_file(file_obj):
    """保证文件条目带有可直接使用的 downloadUrl。"""
    if not isinstance(file_obj, dict):
        return file_obj
    out = dict(file_obj)
    fid = out.get("id")
    name = out.get("fileName")
    if not out.get("downloadUrl") and fid and name:
        out["downloadUrl"] = _cf_file_cdn_url(int(fid), name)
    return out


def _cf_fetch(dm: DownloadManager, path, api_key=None, params=None, timeout=None):
    """GET CurseForge /v1/{path}：官方优先，镜像兜底。返回已解析 JSON。"""
    from .config import CONFIG as _CFG
    if not api_key:
        api_key = _CFG.get("curseforge_api_key")
    last_err = None
    headers = _cf_api_headers(api_key)
    for base in cf_api_bases():
        url = f"{base}{path}"
        try:
            data = dm.fetch_json(
                url, params=params, headers=headers,
                timeout=timeout or API_TIMEOUT, expand=False,
            )
            if data is None:
                last_err = f"{url} 返回空响应"
                continue
            return data
        except Exception as e:
            last_err = e
            utils.log.warning("CurseForge 端点不可用 %s: %s", url, e)
    raise ModError(f"CurseForge 请求失败 {path}: {last_err}")


def _cf_post(dm: DownloadManager, path, body, api_key=None, timeout=60):
    """POST CurseForge /v1/{path}：官方优先，镜像兜底。"""
    from .config import CONFIG as _CFG
    if not api_key:
        api_key = _CFG.get("curseforge_api_key")
    last_err = None
    headers = _cf_api_headers(api_key)
    headers["Content-Type"] = "application/json"
    for base in cf_api_bases():
        url = f"{base}{path}"
        try:
            resp = dm.session.post(
                url, json=body, headers=headers, timeout=timeout or API_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            if data is None:
                last_err = f"{url} 返回空响应"
                continue
            return data
        except Exception as e:
            last_err = e
            utils.log.warning("CurseForge POST 不可用 %s: %s", url, e)
    raise ModError(f"CurseForge POST 失败 {path}: {last_err}")


def murmur2_hash(data: bytes, seed: int = 1) -> int:
    """CurseForge 使用的 MurmurHash2（32 位）。"""
    m = 0x5bd1e995
    r = 24
    length = len(data)
    h = (seed ^ length) & 0xFFFFFFFF
    n = length // 4
    for i in range(n):
        k = int.from_bytes(data[i * 4:i * 4 + 4], "little")
        k = (k * m) & 0xFFFFFFFF
        k ^= k >> r
        k = (k * m) & 0xFFFFFFFF
        h = (h * m) & 0xFFFFFFFF
        h ^= k
    rest = data[n * 4:]
    if len(rest) >= 3:
        h ^= rest[2] << 16
    if len(rest) >= 2:
        h ^= rest[1] << 8
    if len(rest) >= 1:
        h ^= rest[0]
        h = (h * m) & 0xFFFFFFFF
    h ^= h >> 13
    h = (h * m) & 0xFFFFFFFF
    h ^= h >> 15
    return h & 0xFFFFFFFF


def cf_fingerprint(path) -> int:
    """CurseForge 文件指纹：剔除空白字节后的 MurmurHash2。"""
    raw = Path(path).read_bytes()
    cleaned = bytes(b for b in raw if b not in (9, 10, 13, 32))
    return murmur2_hash(cleaned, 1)


def cf_match_fingerprints(dm: DownloadManager, fingerprints, api_key=None) -> dict:
    """批量指纹匹配 POST /v1/fingerprints。

    返回 {fingerprint: {"projectID", "fileID", "fileName"}}；请求失败的
    分片记日志后跳过（对应文件按未匹配处理）。
    """
    fps = []
    for f in fingerprints or []:
        try:
            fps.append(int(f))
        except (TypeError, ValueError):
            continue
    out = {}
    for i in range(0, len(fps), 100):
        chunk = fps[i:i + 100]
        try:
            data = _cf_post(dm, "/fingerprints", {"fingerprints": chunk}, api_key=api_key)
        except Exception as e:
            utils.log.warning("CurseForge 指纹匹配失败: %s", e)
            continue
        matches = ((data or {}).get("data") or {}).get("exactMatches") or []
        for hit in matches:
            f = hit.get("file") or {}
            fp = f.get("fileFingerprint")
            pid = f.get("modId") or hit.get("id")
            fid = f.get("id")
            if fp and pid and fid:
                out[int(fp)] = {
                    "projectID": int(pid),
                    "fileID": int(fid),
                    "fileName": f.get("fileName") or "",
                }
    return out


def cf_files_by_ids(dm: DownloadManager, file_ids, api_key=None):
    """批量查询文件元数据 POST /v1/mods/files，返回 {fileId: file}。"""
    ids = []
    for x in file_ids or []:
        try:
            ids.append(int(x))
        except (TypeError, ValueError):
            continue
    if not ids:
        return {}
    out = {}
    for i in range(0, len(ids), 50):
        chunk = ids[i:i + 50]
        try:
            data = _cf_post(dm, "/mods/files", {"fileIds": chunk}, api_key=api_key)
        except Exception as e:
            utils.log.warning("批量查询 CurseForge 文件失败: %s", e)
            continue
        for f in _cf_items(data):
            nf = _cf_normalize_file(f)
            fid = nf.get("id")
            if fid is not None:
                out[int(fid)] = nf
    return out


_CF_TRANSLATE_KIND = {CF_CLASS_MOD: "mod", CF_CLASS_MODPACK: "modpack"}


def search_curseforge(dm: DownloadManager, query=None, limit=30, api_key=None,
                      class_id=CF_CLASS_MOD, slug=None, game_version=None,
                      categories=None, sort="", offset=0):
    """搜索 CurseForge（官方 API 优先，国内镜像兜底）。

    categories 是 canonical key 的展示名碎片（见 catalog_files.CF_TYPE_TOKENS）；
    CF 的 categoryFilter 对 slug 约束不稳定，这里改为拉回结果后按分类名过滤。
    中文关键词经 mcmod.cn 中文名数据库翻译：先按 slug 精确找本体，再按英文名全文搜。
    sort/offset：下载页排序与「加载更多」分页。
    """
    from . import mod_translate
    kind = _CF_TRANSLATE_KIND.get(class_id)
    cn_slug = None
    if query and not slug and kind:
        rec = mod_translate.best_cn_match(query, kind, dm=dm)
        if rec:
            cn_slug = (rec.get("curseforge") or "").strip() or None
            query = rec.get("subname") or rec.get("name") or query
    params = {
        "gameId": 432,
        "classId": class_id,
        "sortField": cf_sort_field(sort),
        "sortOrder": "desc",
        "pageSize": limit * 2 if categories else limit,
        "index": int(offset or 0),
    }
    if query:
        params["searchFilter"] = query
    if slug:
        params["slug"] = slug
    if game_version:
        params["gameVersion"] = game_version

    data = _cf_fetch(dm, "/mods/search", api_key=api_key, params=params)
    hits = [_cf_norm(m) for m in _cf_items(data)]
    if cn_slug and not offset:
        # 中文命中的本体置顶（只在第一页做，翻页别重复置顶）；
        # 全文搜没带回来就按 slug 单独取一次
        front = [h for h in hits if (h.get("slug") or "").lower() == cn_slug.lower()]
        if front:
            hits = front + [h for h in hits if h not in front]
        else:
            try:
                exact_params = {
                    "gameId": 432, "classId": class_id, "slug": cn_slug,
                    "pageSize": 3, "index": 0,
                }
                if game_version:
                    exact_params["gameVersion"] = game_version
                exact = _cf_fetch(dm, "/mods/search", api_key=api_key, params=exact_params)
                hits = [_cf_norm(m) for m in _cf_items(exact)] + hits
            except Exception as e:
                utils.log.debug("中文名精确 slug 查询失败 %s: %s", cn_slug, e)
    if categories:
        tokens = [str(t).lower() for t in categories if t]
        hits = [h for h in hits if any(
            tok and any(tok in c for c in h.get("cf_categories") or [])
            for tok in tokens)]
    hits = hits[:limit]
    return mod_translate.annotate(hits, kind) if kind else hits


def _cf_norm(m):
    return {
        "source": "curseforge",
        "id": m.get("id"),
        "slug": m.get("slug"),
        "title": m.get("name") or m.get("title") or "?",
        "author": ", ".join(a.get("name", "") for a in (m.get("authors") or [])) or "?",
        "downloads": m.get("downloadCount") or m.get("downloads") or 0,
        "summary": (m.get("summary") or "")[:120],
        "cf_categories": [str((c or {}).get("name") or "").lower()
                          for c in (m.get("categories") or []) if isinstance(c, dict)],
    }


def cf_detail(dm: DownloadManager, addon_id, api_key=None):
    """获取模组/整合包详情（含 latestFiles、slug、mainFileId）。"""
    data = _cf_fetch(dm, f"/mods/{addon_id}", api_key=api_key)
    mod = data.get("data") if isinstance(data, dict) else data
    if mod and isinstance(mod, dict) and mod.get("id"):
        return mod
    raise ModError(f"获取 CurseForge 详情失败: 项目 {addon_id} 无有效数据")


def cf_by_slug(dm: DownloadManager, slug, class_id=CF_CLASS_MODPACK, api_key=None):
    """按 slug 精确查找项目，返回官方原始对象（含 id / mainFileId / latestFiles）。"""
    if not slug:
        return None
    params = {"gameId": 432, "slug": slug, "pageSize": 5, "index": 0}
    if class_id is not None:
        params["classId"] = class_id
    data = _cf_fetch(dm, "/mods/search", api_key=api_key, params=params)
    items = _cf_items(data)
    if items:
        return items[0]
    if class_id is not None:
        return cf_by_slug(dm, slug, class_id=None, api_key=api_key)
    return None


def cf_files(dm: DownloadManager, addon_id, api_key=None, game_version=None,
             mod_loader=None, page_size=50):
    """获取 CurseForge 模组/整合包文件列表，并补全 downloadUrl。

    官方 API 优先，MCIM / BMCLAPI 镜像兜底。可选按 MC 版本 / 加载器过滤。
    返回 [{id, fileName, downloadUrl, gameVersions, ...}]。
    """
    from .config import CONFIG as _CFG
    if not api_key:
        api_key = _CFG.get("curseforge_api_key")

    params = {"pageSize": page_size, "index": 0}
    if game_version:
        params["gameVersion"] = game_version
    if mod_loader:
        params["modLoaderType"] = mod_loader

    last_err = None
    saw_success = False
    headers = _cf_api_headers(api_key)
    for base in cf_api_bases():
        url = f"{base}/mods/{addon_id}/files"
        try:
            data = dm.fetch_json(url, params=params, headers=headers, timeout=60)
            saw_success = True
            items = _cf_items(data)
            if items:
                return [_cf_normalize_file(f) for f in items]
            last_err = f"{url} 返回空文件列表"
        except Exception as e:
            last_err = e
            utils.log.warning("CurseForge 文件列表端点不可用 %s: %s", url, e)
    if saw_success:
        raise ModError(f"CurseForge 项目 {addon_id} 没有可下载文件")
    raise ModError(f"获取 CurseForge 文件列表失败: {last_err or '所有端点不可用'}")


def _cf_download_urls(addon_id, file_id, filename=None):
    """向后兼容：旧调用不传 filename 时仍返回能用的 URL。"""
    urls = _candidate_cf_urls(addon_id, file_id, filename)
    return urls


def _resolve_mods_dir(instance: Instance, mods_dir=None) -> Path:
    """安装目标目录：显式 mods_dir 优先（版本隔离），否则实例 mods。"""
    if mods_dir:
        folder = Path(mods_dir)
        utils.ensure_dir(folder)
        return folder
    folder = instance.path / "mods"
    utils.ensure_dir(folder)
    return folder


# CurseForge dependencies[].relationType == 3 表示必装依赖
CF_RELATION_REQUIRED = 3


def install_curseforge_mod(dm: DownloadManager, addon_id, instance: Instance,
                           mc_version=None, loader=None, api_key=None, on_progress=None,
                           file_id=None, mods_dir=None, _seen=None, _depth=0):
    """安装 CurseForge 模组：自动匹配实例 MC 版本与加载器，含必需依赖。"""
    inst = instance
    inst.ensure_standard_dirs()
    dest_dir = _resolve_mods_dir(inst, mods_dir)
    if _seen is None:
        _seen = set()
    _seen.add(str(addon_id))
    if not mc_version:
        mc_version = detect_mc_version(inst)
    if loader is None:
        loader = detect_loader(inst)

    mod = cf_detail(dm, addon_id, api_key=api_key)
    # 优先 latestFiles（官方 API 详情自带），兜底调 cf_files
    files = mod.get("latestFiles") or []
    if not files:
        try:
            files = cf_files(dm, addon_id, api_key=api_key, page_size=100)
        except Exception as e:
            utils.log.warning("cf_files 兜底也失败: %s", e)
    if file_id:
        hit = next((x for x in files if str(x.get("id")) == str(file_id)), None)
        if not hit:
            try:
                files = cf_files(dm, addon_id, api_key=api_key, page_size=100)
            except Exception:
                files = files or []
            hit = next((x for x in files if str(x.get("id")) == str(file_id)), None)
        if not hit:
            raise ModError(f"找不到 CurseForge 文件 {file_id}")
        f = hit
    else:
        if not files:
            raise ModError("该模组没有可下载的文件")

        candidates = [f for f in files
                      if not mc_version or mc_version in (f.get("gameVersions") or [])]
        if loader:
            pref = [f for f in candidates
                    if any(loader.lower() in (gv or "").lower() for gv in (f.get("gameVersions") or []))]
            if pref:
                candidates = pref
        if not candidates:
            utils.log.warning("没有与 MC %s 完全匹配的文件，使用最新文件", mc_version)
            candidates = files

        f = candidates[0]
    file_id = f.get("id")
    if file_id is None:
        raise ModError("模组文件信息缺失")
    filename = f.get("fileName") or f"mod-{addon_id}-{file_id}.jar"
    download_url = f.get("downloadUrl")  # API 可能返回此字段
    dest = dest_dir / filename
    # 官方 API 带 sha1（algo=1）：传给下载器做完整性校验，已有相同文件时直接跳过
    sha1 = next((h.get("value") for h in (f.get("hashes") or [])
                 if h.get("algo") == 1 and h.get("value")), None)

    last_err = None
    # 候选 URL：API 返回的 downloadUrl → 带文件名的 CDN 直链 → 不带文件名的通用 URL
    url_sets = []
    if download_url:
        url_sets.append([download_url])
    url_sets.append(_candidate_cf_urls(addon_id, file_id, filename))
    url_sets.append(_candidate_cf_urls(addon_id, file_id, None))

    tried = set()
    ok = False
    for urls in url_sets:
        for url in urls:
            if url in tried:
                continue
            tried.add(url)
            try:
                if on_progress:
                    on_progress(f"下载 CurseForge 模组 {filename}", 0, 1)
                dm.download(url, dest, sha1=sha1, timeout=900)
                ok = True
                break
            except Exception as e:
                last_err = e
                utils.remove_tree(dest)
        if ok:
            break
    if not ok:
        raise ModError(f"CurseForge 模组下载失败: {last_err}")

    files = [dest.name]
    # 必需依赖递归安装（对齐 PCL2 / HMCL）。单个依赖失败只警告，不拖垮主模组。
    if _depth < 3:
        for dep in f.get("dependencies") or []:
            if dep.get("relationType") != CF_RELATION_REQUIRED:
                continue
            dep_id = dep.get("modId")
            if not dep_id or str(dep_id) in _seen:
                continue
            try:
                sub = install_curseforge_mod(
                    dm, dep_id, inst, mc_version=mc_version, loader=loader,
                    api_key=api_key, on_progress=on_progress, mods_dir=dest_dir,
                    _seen=_seen, _depth=_depth + 1)
                files.extend((sub or {}).get("files") or [])
            except Exception as e:
                utils.log.warning("安装 CurseForge 依赖 %s 失败: %s", dep_id, e)
    return {"source": "curseforge", "title": mod.get("name"), "files": files}


def install_cf_mod(dm: DownloadManager, url, instance: Instance, on_progress=None, mods_dir=None):
    """从 CurseForge 链接安装模组（文件页链接或模组主页链接都行）。"""
    dest_dir = _resolve_mods_dir(instance, mods_dir)
    m = _CF_RE.search(url)
    if m:
        file_id = m.group(2)
        html = dm.fetch_text(url, timeout=60)
        pm = re.search(r'"projectID"\s*:\s*(\d+)', html)
        if not pm:
            pm = re.search(r"projectID=(\d+)", html)
        if not pm:
            raise ModError("无法从页面解析项目 ID（CurseForge 页面结构变化或网络受限），请改用 Modrinth 或本地文件")
        project_id = pm.group(1)
        dest = dest_dir / f"mod-{project_id}-{file_id}.jar"
        if on_progress:
            on_progress("下载 CurseForge 模组", 0, 1)
        last_err = None
        for u in _cf_download_urls(project_id, file_id):
            try:
                dm.download(u, dest, timeout=600)
                return {"source": "curseforge", "files": [dest.name]}
            except Exception as e:
                last_err = e
                utils.remove_tree(dest)
        raise ModError(f"CurseForge 模组下载失败: {last_err}")
    m = _CF_SLUG_RE.search(url)
    if m:
        slug = m.group(2)
        hits = search_curseforge(dm, slug=slug, limit=5, class_id=CF_CLASS_MOD)
        if not hits:
            raise ModError("找不到该 CurseForge 模组")
        return install_curseforge_mod(dm, hits[0]["id"], instance, on_progress=on_progress,
                                      mods_dir=mods_dir)
    raise ModError("无法识别的 CurseForge 链接")


def install_mod_from_source(dm: DownloadManager, source, instance: Instance,
                            mc_version=None, loader=None, on_progress=None,
                            version_id=None, mods_dir=None):
    """
    统一安装入口：支持
    - Modrinth 链接 (modrinth.com/mod/<slug>) 或直接 slug
    - CurseForge 文件页链接
    - .jar 直链 / 本地 .jar 文件
    """
    inst = instance
    inst.ensure_standard_dirs()
    dest_dir = _resolve_mods_dir(inst, mods_dir)
    s = str(source)
    if re.match(r"^https?://", s):
        m = re.search(r"modrinth\.com/mod(?:s)?/([^/?#]+)", s)
        if m:
            return install_modrinth_mod(dm, m.group(1), inst, mc_version, loader, on_progress,
                                        version_id=version_id, mods_dir=dest_dir)
        if "curseforge.com" in s:
            return install_cf_mod(dm, s, inst, on_progress, mods_dir=dest_dir)
        if s.split("?")[0].lower().endswith(".jar"):
            name = s.split("/")[-1].split("?")[0] or "mod.jar"
            dm.download(s, dest_dir / name, timeout=600)
            return {"source": "url", "files": [name]}
        raise ModError("无法识别的链接：支持 Modrinth 模组链接、CurseForge 文件页链接、.jar 直链")
    p = Path(s)
    if p.is_file():
        if p.suffix.lower() != ".jar":
            raise ModError("本地文件只支持 .jar 模组")
        shutil.copy2(p, dest_dir / p.name)
        return {"source": "file", "files": [p.name]}
    # 不是链接也不是文件：当作 Modrinth slug
    return install_modrinth_mod(dm, s, inst, mc_version, loader, on_progress,
                                version_id=version_id, mods_dir=dest_dir)


# ================================================================ 光影 / 资源包 / 数据包

CONTENT_KINDS = {
    "shader": {"mr": "shader", "subdir": "shaderpacks", "cf": CF_CLASS_SHADER},
    "resourcepack": {"mr": "resourcepack", "subdir": "resourcepacks", "cf": CF_CLASS_RESOURCEPACK},
    "datapack": {"mr": "datapack", "subdir": "datapacks", "cf": CF_CLASS_DATAPACK},
}


def search_modrinth_projects(dm: DownloadManager, query, project_type, limit=30,
                             game_version=None, categories=None, sort="", offset=0):
    """按 project_type 搜 Modrinth（shader / resourcepack / datapack / mod）。"""
    params = {
        "query": query or " ",
        "facets": _mr_facets(project_type, game_version, categories),
        "limit": limit,
        "index": mr_sort_index(sort, query),
    }
    if offset:
        params["offset"] = int(offset)
    last_err = None
    data = None
    from . import source
    for base in source.modrinth_api_bases():
        try:
            data = dm.fetch_json(
                f"{base}/search", params=params, timeout=API_TIMEOUT, expand=False)
            break
        except Exception as e:
            last_err = e
    if data is None:
        raise ModError(f"搜索 {project_type} 失败: {last_err}")
    rows = []
    for h in data.get("hits") or []:
        cats = h.get("display_categories") or h.get("categories") or []
        rows.append({
            "source": "modrinth",
            "slug": h.get("slug"),
            "title": h.get("title") or h.get("slug"),
            "description": (h.get("description") or "")[:160],
            "author": h.get("author") or "?",
            "downloads": h.get("downloads") or 0,
            "tags": [str(c) for c in cats[:6]],
            "updated": str(h.get("date_modified") or "")[:10],
            "icon_url": h.get("icon_url") or "",
        })
    return rows


def list_content_files(instance: Instance, subdir: str):
    folder = instance.path / subdir
    if not folder.is_dir():
        return []
    return sorted(
        [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in (".zip", ".jar")],
        key=lambda p: p.name.lower(),
    )


def delete_content_file(instance: Instance, subdir: str, filename: str):
    folder = (instance.path / subdir).resolve()
    p = (folder / filename).resolve()
    if p.parent != folder:
        raise ModError(f"非法路径: {filename}")
    # 文件夹形式的包（资源包/数据包都允许解压放置）也要能删
    if p.exists():
        from . import trash
        trash.trash_or_delete(p)


def install_modrinth_content(dm: DownloadManager, slug, instance: Instance, subdir: str,
                             mc_version=None, on_progress=None, version_id=None):
    inst = instance
    inst.ensure_standard_dirs()
    dest_dir = inst.path / subdir
    dest_dir.mkdir(parents=True, exist_ok=True)
    if version_id:
        try:
            chosen = dm.fetch_json(f"{MODRINTH_API}/version/{version_id}", timeout=API_TIMEOUT)
        except Exception as e:
            raise ModError(f"获取版本失败: {e}") from e
        versions = [chosen] if chosen else []
    else:
        versions = list_versions(dm, slug, game_version=mc_version or None)
        if not versions:
            versions = list_versions(dm, slug)
    if not versions:
        raise ModError(f"{slug} 没有可下载版本")
    f = _primary_file(versions[0])
    if not f or not f.get("url"):
        raise ModError(f"{slug} 没有可下载文件")
    dest = dest_dir / f["filename"]
    if on_progress:
        on_progress(f"下载 {f['filename']}", 0, 1)
    tried = modrinth_download_urls(f["url"])
    dm.download(tried[0], dest, sha1=f.get("sha1"), size=f.get("size"),
                sha512=f.get("sha512"), urls=tried, timeout=900)
    return {"source": "modrinth", "slug": slug, "files": [dest.name]}


def install_cf_content(dm: DownloadManager, addon_id, instance: Instance, subdir: str,
                       mc_version=None, api_key=None, on_progress=None, file_id=None):
    inst = instance
    inst.ensure_standard_dirs()
    dest_dir = inst.path / subdir
    dest_dir.mkdir(parents=True, exist_ok=True)
    mod = cf_detail(dm, addon_id, api_key=api_key)
    files = mod.get("latestFiles") or []
    if not files:
        files = cf_files(dm, addon_id, api_key=api_key, page_size=50)
    if not files:
        raise ModError("没有可下载文件")
    if file_id:
        f = next((x for x in files if str(x.get("id")) == str(file_id)), None)
        if not f:
            files = cf_files(dm, addon_id, api_key=api_key, page_size=50)
            f = next((x for x in files if str(x.get("id")) == str(file_id)), None)
        if not f:
            raise ModError(f"找不到文件 {file_id}")
    else:
        candidates = [x for x in files
                      if not mc_version or mc_version in (x.get("gameVersions") or [])]
        f = (candidates or files)[0]
    file_id = f.get("id")
    filename = f.get("fileName") or f"file-{addon_id}-{file_id}.zip"
    dest = dest_dir / filename
    download_url = f.get("downloadUrl")
    last_err = None
    urls = []
    if download_url:
        urls.append(download_url)
    urls.extend(_candidate_cf_urls(addon_id, file_id, filename))
    tried = set()
    for url in urls:
        if not url or url in tried:
            continue
        tried.add(url)
        try:
            if on_progress:
                on_progress(f"下载 {filename}", 0, 1)
            dm.download(url, dest, timeout=900)
            return {"source": "curseforge", "id": addon_id, "files": [dest.name]}
        except Exception as e:
            last_err = e
            utils.remove_tree(dest)
    raise ModError(f"下载失败: {last_err}")


def install_content_from_source(dm: DownloadManager, instance: Instance, subdir: str,
                                extra=None, on_progress=None):
    """path / url / curseforge id / modrinth slug。"""
    extra = extra or {}
    inst = instance
    inst.ensure_standard_dirs()
    dest_dir = inst.path / subdir
    dest_dir.mkdir(parents=True, exist_ok=True)
    mc_version = extra.get("game_version") or extra.get("mc_version")
    if isinstance(mc_version, str) and (not mc_version.strip() or mc_version.startswith("全部")):
        mc_version = None
    path = extra.get("path")
    if path and Path(path).is_file():
        src = Path(path)
        dest = dest_dir / src.name
        shutil.copy2(src, dest)
        return {"source": "file", "files": [dest.name]}
    url = extra.get("url")
    if url and str(url).startswith("http"):
        name = str(url).split("/")[-1].split("?")[0] or "pack.zip"
        dest = dest_dir / name
        if on_progress:
            on_progress(f"下载 {name}", 0, 1)
        dm.download(str(url), dest, timeout=900)
        return {"source": "url", "files": [dest.name]}
    src = str(extra.get("source") or "").lower()
    file_id = extra.get("file_id") or extra.get("version_id")
    if extra.get("id") and (src.startswith("curse") or extra.get("file_id")):
        return install_cf_content(
            dm, extra["id"], inst, subdir, mc_version, on_progress=on_progress,
            file_id=file_id)
    slug = extra.get("slug") or extra.get("name")
    if not slug:
        raise ModError("缺少 slug / 文件 / 链接")
    return install_modrinth_content(
        dm, slug, inst, subdir, mc_version, on_progress=on_progress,
        version_id=extra.get("version_id"))

