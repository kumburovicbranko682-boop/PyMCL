# -*- coding: utf-8 -*-
"""整合包支持：Modrinth（.mrpack，含在线搜索）与 CurseForge（.zip）。"""
import json
import os
import re
import shutil
import tempfile
import zipfile
from pathlib import Path

from . import APP_NAME, utils
from .downloader import DownloadManager, DownloadError
from .installer import Installer, InstallError
from .instances import Instance
from . import manifest as manifest_mod

MODRINTH_API = "https://api.modrinth.com/v2"
CURSEFORGE_DOWNLOAD = "https://www.curseforge.com/api/v1/mods/{project_id}/files/{file_id}/download"


class ModpackError(Exception):
    pass


def _emit(on_progress, msg):
    msg = str(msg or "").strip()
    if not msg:
        return
    utils.log.info("%s", msg)
    if on_progress:
        on_progress(msg, 0, 1)


def _pack_retry_worthwhile(err) -> bool:
    """加载器/安装器基础设施失败时，换整合包版本通常也没用。"""
    msg = str(err)
    stop = (
        "Forge 没有支持 Minecraft",
        "NeoForge 没有支持 Minecraft",
        "无法获取 Forge 版本列表",
        "Forge 处理器失败",
        "Forge 处理器运行失败",
        "缺少原版客户端",
        "已取消",
        "用户取消",
    )
    return not any(s in msg for s in stop)


def cf_manifest_loaders(mf):
    """CurseForge 标准字段在 minecraft.modLoaders；少数旧包才写在根上。"""
    mc = mf.get("minecraft") if isinstance(mf.get("minecraft"), dict) else {}
    loaders = mc.get("modLoaders") or mf.get("modLoaders") or []
    return loaders if isinstance(loaders, list) else []


# ================================================================ CurseForge 搜索（BMCLAPI 镜像）

def search_cf_modpacks(dm: DownloadManager, query, limit=25, api_key=None,
                       game_version=None, categories=None):
    """搜索 CurseForge 整合包（走 BMCLAPI 国内镜像，无需 API key）。"""
    from .mods import search_curseforge, CF_CLASS_MODPACK
    from .catalog_files import cf_category_tokens
    tokens = []
    for c in categories or []:
        tokens.extend(cf_category_tokens(c) or [str(c).lower()])
    hits = search_curseforge(dm, query=query, limit=limit, api_key=api_key,
                             class_id=CF_CLASS_MODPACK,
                             game_version=game_version, categories=tokens or None)
    for h in hits:
        h["description"] = h.pop("summary", "")
        h.pop("cf_categories", None)
    return hits


def _cf_pack_is_server(file_obj) -> bool:
    """服务端包不能当客户端整合包装。"""
    if not isinstance(file_obj, dict):
        return False
    if file_obj.get("isServerPack") is True:
        return True
    name = str(file_obj.get("fileName") or "").lower()
    if "client" in name:
        return False
    return "server" in name


def _pick_cf_pack_file(files, main_file_id=None):
    """从文件列表里挑整合包 zip：优先客户端、mainFileId，其次正式版。"""
    if not files:
        return None
    zips = [f for f in files if str(f.get("fileName") or "").lower().endswith(".zip")]
    pool = [f for f in (zips or list(files)) if not _cf_pack_is_server(f)]
    if not pool:
        pool = zips or list(files)
    if main_file_id is not None:
        try:
            mid = int(main_file_id)
        except (TypeError, ValueError):
            mid = None
        if mid is not None:
            for f in pool:
                try:
                    if int(f.get("id")) == mid:
                        return f
                except (TypeError, ValueError):
                    continue
    releases = [f for f in pool if f.get("releaseType") == 1]
    return (releases or pool)[0]


def resolve_cf_modpack_file(dm: DownloadManager, addon_id, api_key=None, cf_slug=None):
    """解析 CurseForge 整合包的最新文件（不下载）。

    返回 {addon_id, file_id, fileName, downloadUrl, name, slug}。
    有 slug 时以 slug 解析出的项目 ID 为准，避免目录里写错 addon_id。
    文件来源以 /files 列表为准（latestFiles 经常不是最新版）。
    """
    from .mods import CF_CLASS_MODPACK, cf_by_slug, cf_detail, cf_files

    last_err = None
    main_file_id = None
    mod_name = f"curseforge:{addon_id}"

    if cf_slug:
        try:
            hit = cf_by_slug(dm, cf_slug, class_id=CF_CLASS_MODPACK, api_key=api_key)
            if hit:
                addon_id = hit.get("id") or addon_id
                mod_name = hit.get("name") or mod_name
                main_file_id = hit.get("mainFileId")
        except Exception as e:
            last_err = e
            utils.log.warning("slug 解析失败(%s): %s", cf_slug, e)

    try:
        mod = cf_detail(dm, addon_id, api_key=api_key)
        mod_name = mod.get("name") or mod_name
        if not main_file_id:
            main_file_id = mod.get("mainFileId")
        if not cf_slug:
            cf_slug = mod.get("slug")
    except Exception as e:
        last_err = e
        utils.log.warning("cf_detail 失败: %s", e)

    try:
        all_files = cf_files(dm, addon_id, api_key=api_key, page_size=100)
        f = _pick_cf_pack_file(all_files, main_file_id)
        if f and f.get("id"):
            return {
                "addon_id": addon_id,
                "file_id": f.get("id"),
                "fileName": f.get("fileName"),
                "downloadUrl": f.get("downloadUrl"),
                "name": mod_name,
                "slug": cf_slug,
            }
        last_err = last_err or f"项目 {addon_id} 文件列表无可选文件"
    except Exception as e:
        last_err = e
        utils.log.warning("cf_files 失败: %s", e)

    if cf_slug:
        try:
            file_id, filename, download_url = _cf_latest_from_html(dm, cf_slug)
            return {
                "addon_id": addon_id,
                "file_id": file_id,
                "fileName": filename,
                "downloadUrl": download_url,
                "name": _cf_name_from_html(dm, cf_slug) or mod_name,
                "slug": cf_slug,
            }
        except Exception as e:
            last_err = e
            utils.log.warning("HTML 抓取失败(%s): %s", cf_slug, e)

    raise ModpackError(
        f"获取 CurseForge 整合包文件失败: {last_err or '无可用文件'}"
        f"（slug={cf_slug}, addon_id={addon_id}）"
    )


def install_cf_modpack(dm: DownloadManager, addon_id, instance: Instance,
                       api_key=None, on_progress=None, cancel=None, cf_slug=None,
                       file_id=None):
    """安装 CurseForge 整合包：解析最新文件后下载 zip，再复用 install_cf_zip。"""
    from .mods import _cf_download_urls, cf_files

    if file_id:
        files = cf_files(dm, addon_id, api_key=api_key, page_size=50)
        f = next((x for x in files if str(x.get("id")) == str(file_id)), None)
        if not f:
            raise ModpackError(f"找不到整合包文件 {file_id}")
        info = {
            "addon_id": addon_id,
            "file_id": f.get("id"),
            "fileName": f.get("fileName"),
            "downloadUrl": f.get("downloadUrl"),
            "name": cf_slug or f"curseforge:{addon_id}",
        }
    else:
        info = resolve_cf_modpack_file(dm, addon_id, api_key=api_key, cf_slug=cf_slug)
    addon_id = info["addon_id"]
    file_id = info["file_id"]
    filename = info.get("fileName")
    download_url = info.get("downloadUrl")
    mod_name = info.get("name") or f"curseforge:{addon_id}"
    last_err = None

    tmp = Path(tempfile.gettempdir()) / f"pymcl_cfpack_{addon_id}_{file_id}.zip"
    cands = []
    if download_url:
        cands.append(download_url)
    if filename:
        cands.extend(_cf_download_urls(addon_id, file_id, filename))
    cands.extend(_cf_download_urls(addon_id, file_id, None))
    tried = set()
    for url in cands:
        if url in tried:
            continue
        tried.add(url)
        try:
            if on_progress:
                on_progress(f"下载整合包 {mod_name}", 0, 1)
            dm.download(url, tmp, timeout=1800)
            break
        except Exception as e:
            last_err = e
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
    else:
        raise ModpackError(f"整合包下载失败: {last_err}")
    try:
        return install_cf_zip(dm, tmp, instance, on_progress=on_progress, cancel=cancel)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _cf_latest_from_html(dm: DownloadManager, slug: str, class_path="modpacks"):
    """从 CurseForge 网页抓取某项目的最新文件信息。

    返回 (file_id, filename, download_url)。
    不依赖 API key，直接从 HTML 正则提取。
    页面: https://www.curseforge.com/minecraft/{class_path}/{slug}/files
    """
    import re as _re
    page_url = f"https://www.curseforge.com/minecraft/{class_path}/{slug}/files"
    headers = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/120.0.0.0 Safari/537.36"),
        "Referer": "https://www.curseforge.com/",
    }
    html = dm.fetch_text(page_url, timeout=60, headers=headers)
    # 提取文件 ID：/minecraft/{class_path}/{slug}/files/{数字}
    pat = _re.compile(rf"/minecraft/{class_path}/{_re.escape(slug)}/files/(\d+)")
    ids = _re.findall(pat, html)
    if not ids:
        raise ModpackError(f"网页未找到文件链接: {page_url}")
    fid = int(ids[0])  # 最新文件（页面倒序）

    # 从 HTML 提取文件名
    fn = None
    pm = _re.search(rf"files/{fid}/[^\"'<> ]+\.zip", html)
    if pm:
        fn = pm.group(0).rsplit("/", 1)[-1]
    if not fn:
        pm = _re.search(rf'data-filename\s*=\s*["\']([^"\']+\.zip)', html)
        if pm:
            fn = pm.group(1)
    if not fn:
        fn = f"modpack-{fid}.zip"
    return fid, fn, None


def _cf_name_from_html(dm: DownloadManager, slug: str, class_path="modpacks"):
    """从 CurseForge 项目页抓取整合包名称（HTML 兜底）。"""
    import re as _re
    url = f"https://www.curseforge.com/minecraft/{class_path}/{slug}"
    headers = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/120.0.0.0 Safari/537.36"),
    }
    try:
        html = dm.fetch_text(url, timeout=60, headers=headers)
        pm = _re.search(r'<title>\s*(.+?)\s*-\s*Modpacks\s*-\s*CurseForge\s*</title>', html, _re.I)
        if pm:
            return pm.group(1).strip()
    except Exception:
        pass
    return None


# ================================================================ 中文搜索（别名目录 + 多源）

def search_modpacks_chinese(dm: DownloadManager, query, limit=25, api_key=None,
                            game_version=None, categories=None):
    """中文搜索整合包：优先命中内置中文别名目录，否则回退到多源搜索。"""
    from . import catalog
    from .catalog_files import type_key

    q = query.strip()
    if not q:
        return []
    cat_keys = [type_key(c) for c in (categories or []) if type_key(c)]
    hits = []

    # 1) 精确别名
    slug, cf_id, title = catalog.lookup_modpack_alias(q)
    if slug:
        try:
            data = dm.fetch_json(f"{MODRINTH_API}/project/{slug}", timeout=60)
            if _match_filters_mr(data, game_version, cat_keys):
                gvs = data.get("game_versions") or []
                desc = (data.get("description") or "")[:120]
                if gvs:
                    desc = f"MC {', '.join(gvs[:3])} · {desc}"
                hits.append({
                    "source": "modrinth",
                    "slug": data.get("slug", slug),
                    "title": data.get("title") or title or slug,
                    "author": (data.get("author") or "?"),
                    "downloads": data.get("downloads", 0),
                    "description": desc,
                    "matched_alias": True,
                })
        except Exception as e:
            utils.log.warning("整合包别名命中后 Modrinth 查询失败: %s", e)
    if cf_id:
        try:
            from .mods import cf_detail, _cf_norm
            mod = cf_detail(dm, cf_id, api_key=api_key)
            if _match_filters_cf(mod, game_version, cat_keys):
                gvs = []
                for idx in (mod.get("latestFilesIndexes") or []):
                    gv = (idx or {}).get("gameVersion")
                    if gv and gv not in gvs:
                        gvs.append(gv)
                desc = (mod.get("summary") or "")[:120]
                if gvs:
                    desc = f"MC {', '.join(gvs[:3])} · {desc}"
                hits.append({
                    "source": "curseforge",
                    "id": mod.get("id"),
                    "slug": mod.get("slug"),
                    "title": mod.get("name") or title or str(cf_id),
                    "author": ", ".join(a.get("name", "") for a in (mod.get("authors") or [])) or "?",
                    "downloads": mod.get("downloadCount") or 0,
                    "description": desc,
                    "matched_alias": True,
                })
        except Exception as e:
            utils.log.warning("整合包别名命中后 CurseForge 查询失败: %s", e)
    if hits:
        return hits[:limit]

    # 2) 回退到多源搜索
    try:
        hits.extend(modrinth_search(dm, q, limit=limit,
                                    game_version=game_version, categories=cat_keys))
    except Exception as e:
        utils.log.warning("中文搜索回退 Modrinth 整合包失败: %s", e)
    try:
        hits.extend(search_cf_modpacks(dm, q, limit=limit, api_key=api_key,
                                       game_version=game_version, categories=cat_keys))
    except Exception as e:
        utils.log.warning("中文搜索回退 CurseForge 整合包失败: %s", e)
    return hits[:limit]


def _match_filters_mr(project: dict, game_version=None, cat_keys=None) -> bool:
    """别名命中的 Modrinth 项目按版本/分类过滤（项目自带字段，可直接比对）。"""
    from .catalog_files import TYPE_FACETS
    if game_version:
        gvs = [str(v) for v in (project.get("game_versions") or [])]
        if gvs and game_version not in gvs:
            return False
    if cat_keys:
        cats = {str(c).lower() for c in (project.get("categories") or [])}
        want = {c for k in cat_keys for c in TYPE_FACETS.get(k, [])}
        if want and not (want & cats):
            return False
    return True


def _match_filters_cf(mod: dict, game_version=None, cat_keys=None) -> bool:
    """别名命中的 CurseForge 项目按版本/分类过滤。"""
    if game_version:
        gvs = [str((i or {}).get("gameVersion") or "") for i in (mod.get("latestFilesIndexes") or [])]
        gvs = [g for g in gvs if g]
        if gvs and game_version not in gvs:
            return False
    if cat_keys:
        from .catalog_files import CF_TYPE_TOKENS
        names = " ".join(str((c or {}).get("name") or "").lower()
                         for c in (mod.get("categories") or []) if isinstance(c, dict))
        tokens = [t for k in cat_keys for t in CF_TYPE_TOKENS.get(k, [])]
        if tokens and not any(t in names for t in tokens):
            return False
    return True


# ================================================================ Modrinth

def modrinth_search(dm: DownloadManager, query, limit=25,
                    game_version=None, categories=None):
    """搜索 Modrinth 整合包（官方优先，MCIM 镜像兜底）。"""
    from .mods import mirror_modrinth_url, _mr_facets
    from .catalog_files import category_facets

    facets = _mr_facets("modpack", game_version, categories)
    params = {
        "query": query,
        "facets": facets,
        "limit": limit,
        "index": "relevance",
    }
    if categories:
        cats = category_facets(categories[0] if len(categories) == 1 else "")
        # 多类型时 _mr_facets 生成 OR 组，单类型走 facets 里的精确分类
        if len(categories) == 1 and not cats:
            params["facets"] = _mr_facets("modpack", game_version, None)
    last_err = None
    from . import source
    for api_base in source.modrinth_api_bases():
        try:
            data = dm.fetch_json(
                f"{api_base}/search", params=params, timeout=(3, 8), expand=False)
            break
        except Exception as e:
            last_err = e
            utils.log.warning("Modrinth 搜索端点不可用 %s: %s", api_base, e)
    else:
        raise ModpackError(f"搜索 Modrinth 整合包失败: {last_err}")
    result = []
    for hit in data.get("hits", []):
        result.append({
            "slug": hit.get("slug"),
            "title": hit.get("title", hit.get("slug")),
            "description": (hit.get("description") or "")[:120],
            "author": hit.get("author", "?"),
            "downloads": hit.get("downloads", 0),
            "game_versions": hit.get("versions") or [],
        })
    return result


def modrinth_project(dm: DownloadManager, slug):
    """获取 Modrinth 项目详情。"""
    last_err = None
    from . import source
    for api_base in source.modrinth_api_bases():
        try:
            return dm.fetch_json(f"{api_base}/project/{slug}", timeout=12, expand=False)
        except Exception as e:
            last_err = e
            utils.log.warning("Modrinth 项目端点不可用 %s: %s", api_base, e)
    raise ModpackError(f"找不到 Modrinth 项目 {slug}: {last_err}")


def modrinth_versions(dm: DownloadManager, slug):
    """列出某个整合包的版本。"""
    try:
        data = dm.fetch_json(f"{MODRINTH_API}/project/{slug}/version", timeout=60)
    except Exception as e:
        raise ModpackError(f"获取整合包版本失败: {e}")
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
        })
    return result


def _pick_mrpack_file(versions):
    """优先选正式版客户端 .mrpack，跳过 server pack 和纯 jar。"""
    ranked = []
    for v in versions or []:
        vtype = v.get("version_type") or "release"
        rank_type = 0 if vtype == "release" else 1 if vtype == "beta" else 2
        files = v.get("files") or []
        mrpacks = [f for f in files
                   if str(f.get("filename") or "").lower().endswith(".mrpack") and f.get("url")]
        if not mrpacks:
            continue
        client = [f for f in mrpacks if "server" not in str(f.get("filename") or "").lower()]
        pool = client or mrpacks
        primary = [f for f in pool if f.get("primary")]
        ranked.append((rank_type, primary or pool, v))
    if not ranked:
        return None, None
    ranked.sort(key=lambda item: item[0])
    _rank, pool, v = ranked[0]
    return pool[0], v


def _mrpack_candidates(versions, limit=5):
    """按正式版优先列出可尝试的 .mrpack（版本不兼容时自动换下一个）。"""
    items = []
    seen = set()
    for v in versions or []:
        f, _ = _pick_mrpack_file([v])
        if not f:
            continue
        url = f.get("url")
        if not url or url in seen:
            continue
        seen.add(url)
        vtype = v.get("version_type") or "release"
        rank = 0 if vtype == "release" else 1 if vtype == "beta" else 2
        items.append((rank, f, v))
    items.sort(key=lambda x: x[0])
    return [(f, v) for _rank, f, v in items[:limit]]


def _copy_embedded_versions(tmpdir, instance: Instance, plain=False):
    """把整合包自带的 versions JSON 先拷进实例，供自定义版本安装。

    CF/mrpack 的自带版本在 overrides/versions 下；“直接压缩的 .minecraft”
    的版本就在包根 versions/（plain=True）。
    """
    copied = []
    dest_root = instance.versions_dir()
    folders = ("overrides", "client-overrides", "") if plain else ("overrides", "client-overrides")
    for folder in folders:
        src = Path(tmpdir) / folder / "versions"
        if not src.is_dir():
            continue
        for ver_dir in src.iterdir():
            if not ver_dir.is_dir():
                continue
            json_file = ver_dir / f"{ver_dir.name}.json"
            if not json_file.is_file():
                continue
            target = dest_root / ver_dir.name
            shutil.copytree(ver_dir, target, dirs_exist_ok=True)
            copied.append(ver_dir.name)
    return copied


def _nested_marker_root(tmpdir: Path, marker: str, depth: int = 3):
    """zip 根目录没有 marker 时向下找包含它的目录（“压缩了文件夹本身”的包）。"""
    for level in range(1, depth + 1):
        hits = sorted(p for p in tmpdir.glob("*/" * level + marker) if p.is_file())
        if hits:
            return hits[0].parent
    return None


_PLAIN_MC_STRONG = frozenset(("mods", "config", "versions", "saves"))


def _looks_like_mc_dir(root: Path) -> bool:
    """像不像一个 .minecraft 目录（按标志性子目录判断）。"""
    try:
        names = {p.name.lower() for p in root.iterdir()}
    except OSError:
        return False
    if "manifest.json" in names or "modrinth.index.json" in names:
        return False
    return bool(names & _PLAIN_MC_STRONG)


def _plain_pack_root(tmpdir: Path):
    """识别“直接压缩的 .minecraft 目录”整合包（可能还套了一层文件夹）。"""
    if _looks_like_mc_dir(tmpdir):
        return tmpdir
    for level in (1, 2):
        for cand in sorted(tmpdir.glob("*/" * level)):
            if cand.is_dir() and _looks_like_mc_dir(cand):
                return cand
    return None


def _plain_pack_version(root: Path):
    """从 versions/<名>/<名>.json 推断 MC 版本与加载器（没有则返回 None）。"""
    vdir = root / "versions"
    if not vdir.is_dir():
        return None
    plain = None
    for vd in sorted(vdir.iterdir()):
        if not vd.is_dir():
            continue
        jf = vd / f"{vd.name}.json"
        if not jf.is_file():
            continue
        j = utils.read_json(jf, None) or {}
        vid = str(j.get("id") or vd.name)
        libs = " ".join(str((l or {}).get("name") or "") for l in (j.get("libraries") or []))
        mc = str(j.get("inheritsFrom") or "") or None
        if "-forge" in vid or "minecraftforge" in libs:
            m = re.search(r"-forge-?(.+)$", vid)
            return {"mc": mc, "loader": "forge", "loader_version": m.group(1) if m else ""}
        if "neoforge" in vid or "neoforge" in libs:
            m = re.search(r"-neoforge-?(.+)$", vid)
            return {"mc": mc, "loader": "neoforge", "loader_version": m.group(1) if m else ""}
        if vid.startswith("fabric-loader-"):
            m = re.match(r"^fabric-loader-([^-]+)-(.+)$", vid)
            return {"mc": (m.group(2) if m else mc),
                    "loader": "fabric-loader", "loader_version": m.group(1) if m else ""}
        if vid.startswith("quilt-loader-"):
            m = re.match(r"^quilt-loader-([^-]+)-(.+)$", vid)
            return {"mc": (m.group(2) if m else mc),
                    "loader": "quilt-loader", "loader_version": m.group(1) if m else ""}
        if plain is None and manifest_mod.looks_like_minecraft_version(vid):
            plain = {"mc": vid, "loader": None, "loader_version": ""}
    return plain


def _resolve_pack_minecraft(dm, declared, on_progress=None):
    """整合包声明的 MC 版本 -> 官方可安装版本。"""
    if not declared:
        return None
    if manifest_mod.looks_like_minecraft_version(declared):
        alt = manifest_mod.resolve_playable_version(dm, declared)
        if alt and alt != declared and on_progress:
            on_progress(f"Minecraft {declared} 不在官方列表，改用 {alt}", 0, 1)
        return alt or declared
    alt = manifest_mod.resolve_playable_version(dm, declared)
    if alt:
        if on_progress:
            on_progress(f"声明版本 {declared} 不是 Minecraft 版本，改用 {alt}", 0, 1)
        return alt
    return None


def install_mrpack_by_slug(dm: DownloadManager, slug, instance: Instance,
                           on_progress=None, cancel=None, force=False, java=None,
                           version_id=None):
    """通过 Modrinth slug 安装整合包；某个包版本装不上就自动换下一个。"""
    proj = modrinth_project(dm, slug)
    title = proj.get("title") or slug
    ptype = proj.get("project_type") or ""
    if ptype != "modpack":
        raise ModpackError(
            f"「{title}」({slug}) 是 {ptype or '未知类型'}，不是整合包。"
            "请到「下载 → Mod」搜索安装，或换一个真正的整合包（机械动力可搜黄铜协奏曲 CBC）。"
        )
    versions = modrinth_versions(dm, slug)
    if version_id:
        pinned = [v for v in versions if str(v.get("id")) == str(version_id)]
        if pinned:
            versions = pinned
    if not versions:
        raise ModpackError(f"整合包 {slug} 没有可下载的版本")
    candidates = _mrpack_candidates(versions, limit=1 if version_id else 5)
    if not candidates:
        names = [f.get("filename") for f in (versions[0].get("files") or [])]
        raise ModpackError(
            f"「{title}」没有 .mrpack 文件（最新版本文件: {names or '无'}）。"
            "它可能不是 Modrinth 整合包。"
        )
    last_err = None
    _emit(on_progress, f"整合包「{title}」共 {len(versions)} 个版本，将依次尝试 {len(candidates)} 个")
    for i, (pack_file, v) in enumerate(candidates):
        if cancel and cancel():
            raise ModpackError("已取消")
        label = v.get("version_number") or pack_file.get("filename") or ""
        fname = pack_file.get("filename") or ""
        _emit(on_progress, f"[{i + 1}/{len(candidates)}] 尝试 {label} ({fname})")
        try:
            return install_mrpack(dm, pack_file.get("url"), instance,
                                  on_progress=on_progress, cancel=cancel,
                                  force=force, java=java)
        except (ModpackError, InstallError, manifest_mod.VersionNotFound) as e:
            last_err = e
            _emit(on_progress, f"{label} 安装失败: {e}")
            if not _pack_retry_worthwhile(e):
                raise ModpackError(f"「{title}」{label} 无法安装: {e}") from e
            if i == len(candidates) - 1:
                break
            _emit(on_progress, "该版本装不上，尝试下一个整合包版本…")
    raise ModpackError(f"「{title}」多个版本均无法安装: {last_err}")


def _fetch_mrpack(dm: DownloadManager, source):
    """source 可以是本地文件路径或 URL；返回本地临时路径。"""
    if re.match(r"^https?://", str(source)):
        tmp = Path(tempfile.gettempdir()) / f"pymcl_mrpack_{abs(hash(str(source)))}.mrpack"
        dm.download(str(source), tmp, timeout=900)
        return tmp
    p = Path(source)
    if not p.is_file():
        raise ModpackError(f"找不到整合包文件: {source}")
    return p


def install_mrpack(dm: DownloadManager, source, instance: Instance,
                   on_progress=None, cancel=None, force=False, java=None):
    """安装 .mrpack 整合包到指定实例。"""
    downloaded = bool(re.match(r"^https?://", str(source)))
    _emit(on_progress, f"{'下载' if downloaded else '读取'}整合包: {source}")
    pack_path = _fetch_mrpack(dm, source)
    _emit(on_progress, f"解压整合包 {Path(pack_path).name}")
    tmpdir = Path(tempfile.mkdtemp(prefix="pymcl_mrpack_"))
    try:
        try:
            utils.safe_extract_zip(pack_path, tmpdir)
        except (zipfile.BadZipFile, ValueError) as e:
            raise ModpackError(f"不是有效的 mrpack 文件: {e}")

        pack_root = tmpdir
        index_file = tmpdir / "modrinth.index.json"
        if not index_file.is_file():
            nested = _nested_marker_root(tmpdir, "modrinth.index.json")
            if nested is not None:
                pack_root = nested
                index_file = nested / "modrinth.index.json"
                _emit(on_progress, f"modrinth.index.json 位于子目录 {nested.name}/，按该层作为包根安装")
        if not index_file.is_file():
            if (pack_root / "manifest.json").is_file():
                raise ModpackError(
                    "这是 CurseForge 整合包（含 manifest.json），请按 zip 安装，不是 mrpack")
            raise ModpackError(
                "整合包缺少 modrinth.index.json。"
                "常见原因：下载到的是模组 jar，而不是 .mrpack 整合包。"
            )
        idx = utils.read_json(index_file, None) or {}
        if idx.get("formatVersion", 1) > 1:
            _emit(on_progress, f"mrpack 格式版本 {idx.get('formatVersion')} 较新，可能不完全兼容")

        pack_name = idx.get("name") or Path(pack_path).stem
        pack_ver = idx.get("versionId") or "?"
        deps = idx.get("dependencies") or {}
        files = idx.get("files") or []
        _emit(on_progress, f"解析整合包 {pack_name} 版本 {pack_ver}")
        _emit(on_progress, f"声明依赖: {deps}")
        _emit(on_progress, f"索引文件 {len(files)} 个")

        if instance.path.is_dir():
            instance.ensure_standard_dirs()
        else:
            instance.create()
        _emit(on_progress, f"安装到实例 {instance.name} ({instance.path})")
        installer = Installer(instance, dm, on_progress=on_progress or dm.on_progress, cancel=cancel)

        embedded = _copy_embedded_versions(pack_root, instance)
        if embedded:
            _emit(on_progress, f"发现整合包自带版本: {', '.join(embedded)}")

        declared = deps.get("minecraft") or idx.get("versionId")
        mc_version = None
        if declared in embedded:
            mc_version = declared
            _emit(on_progress, f"使用整合包自带 Minecraft {mc_version}")
        else:
            mc_version = _resolve_pack_minecraft(dm, deps.get("minecraft"), on_progress)
            if not mc_version:
                mc_version = _resolve_pack_minecraft(dm, idx.get("versionId"), on_progress)
        if not mc_version:
            raise ModpackError("整合包没有声明可用的 Minecraft 版本")
        _emit(on_progress, f"安装 Minecraft {mc_version}")
        installer.install_version(mc_version, force=force, java=java)

        # 加载器
        loader = None
        loader_vid = None
        for key in ("fabric-loader", "quilt-loader", "forge", "neoforge"):
            if deps.get(key):
                loader = key
                break
        if loader:
            loader_version = deps[loader]
            _emit(on_progress, f"安装加载器 {loader} {loader_version} (Minecraft {mc_version})")
            loader_vid = install_loader(installer, loader, loader_version, mc_version, force=force)
            _emit(on_progress, f"加载器安装完成: {loader_vid}")
        else:
            _emit(on_progress, "整合包未声明 Forge/Fabric/Quilt/NeoForge，仅安装原版")

        # 整合包文件：每个文件一条任务，镜像失败立刻换官方 CDN（不要拆成两个会互相计失败的任务）
        from .mods import modrinth_download_urls
        tasks = []
        sha512_checks = []
        for f in idx.get("files", []):
            env = f.get("env") or {}
            if env.get("client") in ("unsupported", "server"):
                continue
            rel = f.get("path")
            downloads = [u for u in (f.get("downloads") or []) if u]
            if not rel or not downloads:
                continue
            dest = (instance.path / rel).resolve()
            # 防路径穿越
            if not str(dest).startswith(str(instance.path.resolve()) + os.sep):
                raise ModpackError(f"整合包文件路径非法: {rel}")
            hashes = f.get("hashes") or {}
            tasks.append((
                modrinth_download_urls(downloads), dest,
                hashes.get("sha1"), f.get("size"), hashes.get("sha512"),
            ))
            if hashes.get("sha512"):
                sha512_checks.append((dest, hashes["sha512"]))
        if tasks:
            _emit(on_progress, f"开始下载整合包文件（{len(tasks)} 个，镜像失败会改走官方源）")
            try:
                dm.download_all(tasks, message="下载整合包文件")
            except DownloadError as e:
                raise ModpackError(str(e)) from e
            for dest, want in sha512_checks:
                if not dest.is_file() or utils.sha512_file(dest) != want.lower():
                    raise ModpackError(f"文件校验失败 (sha512): {dest}")
            _emit(on_progress, "整合包文件下载完成")
        else:
            _emit(on_progress, "索引中没有客户端文件需要下载")

        # overrides
        for overrides_dir in ("overrides", "client-overrides"):
            src = pack_root / overrides_dir
            if src.is_dir():
                _emit(on_progress, f"复制 {overrides_dir}")
                _copy_tree_over(src, instance.path)
                break

        pack_meta = {
            "name": idx.get("name", Path(pack_path).stem),
            "version": idx.get("versionId"),
            "mc_version": mc_version,
            "loader": f"{loader}-{deps.get(loader)}" if loader else None,
            "source": "modrinth",
            "instance": instance.name,
        }
        instance.set_meta("modpack", pack_meta)
        instance.set_meta("mc_version", loader_vid or mc_version)
        _emit(on_progress, f"整合包 {pack_meta['name']} 安装完成 -> 实例 {instance.name}")
        return pack_meta
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
        if downloaded:
            try:
                pack_path.unlink(missing_ok=True)
            except OSError:
                pass


# ================================================================ CurseForge

def install_cf_zip(dm: DownloadManager, source, instance: Instance,
                   on_progress=None, cancel=None, force=False, java=None):
    """安装 CurseForge 整合包 zip（本地文件或直链）。"""
    downloaded = False
    if re.match(r"^https?://", str(source)):
        tmp = Path(tempfile.gettempdir()) / f"pymcl_cfpack_{abs(hash(str(source)))}.zip"
        dm.download(str(source), tmp, timeout=900)
        pack_path = tmp
        downloaded = True
    else:
        pack_path = Path(source)
        if not pack_path.is_file():
            raise ModpackError(f"找不到整合包文件: {source}")

    tmpdir = Path(tempfile.mkdtemp(prefix="pymcl_cfpack_"))
    try:
        try:
            utils.safe_extract_zip(pack_path, tmpdir)
        except (zipfile.BadZipFile, ValueError) as e:
            raise ModpackError(f"不是有效的整合包 zip: {e}")

        manifest_file = tmpdir / "manifest.json"
        pack_root = tmpdir
        if not manifest_file.is_file():
            nested = _nested_marker_root(tmpdir, "manifest.json")
            if nested is not None:
                pack_root = nested
                manifest_file = nested / "manifest.json"
                _emit(on_progress, f"manifest.json 位于子目录 {nested.name}/，按该层作为包根安装")
        if not manifest_file.is_file():
            # 没有 manifest.json：按“直接压缩的 .minecraft 目录”整合包安装
            return _install_plain_zip(dm, tmpdir, instance, pack_path,
                                      on_progress=on_progress, cancel=cancel,
                                      force=force, java=java)
        mf = utils.read_json(manifest_file, None) or {}
        if mf.get("manifestType") != "minecraftModpack":
            raise ModpackError("manifest.json 不是 minecraftModpack 类型")
        _emit(on_progress, f"解析 CurseForge 整合包 {mf.get('name') or pack_path.stem} 版本 {mf.get('version', '?')}")

        if instance.path.is_dir():
            instance.ensure_standard_dirs()
        else:
            instance.create()
        _emit(on_progress, f"安装到实例 {instance.name} ({instance.path})")
        installer = Installer(instance, dm, on_progress=on_progress or dm.on_progress, cancel=cancel)

        embedded = _copy_embedded_versions(pack_root, instance)
        if embedded:
            _emit(on_progress, f"发现整合包自带版本: {', '.join(embedded)}")

        declared = (mf.get("minecraft") or {}).get("version")
        mc_version = declared if declared in embedded else _resolve_pack_minecraft(
            dm, declared, on_progress)
        if not mc_version:
            raise ModpackError("整合包没有声明可用的 Minecraft 版本")
        _emit(on_progress, f"安装 Minecraft {mc_version}")
        installer.install_version(mc_version, force=force, java=java)

        loaders = cf_manifest_loaders(mf)
        primary = next((l for l in loaders if l.get("primary")), loaders[0] if loaders else None)
        if not primary or not primary.get("id"):
            raise ModpackError(
                "整合包没有声明 Forge/Fabric 加载器（manifest.json 的 minecraft.modLoaders）。"
                "只装原版的话，游戏不会加载 mods 文件夹。"
            )
        loader_id = primary.get("id", "")
        m = re.match(r"^([a-zA-Z]+)-(.+)$", loader_id)
        if not m:
            raise ModpackError(f"无法解析加载器 id: {loader_id}")
        loader, loader_version = m.group(1), m.group(2)
        loader = {"fabric": "fabric-loader", "quilt": "quilt-loader"}.get(loader, loader)
        _emit(on_progress, f"安装加载器 {loader} {loader_version} (Minecraft {mc_version})")
        loader_vid = install_loader(installer, loader, loader_version, mc_version, force=force)
        _emit(on_progress, f"加载器安装完成: {loader_vid}")

        # mods 文件：先批量查元数据，再用 CDN 直链（官网 /download 会被 Cloudflare 403）
        from .mods import cf_files_by_ids, cf_mod_download_urls
        raw_files = [f for f in (mf.get("files") or []) if f.get("projectID") and f.get("fileID")]
        meta = {}
        if raw_files:
            try:
                meta = cf_files_by_ids(dm, [f.get("fileID") for f in raw_files])
            except Exception as e:
                utils.log.warning("批量查询整合包 Mod 元数据失败，将仅用 CDN 规则: %s", e)
        tasks = []
        for f in raw_files:
            pid, fid = f.get("projectID"), f.get("fileID")
            info = meta.get(int(fid), {}) if fid is not None else {}
            filename = info.get("fileName")
            download_url = info.get("downloadUrl")
            dest_name = filename or f"mod-{pid}-{fid}.jar"
            dest = instance.path / "mods" / dest_name
            sha1 = None
            for h in info.get("hashes") or []:
                if h.get("algo") == 1 and h.get("value"):
                    sha1 = h.get("value")
                    break
            size = info.get("fileLength")
            urls = cf_mod_download_urls(pid, fid, filename=filename, download_url=download_url)
            tasks.append((urls, dest, sha1, size))
        if tasks:
            _emit(on_progress, f"开始下载整合包 Mod（{len(tasks)} 个）")
            dm.download_all(tasks, message="下载整合包 Mod")
            _emit(on_progress, "整合包 Mod 下载完成")

        # overrides
        overrides = mf.get("overrides")
        if overrides:
            src = pack_root / overrides
            if src.is_dir():
                _copy_tree_over(src, instance.path)

        pack_meta = {
            "name": mf.get("name", Path(pack_path).stem),
            "version": mf.get("version", "?"),
            "mc_version": mc_version,
            "loader": loader_id,
            "source": "curseforge",
            "instance": instance.name,
        }
        instance.set_meta("modpack", pack_meta)
        instance.set_meta("mc_version", loader_vid or mc_version)
        _emit(on_progress, f"整合包 {pack_meta['name']} 安装完成 -> 实例 {instance.name}")
        return pack_meta
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
        if downloaded:
            try:
                pack_path.unlink(missing_ok=True)
            except OSError:
                pass


def _copy_tree_over(src: Path, dest: Path):
    for item in src.rglob("*"):
        rel = item.relative_to(src)
        target = dest / rel
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif item.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)


def _copy_mc_tree(root: Path, dest: Path):
    """整个 .minecraft 目录拷进实例。versions/ 由 _copy_embedded_versions
    处理，logs / crash-reports 是运行垃圾不拷。"""
    skip = {"versions", "logs", "crash-reports"}
    for item in root.iterdir():
        if item.name in skip:
            continue
        target = dest / item.name
        try:
            if item.is_dir():
                shutil.copytree(item, target, dirs_exist_ok=True)
            elif item.is_file():
                shutil.copy2(item, target)
        except OSError:
            continue


def _install_plain_zip(dm: DownloadManager, tmpdir: Path, instance: Instance, pack_path,
                       on_progress=None, cancel=None, force=False, java=None):
    """没有 manifest.json 的 zip：按“直接压缩的 .minecraft 目录”整合包安装。

    版本与加载器从 zip 里 versions/<名>/<名>.json 推断；装好后其余目录
    （mods / config / saves / 资源包等）原样拷入实例。
    """
    root = _plain_pack_root(tmpdir)
    if root is None:
        top = ", ".join(sorted(p.name for p in tmpdir.iterdir())[:10])
        raise ModpackError(
            "整合包缺少 manifest.json，也不是 .minecraft 目录结构。\n"
            f"zip 顶层内容: {top or '(空)'}\n"
            "支持三种格式：CurseForge 导出的 zip（内含 manifest.json）、"
            "Modrinth 的 .mrpack、直接压缩的 .minecraft 目录（含 mods / versions 等）。"
        )
    rel = root.relative_to(tmpdir)
    where = f"（位于 {rel} 子目录）" if str(rel) != "." else ""
    _emit(on_progress, f"识别为 .minecraft 目录压缩包{where}，开始安装")
    if instance.path.is_dir():
        instance.ensure_standard_dirs()
    else:
        instance.create()
    _emit(on_progress, f"安装到实例 {instance.name} ({instance.path})")
    installer = Installer(instance, dm, on_progress=on_progress or dm.on_progress, cancel=cancel)

    ver = _plain_pack_version(root) or {}
    mc_version = ver.get("mc") or ""
    loader = ver.get("loader")
    loader_version = ver.get("loader_version") or ""

    embedded = _copy_embedded_versions(root, instance, plain=True)
    if embedded:
        _emit(on_progress, f"发现自带版本: {', '.join(embedded)}")

    loader_vid = None
    if not mc_version:
        _emit(on_progress, "包里没有可识别的版本信息，未安装游戏版本；请到「下载 → 原版游戏」安装后再启动")
    else:
        resolved = _resolve_pack_minecraft(dm, mc_version, on_progress) or mc_version
        _emit(on_progress, f"安装 Minecraft {resolved}")
        installer.install_version(resolved, force=force, java=java)
        if loader:
            _emit(on_progress, f"安装加载器 {loader} {loader_version} (Minecraft {resolved})")
            try:
                loader_vid = install_loader(installer, loader, loader_version, resolved, force=force)
            except InstallError as e:
                loader_vid = None
                _emit(on_progress, f"加载器安装失败（{e}），已仅装原版；mods 可能无法加载")
        else:
            _emit(on_progress, "未识别到 Forge/Fabric 加载器，按原版安装（mods 不会被加载）")

    _copy_mc_tree(root, instance.path)
    pack_meta = {
        "name": Path(pack_path).stem,
        "version": "?",
        "mc_version": mc_version or None,
        "loader": (f"{loader}-{loader_version}" if loader else "vanilla"),
        "source": "plain-zip",
        "instance": instance.name,
    }
    instance.set_meta("modpack", pack_meta)
    instance.set_meta("mc_version", loader_vid or mc_version or "")
    _emit(on_progress, f"整合包 {pack_meta['name']} 安装完成 -> 实例 {instance.name}")
    return pack_meta


# ================================================================ 加载器

def install_loader(installer: Installer, loader: str, version: str, mc_version: str,
                   force=False):
    """按名称安装加载器。loader: fabric-loader / quilt-loader / forge / neoforge"""
    loader = (loader or "").lower()
    if loader == "fabric-loader":
        try:
            return installer.install_fabric(mc_version, version, force=force)
        except InstallError as e:
            installer._note(f"Fabric Loader {version} 不可用: {e}，尝试最新稳定版")
            try:
                return installer.install_fabric(mc_version, None, force=force)
            except InstallError as e2:
                raise InstallError(f"{e}；回退最新版也失败: {e2}") from e
    elif loader == "quilt-loader":
        try:
            return installer.install_quilt(mc_version, version, force=force)
        except InstallError as e:
            installer._note(f"Quilt Loader {version} 不可用: {e}，尝试最新版")
            try:
                return installer.install_quilt(mc_version, None, force=force)
            except InstallError as e2:
                raise InstallError(f"{e}；回退最新版也失败: {e2}") from e
    elif loader == "forge":
        return installer.install_forge(mc_version, version, force=force)
    elif loader == "neoforge":
        try:
            return installer.install_neoforge(mc_version, version, force=force)
        except InstallError as e:
            if not version:
                raise
            installer._note(f"NeoForge {version} 安装失败: {e}，尝试该 MC 最新 NeoForge")
            try:
                return installer.install_neoforge(mc_version, None, force=force)
            except InstallError as e2:
                raise InstallError(f"{e}；回退最新版也失败: {e2}") from e
    raise ModpackError(f"不支持的加载器: {loader}")
