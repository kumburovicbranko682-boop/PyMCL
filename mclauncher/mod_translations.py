# -*- coding: utf-8 -*-
"""中文模组/整合包名数据库（对标 PCL2 / HMCL 的 mcmod.cn 数据集）。

数据源是 HMCL 随包分发的两份数据（mcmod.cn 数据）:
- assets/mod_data.txt      模组，约 2.8 万行
- assets/modpack_data.txt  整合包，约 1400 行
每行 6 段:  curseforge_slug;mcmod_id;modids;中文名;英文名;缩写
运行时从 GitHub 下载（fetch_text 自动展开国内代理镜像）并缓存到
cache/ 下，7 天刷新一次；刷新失败用旧缓存继续，完全拿不到就静默
降级——内置别名目录（catalog.py）仍然可用。

记录是 6 元组 (slug, mcmod_id, name_cn, name_en, abbr, modids)，用元
组存省内存；modids（逗号分隔的 modid 列表）用于本地已装模组的译名
匹配（HMCL 模组列表同款：modid 与 subname 双重匹配）。
"""
from __future__ import annotations

import re
import threading
import time
from pathlib import Path

from . import utils

_HMCL_RAW = ("https://raw.githubusercontent.com/HMCL-dev/HMCL/main/"
             "HMCL/src/main/resources/assets/")
DATA_URL = _HMCL_RAW + "mod_data.txt"
PACK_DATA_URL = _HMCL_RAW + "modpack_data.txt"
CACHE_TTL = 7 * 24 * 3600
MCMOD_CLASS = "https://www.mcmod.cn/class/{id}.html"
MCMOD_MODPACK = "https://www.mcmod.cn/modpack/{id}.html"

_CJK = re.compile(r"[\u3400-\u9fff]")

_lock = threading.Lock()
_records: list[tuple] | None = None   # 模组数据集，None=未加载
_by_slug: dict[str, tuple] = {}
_by_title: dict[str, tuple] = {}
_by_modid: dict[str, tuple] = {}
_load_failed = False

_pack_records: list[tuple] | None = None   # 整合包数据集
_pack_by_slug: dict[str, tuple] = {}
_pack_by_title: dict[str, tuple] = {}
_pack_load_failed = False


def has_cjk(text) -> bool:
    return bool(_CJK.search(str(text or "")))


def cache_file() -> Path:
    return Path(utils.ROOT) / "cache" / "mod_data.txt"


def pack_cache_file() -> Path:
    return Path(utils.ROOT) / "cache" / "modpack_data.txt"


def parse(text: str) -> list[tuple]:
    """解析数据文件为 (slug, mcmod_id, name_cn, name_en, abbr, modids) 列表。"""
    out = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(";")
        if len(parts) < 6:
            parts += [""] * (6 - len(parts))
        slug = parts[0].strip()
        mcmod_id = parts[1].strip()
        modids = tuple(m.strip() for m in parts[2].split(",") if m.strip())
        name_cn = parts[3].strip()
        name_en = parts[4].strip()
        abbr = ";".join(parts[5:]).strip()
        if not (name_cn or name_en):
            continue
        out.append((slug, mcmod_id, name_cn, name_en, abbr, modids))
    return out


def _index(records: list[tuple]) -> tuple[dict, dict, dict]:
    by_slug, by_title, by_modid = {}, {}, {}
    for rec in records:
        slug, _mid, _cn, name_en, _abbr, modids = rec
        if slug and slug not in by_slug:
            by_slug[slug] = rec
        key = name_en.lower()
        if key and key not in by_title:
            by_title[key] = rec
        for mid in modids:
            mk = mid.lower()
            if mk and mk not in by_modid:
                by_modid[mk] = rec
    return by_slug, by_title, by_modid


def _read_cache(path: Path, min_bytes: int) -> str:
    try:
        if path.is_file() and path.stat().st_size > min_bytes:
            return path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        utils.log.warning("读取译名缓存失败 %s: %s", path.name, e)
    return ""


def _cache_fresh(path: Path) -> bool:
    try:
        return path.is_file() and (time.time() - path.stat().st_mtime) < CACHE_TTL
    except OSError:
        return False


def _fetch(dm, url: str, min_bytes: int) -> str:
    """拉取数据文件；镜像轮询由 DownloadManager 处理。"""
    if dm is None:
        from .downloader import DownloadManager
        dm = DownloadManager(threads=2)
    text = dm.fetch_text(url, timeout=(5, 60))
    # 半截响应（代理截断）不要落缓存
    if len(text) < min_bytes or "mcmod" not in text[:400]:
        raise ValueError(f"译名数据不完整（{len(text)} 字节）")
    return text


def _load_text(dm, url: str, path: Path, min_bytes: int, force: bool) -> str:
    """优先用 7 天内的磁盘缓存；过期或没有则下载，失败回退旧缓存。"""
    text = ""
    if not force and _cache_fresh(path):
        text = _read_cache(path, min_bytes)
    if not text:
        try:
            text = _fetch(dm, url, min_bytes)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        except Exception as e:
            utils.log.warning("下载译名数据失败 %s: %s", path.name, e)
            text = _read_cache(path, min_bytes)   # 过期缓存好过没有
    return text


# ---------------------------------------------------------------- 模组数据集

def load(dm=None, force=False) -> bool:
    """确保模组数据集已加载；返回是否可用。加载失败本进程内不再重试。"""
    global _load_failed, _records, _by_slug, _by_title, _by_modid
    with _lock:
        if _records is not None and not force:
            return True
        if _load_failed and not force:
            return False
        text = _load_text(dm, DATA_URL, cache_file(), 100_000, force)
        records = parse(text) if text else []
        if not records:
            _load_failed = True
            return False
        _by_slug, _by_title, _by_modid = _index(records)
        _records = records
        utils.log.info("模组译名数据已加载: %d 条", len(records))
        return True


def load_async():
    """后台预热（英文搜索的结果注解用；不阻塞当前调用）。"""
    if _records is not None or _load_failed:
        return
    threading.Thread(target=load, name="mod-translations", daemon=True).start()


def loaded() -> bool:
    return _records is not None


def search_chinese(query: str, limit: int = 6) -> list[dict]:
    """按中文名 / 缩写搜索模组，返回 [{slug, mcmod_id, name_cn, name_en, abbr}]。"""
    return _search(_records, query, limit)


def mcmod_url(mcmod_id) -> str:
    mid = str(mcmod_id or "").strip()
    return MCMOD_CLASS.format(id=mid) if mid.isdigit() else ""


def lookup(slug: str = "", title: str = "") -> dict | None:
    """按 CurseForge/Modrinth slug 或英文名查模组记录（未加载返回 None）。"""
    return _lookup(_records, _by_slug, _by_title, slug, title)


def annotate_hits(hits) -> None:
    """给模组搜索结果就地补 name_cn / mcmod_url（对标 HMCL 下载列表译名）。

    只在数据已加载时生效，否则触发后台预热等下次搜索；
    译名里没有中文的条目（如 Minecraft Forge）不注，避免噪音。
    """
    if _records is None:
        load_async()
        return
    _annotate(hits, lookup, mcmod_url)


def lookup_local(modid: str = "", name: str = "") -> dict | None:
    """按本地 jar 元数据查模组记录（HMCL 同款：modid 与英文名双重匹配）。"""
    if _records is None:
        return None
    rec = _by_modid.get(str(modid or "").strip().lower())
    if rec is None:
        rec = _by_title.get(str(name or "").strip().lower())
    return _rec_dict(rec) if rec is not None else None


def annotate_local_mods(rows) -> None:
    """给已装模组条目就地补 name_cn / mcmod_url（对标 HMCL 模组列表译名）。

    rows 是 list_mod_entries_at 的输出（含 id / mod_name）。
    未加载时触发后台预热，下次刷新列表就有译名。
    """
    if _records is None:
        load_async()
        return
    for r in rows or []:
        if not isinstance(r, dict) or r.get("name_cn"):
            continue
        rec = lookup_local(modid=r.get("id") or "",
                           name=r.get("mod_name") or r.get("name") or "")
        if not rec:
            continue
        if has_cjk(rec["name_cn"]):
            r["name_cn"] = rec["name_cn"]
        url = mcmod_url(rec["mcmod_id"])
        if url:
            r["mcmod_url"] = url


# ---------------------------------------------------------------- 整合包数据集

def load_packs(dm=None, force=False) -> bool:
    """确保整合包数据集已加载；返回是否可用。"""
    global _pack_load_failed, _pack_records, _pack_by_slug, _pack_by_title
    with _lock:
        if _pack_records is not None and not force:
            return True
        if _pack_load_failed and not force:
            return False
        # 整合包数据约 66KB，完整性下限放低
        text = _load_text(dm, PACK_DATA_URL, pack_cache_file(), 20_000, force)
        records = parse(text) if text else []
        if not records:
            _pack_load_failed = True
            return False
        _pack_by_slug, _pack_by_title, _ = _index(records)
        _pack_records = records
        utils.log.info("整合包译名数据已加载: %d 条", len(records))
        return True


def load_packs_async():
    if _pack_records is not None or _pack_load_failed:
        return
    threading.Thread(target=load_packs, name="pack-translations", daemon=True).start()


def packs_loaded() -> bool:
    return _pack_records is not None


def search_packs_chinese(query: str, limit: int = 6) -> list[dict]:
    """按中文名 / 缩写搜索整合包。"""
    return _search(_pack_records, query, limit)


def mcmod_pack_url(mcmod_id) -> str:
    mid = str(mcmod_id or "").strip()
    return MCMOD_MODPACK.format(id=mid) if mid.isdigit() else ""


def lookup_pack(slug: str = "", title: str = "") -> dict | None:
    return _lookup(_pack_records, _pack_by_slug, _pack_by_title, slug, title)


def annotate_pack_hits(hits) -> None:
    """给整合包搜索结果就地补 name_cn / mcmod_url。"""
    if _pack_records is None:
        load_packs_async()
        return
    _annotate(hits, lookup_pack, mcmod_pack_url)


# ---------------------------------------------------------------- 共享实现

def _search(records, query: str, limit: int) -> list[dict]:
    """排序：精确命中 > 前缀 > 包含 > 缩写包含；同分短名优先
    （「机械动力」排在「机械动力：创想附加」前面）。
    """
    q = str(query or "").strip()
    if not q or records is None:
        return []
    ql = q.lower()
    scored = []
    for rec in records:
        name_cn, abbr = rec[2], rec[4]
        al = abbr.lower()
        if ql == name_cn.lower() or (al and ql == al):
            score = 0
        elif name_cn.lower().startswith(ql):
            score = 1
        elif ql in name_cn.lower():
            score = 2
        elif al and ql in al:
            score = 3
        else:
            continue
        scored.append((score, len(name_cn), rec))
    scored.sort(key=lambda t: (t[0], t[1]))
    return [_rec_dict(rec) for _s, _l, rec in scored[: max(1, int(limit))]]


def _lookup(records, by_slug, by_title, slug: str, title: str) -> dict | None:
    if records is None:
        return None
    rec = by_slug.get(str(slug or "").strip())
    if rec is None:
        rec = by_title.get(str(title or "").strip().lower())
    return _rec_dict(rec) if rec is not None else None


def _annotate(hits, lookup_fn, url_fn) -> None:
    for h in hits or []:
        if not isinstance(h, dict) or h.get("name_cn"):
            continue
        rec = lookup_fn(slug=h.get("slug") or "",
                        title=h.get("title") or h.get("name") or "")
        if not rec:
            continue
        if has_cjk(rec["name_cn"]):
            h["name_cn"] = rec["name_cn"]
        url = url_fn(rec["mcmod_id"])
        if url:
            h["mcmod_url"] = url


def _rec_dict(rec: tuple) -> dict:
    slug, mcmod_id, name_cn, name_en, abbr, modids = rec
    return {
        "slug": slug,
        "mcmod_id": mcmod_id,
        "name_cn": name_cn,
        "name_en": name_en,
        "abbr": abbr,
        "modids": list(modids),
    }


def _build_index(records: list[tuple]):
    """测试辅助：直接建模组索引，绕过下载。"""
    global _records, _by_slug, _by_title, _by_modid
    _by_slug, _by_title, _by_modid = _index(records)
    _records = records


def _build_pack_index(records: list[tuple]):
    """测试辅助：直接建整合包索引。"""
    global _pack_records, _pack_by_slug, _pack_by_title
    _pack_by_slug, _pack_by_title, _ = _index(records)
    _pack_records = records


def _reset_for_tests():
    global _records, _by_slug, _by_title, _by_modid, _load_failed
    global _pack_records, _pack_by_slug, _pack_by_title, _pack_load_failed
    with _lock:
        _records = None
        _by_slug = {}
        _by_title = {}
        _by_modid = {}
        _load_failed = False
        _pack_records = None
        _pack_by_slug = {}
        _pack_by_title = {}
        _pack_load_failed = False
