# -*- coding: utf-8 -*-
"""整合包支持：Modrinth（.mrpack，含在线搜索）、CurseForge（.zip）、
MultiMC / Prism Launcher 导出的实例包（mmc-pack.json）。"""
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


# ================================================================ 整合包更新（文件清单 + 版本检查）

PACK_FILES_NAME = "modpack.files.json"
# 更新时只自动清理这些目录下的旧包文件；config 等可能被用户改过，不动。
_MANAGED_PREFIXES = ("mods/", "resourcepacks/", "shaderpacks/", "datapacks/")


def pack_files_path(instance: Instance) -> Path:
    return Path(instance.path) / PACK_FILES_NAME


def read_pack_files(instance: Instance) -> list[str]:
    """读取整合包写入实例的文件清单（相对实例根的 posix 路径）。"""
    data = utils.read_json(pack_files_path(instance), None)
    if not isinstance(data, dict):
        return []
    files = data.get("files")
    if not isinstance(files, list):
        return []
    return [str(x) for x in files if x]


def write_pack_files(instance: Instance, paths):
    """记录整合包本次安装写入的文件（下载文件 + overrides）。"""
    uniq = sorted({str(p).replace("\\", "/").lstrip("/") for p in paths if p})
    utils.write_json(pack_files_path(instance), {"files": uniq})


def _tree_rel_paths(src: Path) -> list[str]:
    out = []
    for p in src.rglob("*"):
        if p.is_file():
            out.append(p.relative_to(src).as_posix())
    return out


def _merge_origin(pack_meta: dict, origin, keys):
    """把安装来源标识（slug / addon_id / file_id / version_id）并进 pack_meta。"""
    for k in keys:
        v = (origin or {}).get(k)
        if v not in (None, ""):
            pack_meta[k] = v
    return pack_meta


def cleanup_stale_pack_files(instance: Instance, old_files) -> list[str]:
    """删除旧整合包版本装入、且新版本清单里没有的文件。

    只清理 mods / resourcepacks / shaderpacks / datapacks（旧版模组残留会和
    新版模组一起加载导致崩溃）；存档、截图、config 与用户手动放入的文件
    不在旧清单里，不受影响。被用户禁用的旧包模组（.disabled）一并清掉。
    """
    new = set(read_pack_files(instance))
    root = Path(instance.path).resolve()
    removed = []
    for rel in old_files or []:
        posix = str(rel).replace("\\", "/").lstrip("/")
        if not posix or posix in new or not posix.startswith(_MANAGED_PREFIXES):
            continue
        target = root / posix
        try:
            rp = target.resolve()
        except OSError:
            continue
        # 防路径穿越：清单可能来自损坏/恶意文件
        if not str(rp).startswith(str(root) + os.sep):
            continue
        for cand, suffix in ((rp, ""), (rp.with_name(rp.name + ".disabled"), ".disabled")):
            if not cand.is_file():
                continue
            try:
                cand.unlink()
                removed.append(posix + suffix)
            except OSError:
                pass
    return removed


_NO_ORIGIN_HINT = (
    "该整合包安装时未记录来源信息（本地文件安装，或由旧版本 PyMCL 安装）。"
    "从下载页重新安装一次同名整合包后即可在线检查更新。"
)


def check_modpack_update(dm: DownloadManager, instance: Instance, api_key=None) -> dict:
    """检查实例整合包是否有新版本（Modrinth / CurseForge）。

    返回 {source, name, slug, current, latest, current_id, latest_id,
    update, url, mc_versions}。没有安装记录 / 来源不支持时抛 ModpackError。
    """
    meta = (instance.meta() or {}).get("modpack")
    if not isinstance(meta, dict) or not meta.get("name"):
        raise ModpackError("该实例没有整合包安装记录，无法检查更新")
    source = str(meta.get("source") or "").lower()
    name = str(meta.get("name") or "?")
    current = str(meta.get("version") or "?")

    if source == "modrinth":
        slug = meta.get("slug")
        if not slug:
            raise ModpackError(_NO_ORIGIN_HINT)
        versions = modrinth_versions(dm, slug)
        f, v = _pick_mrpack_file(versions)
        if not f or not v:
            raise ModpackError(f"整合包 {slug} 在 Modrinth 上没有可下载的 .mrpack 版本")
        latest_id = str(v.get("id") or "")
        latest = str(v.get("version_number") or v.get("name") or latest_id)
        cur_id = str(meta.get("version_id") or "")
        if cur_id and latest_id:
            update = latest_id != cur_id
        else:
            update = latest != current
        return {
            "source": "modrinth", "name": name, "slug": slug,
            "current": current, "latest": latest,
            "current_id": cur_id or None, "latest_id": latest_id or None,
            "update": bool(update), "url": f.get("url"),
            "mc_versions": v.get("game_versions") or [],
        }

    if source == "curseforge":
        addon_id = meta.get("addon_id")
        cur_id = meta.get("file_id")
        if not addon_id or not cur_id:
            raise ModpackError(_NO_ORIGIN_HINT)
        info = resolve_cf_modpack_file(dm, addon_id, api_key=api_key,
                                       cf_slug=meta.get("slug"))
        latest_id = info.get("file_id")
        latest = str(info.get("fileName") or latest_id or "?")
        return {
            "source": "curseforge", "name": info.get("name") or name,
            "slug": info.get("slug") or meta.get("slug"),
            "addon_id": info.get("addon_id") or addon_id,
            "current": current, "latest": latest,
            "current_id": cur_id, "latest_id": latest_id,
            "update": str(latest_id) != str(cur_id), "url": None,
            "mc_versions": [],
        }

    raise ModpackError(
        f"整合包来源「{source or '未知'}」不支持在线检查更新"
        "（本地 zip / MultiMC 实例包没有更新源）"
    )


def update_modpack(dm: DownloadManager, instance: Instance, on_progress=None,
                   cancel=None, api_key=None, info=None) -> dict:
    """把实例整合包升级到最新版本。

    重装新版本的下载文件与 overrides，然后删除旧版本装入、新版本不再
    包含的 mods / resourcepacks / shaderpacks / datapacks 文件。存档、
    截图、options.txt 与用户手动加的模组不在整合包清单里，不受影响。
    """
    info = info or check_modpack_update(dm, instance, api_key=api_key)
    if not info.get("update"):
        _emit(on_progress, f"{info.get('name')} 已是最新版本 {info.get('current')}")
        return {"updated": False, **info}
    old_files = read_pack_files(instance)
    if not old_files:
        _emit(on_progress, "没有旧版本文件清单（由旧版 PyMCL 安装），更新后不会自动清理旧文件")
    _emit(on_progress,
          f"更新整合包 {info.get('name')}: {info.get('current')} -> {info.get('latest')}")
    if info.get("source") == "modrinth":
        if not info.get("url"):
            raise ModpackError("最新版本没有可下载的 .mrpack 文件")
        meta = install_mrpack(
            dm, info["url"], instance, on_progress=on_progress, cancel=cancel,
            origin={"slug": info.get("slug"), "version_id": info.get("latest_id")})
    else:
        meta = install_cf_modpack(
            dm, info.get("addon_id"), instance, api_key=api_key,
            on_progress=on_progress, cancel=cancel,
            cf_slug=info.get("slug"), file_id=info.get("latest_id"))
    removed = cleanup_stale_pack_files(instance, old_files)
    if removed:
        _emit(on_progress, f"已清理旧版本残留文件 {len(removed)} 个")
    return {
        "updated": True, "name": info.get("name"),
        "from": info.get("current"),
        "to": str((meta or {}).get("version") or info.get("latest") or "?"),
        "removed": removed, "meta": meta,
    }


# ================================================================ CurseForge 搜索（BMCLAPI 镜像）

def search_cf_modpacks(dm: DownloadManager, query, limit=25, api_key=None,
                       game_version=None, categories=None, sort="", offset=0):
    """搜索 CurseForge 整合包（走 BMCLAPI 国内镜像，无需 API key）。"""
    from .mods import search_curseforge, CF_CLASS_MODPACK
    from .catalog_files import cf_category_tokens
    tokens = []
    for c in categories or []:
        tokens.extend(cf_category_tokens(c) or [str(c).lower()])
    hits = search_curseforge(dm, query=query, limit=limit, api_key=api_key,
                             class_id=CF_CLASS_MODPACK,
                             game_version=game_version, categories=tokens or None,
                             sort=sort, offset=offset)
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
        return install_cf_zip(dm, tmp, instance, on_progress=on_progress, cancel=cancel,
                              origin={"addon_id": addon_id, "file_id": file_id,
                                      "slug": info.get("slug") or cf_slug})
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
                    game_version=None, categories=None, sort="", offset=0):
    """搜索 Modrinth 整合包（官方优先，MCIM 镜像兜底）。

    中文关键词先经 mcmod.cn 中文名数据库翻成英文名再搜（HMCL/PCL2 同款）。
    sort/offset：下载页排序与「加载更多」分页。
    """
    from .mods import mirror_modrinth_url, _mr_facets, mr_sort_index
    from .catalog_files import category_facets
    from . import mod_translate

    rec = mod_translate.best_cn_match(query, "modpack", dm=dm)
    if rec:
        query = rec.get("subname") or rec.get("curseforge") or rec.get("name") or query
    facets = _mr_facets("modpack", game_version, categories)
    params = {
        "query": query,
        "facets": facets,
        "limit": limit,
        # 有词按相关度、无词浏览按下载量（真实榜单），显式排序优先
        "index": mr_sort_index(sort, query),
    }
    if offset:
        params["offset"] = int(offset)
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
    return mod_translate.annotate(result, "modpack")


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
            "请到「模组」页安装，或换一个真正的整合包（机械动力可搜黄铜协奏曲 CBC）。"
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
                                  force=force, java=java,
                                  origin={"slug": proj.get("slug") or slug,
                                          "version_id": v.get("id")})
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
                   on_progress=None, cancel=None, force=False, java=None,
                   origin=None):
    """安装 .mrpack 整合包到指定实例。

    origin: {"slug", "version_id"}，来自在线安装入口；记进 pack_meta
    供后续检查更新用。
    """
    from . import diskspace
    diskspace.ensure_free(instance.path, what="安装整合包")
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
        pack_paths = []
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
            pack_paths.append(str(rel).replace("\\", "/").lstrip("/"))
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
                pack_paths.extend(_tree_rel_paths(src))
                break

        pack_meta = {
            "name": idx.get("name", Path(pack_path).stem),
            "version": idx.get("versionId"),
            "mc_version": mc_version,
            "loader": f"{loader}-{deps.get(loader)}" if loader else None,
            "source": "modrinth",
            "instance": instance.name,
        }
        _merge_origin(pack_meta, origin, ("slug", "version_id"))
        instance.set_meta("modpack", pack_meta)
        instance.set_meta("mc_version", loader_vid or mc_version)
        write_pack_files(instance, pack_paths)
        _emit(on_progress, f"整合包 {pack_meta['name']} 安装完成 -> 实例 {instance.name}")
        return pack_meta
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
        if downloaded:
            try:
                pack_path.unlink(missing_ok=True)
            except OSError:
                pass


# ================================================================ MultiMC / Prism

_MMC_LOADER_UIDS = {
    "net.minecraftforge": "forge",
    "net.neoforged": "neoforge",
    "net.fabricmc.fabric-loader": "fabric-loader",
    "org.quiltmc.quilt-loader": "quilt-loader",
    "com.mumfrey.liteloader": "liteloader",
}
# 纯依赖组件：由对应加载器/游戏本体安装流程自带，无需单独处理。
_MMC_IGNORED_UIDS = frozenset((
    "org.lwjgl", "org.lwjgl3", "net.fabricmc.intermediary", "org.quiltmc.hashed",
))


def _mmc_root(tmpdir: Path):
    """找 MultiMC / Prism 导出包的根（mmc-pack.json 所在目录，可能套一层文件夹）。"""
    if (tmpdir / "mmc-pack.json").is_file():
        return tmpdir
    return _nested_marker_root(tmpdir, "mmc-pack.json")


def parse_mmc_components(pack: dict) -> dict:
    """解析 mmc-pack.json 的 components：MC 版本、加载器、无法自动处理的组件。"""
    mc = ""
    loader = None
    loader_version = ""
    skipped = []
    for comp in (pack or {}).get("components") or []:
        if not isinstance(comp, dict):
            continue
        uid = str(comp.get("uid") or "")
        ver = str(comp.get("version") or comp.get("cachedVersion") or "")
        if uid == "net.minecraft":
            mc = ver
            continue
        if uid in _MMC_LOADER_UIDS and loader is None:
            loader = _MMC_LOADER_UIDS[uid]
            loader_version = ver
            continue
        if comp.get("dependencyOnly") or uid in _MMC_IGNORED_UIDS:
            continue
        skipped.append(f"{comp.get('cachedName') or uid} {ver}".strip())
    return {"mc": mc, "loader": loader, "loader_version": loader_version,
            "skipped": skipped}


def _mmc_instance_name(root: Path) -> str:
    """instance.cfg 里的实例名（Prism 新版带 [General] 节，老版 MultiMC 没有）。"""
    cfg = root / "instance.cfg"
    if not cfg.is_file():
        return ""
    try:
        text = cfg.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("name="):
            return line[5:].strip()
    return ""


def _install_mmc_pack(dm: DownloadManager, root: Path, instance: Instance, pack_path,
                      on_progress=None, cancel=None, force=False, java=None):
    """安装 MultiMC / Prism Launcher 导出的实例包（mmc-pack.json + minecraft/）。"""
    pack = utils.read_json(root / "mmc-pack.json", None) or {}
    parsed = parse_mmc_components(pack)
    name = _mmc_instance_name(root) or Path(pack_path).stem
    _emit(on_progress, f"识别为 MultiMC/Prism 实例包「{name}」")
    mc_version = parsed["mc"]
    if not mc_version:
        raise ModpackError(
            "mmc-pack.json 里没有 net.minecraft 组件，无法确定 Minecraft 版本")

    if instance.path.is_dir():
        instance.ensure_standard_dirs()
    else:
        instance.create()
    _emit(on_progress, f"安装到实例 {instance.name} ({instance.path})")
    installer = Installer(instance, dm, on_progress=on_progress or dm.on_progress, cancel=cancel)

    resolved = _resolve_pack_minecraft(dm, mc_version, on_progress) or mc_version
    _emit(on_progress, f"安装 Minecraft {resolved}")
    installer.install_version(resolved, force=force, java=java)

    loader = parsed["loader"]
    loader_version = parsed["loader_version"]
    loader_vid = None
    if loader == "liteloader":
        _emit(on_progress, f"安装 LiteLoader (Minecraft {resolved})")
        try:
            loader_vid = installer.install_liteloader(resolved, force=force)
        except InstallError as e:
            _emit(on_progress, f"LiteLoader 安装失败（{e}），已仅装原版；mods 可能无法加载")
    elif loader:
        _emit(on_progress, f"安装加载器 {loader} {loader_version} (Minecraft {resolved})")
        try:
            loader_vid = install_loader(installer, loader, loader_version, resolved, force=force)
        except InstallError as e:
            loader_vid = None
            _emit(on_progress, f"加载器安装失败（{e}），已仅装原版；mods 可能无法加载")
    else:
        _emit(on_progress, "实例包没有 Forge/Fabric 等加载器组件，按原版安装")
    for extra_comp in parsed["skipped"]:
        _emit(on_progress, f"组件 {extra_comp} 暂不支持自动安装，已跳过")

    game_dir = next((root / d for d in (".minecraft", "minecraft") if (root / d).is_dir()), None)
    if game_dir is not None:
        _emit(on_progress, f"拷贝游戏目录 {game_dir.name}/（mods、config、存档等）")
        _copy_mc_tree(game_dir, instance.path)
    else:
        _emit(on_progress, "实例包里没有 minecraft/ 游戏目录，仅安装了游戏本体")

    pack_meta = {
        "name": name,
        "version": "?",
        "mc_version": resolved,
        "loader": (f"{loader}-{loader_version}" if loader else "vanilla"),
        "source": "multimc",
        "instance": instance.name,
    }
    instance.set_meta("modpack", pack_meta)
    instance.set_meta("mc_version", loader_vid or resolved)
    _emit(on_progress, f"整合包 {name} 安装完成 -> 实例 {instance.name}")
    return pack_meta


# ================================================================ CurseForge

def download_pack_mods_tolerant(dm: DownloadManager, tasks, raw_files, meta,
                                on_progress=None) -> list[dict]:
    """下载整合包 Mod，容忍个别失败。

    个别 Mod 常因作者禁止第三方分发（API downloadUrl 为空、CDN 直链 403）
    下不下来。PCL2 / HMCL 的做法是装完其余部分、列出需手动下载的清单，
    而不是让整包安装报废。返回需手动下载的条目列表
    [{name, filename, project_id, file_id, url, dest_dir}]。

    只有「一个都没下载成」（且不止几个文件）或失败但找不到缺失文件时才
    原样抛错——那说明是网络整体不可用，不该转成手动清单。用户取消照常抛。
    """
    download_err = None
    try:
        dm.download_all(tasks, message="下载整合包 Mod")
    except DownloadError as e:
        if "用户取消" in str(e):
            raise
        download_err = e
    manual_mods = []
    for raw, task in zip(raw_files, tasks):
        dest = Path(task[1])
        if dest.is_file():
            continue
        pid, fid = raw.get("projectID"), raw.get("fileID")
        info = meta.get(int(fid), {}) if fid is not None else {}
        manual_mods.append({
            "name": info.get("displayName") or info.get("fileName") or f"项目 {pid}",
            "filename": dest.name,
            "project_id": pid,
            "file_id": fid,
            "url": f"https://www.curseforge.com/projects/{pid}",
            "dest_dir": str(dest.parent),
        })
    if download_err:
        if not manual_mods:
            raise download_err
        if len(manual_mods) == len(tasks) and len(tasks) > 3:
            raise download_err
    if manual_mods:
        _emit(on_progress,
              f"{len(manual_mods)} 个 Mod 未能自动下载（常见原因：作者禁止第三方分发）：")
        for m in manual_mods:
            _emit(on_progress, f"  {m['name']} → 浏览器打开 {m['url']} 下载后放入 {m['dest_dir']}")
        _emit(on_progress, "其余内容已安装。手动补齐上述文件后即可正常启动。")
    else:
        _emit(on_progress, "整合包 Mod 下载完成")
    return manual_mods


def install_cf_zip(dm: DownloadManager, source, instance: Instance,
                   on_progress=None, cancel=None, force=False, java=None,
                   origin=None):
    """安装 CurseForge 整合包 zip（本地文件或直链）。

    origin: {"addon_id", "file_id", "slug"}，来自在线安装入口；记进
    pack_meta 供后续检查更新用。
    """
    from . import diskspace
    diskspace.ensure_free(instance.path, what="安装整合包")
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
            mmc_root = _mmc_root(tmpdir)
            if mmc_root is not None:
                # MultiMC / Prism Launcher 导出的实例包
                return _install_mmc_pack(dm, mmc_root, instance, pack_path,
                                         on_progress=on_progress, cancel=cancel,
                                         force=force, java=java)
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
        pack_paths = []
        for f in raw_files:
            pid, fid = f.get("projectID"), f.get("fileID")
            info = meta.get(int(fid), {}) if fid is not None else {}
            filename = info.get("fileName")
            download_url = info.get("downloadUrl")
            dest_name = filename or f"mod-{pid}-{fid}.jar"
            dest = instance.path / "mods" / dest_name
            pack_paths.append(f"mods/{dest_name}")
            sha1 = None
            for h in info.get("hashes") or []:
                if h.get("algo") == 1 and h.get("value"):
                    sha1 = h.get("value")
                    break
            size = info.get("fileLength")
            urls = cf_mod_download_urls(pid, fid, filename=filename, download_url=download_url)
            tasks.append((urls, dest, sha1, size))
        manual_mods = []
        if tasks:
            _emit(on_progress, f"开始下载整合包 Mod（{len(tasks)} 个）")
            manual_mods = download_pack_mods_tolerant(
                dm, tasks, raw_files, meta, on_progress=on_progress)

        # overrides
        overrides = mf.get("overrides")
        if overrides:
            src = pack_root / overrides
            if src.is_dir():
                _copy_tree_over(src, instance.path)
                pack_paths.extend(_tree_rel_paths(src))

        pack_meta = {
            "name": mf.get("name", Path(pack_path).stem),
            "version": mf.get("version", "?"),
            "mc_version": mc_version,
            "loader": loader_id,
            "source": "curseforge",
            "instance": instance.name,
        }
        if manual_mods:
            pack_meta["manual_mods"] = manual_mods
        _merge_origin(pack_meta, origin, ("addon_id", "file_id", "slug"))
        instance.set_meta("modpack", pack_meta)
        instance.set_meta("mc_version", loader_vid or mc_version)
        write_pack_files(instance, pack_paths)
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
            "支持四种格式：CurseForge 导出的 zip（内含 manifest.json）、"
            "Modrinth 的 .mrpack、MultiMC / Prism Launcher 导出的实例包"
            "（内含 mmc-pack.json）、直接压缩的 .minecraft 目录（含 mods / versions 等）。"
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
        _emit(on_progress, "包里没有可识别的版本信息，未安装游戏版本，稍后请在版本页选择")
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
