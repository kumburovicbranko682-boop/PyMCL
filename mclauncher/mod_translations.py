# -*- coding: utf-8 -*-
"""中文模组名数据库（对标 PCL2 / HMCL 的 mcmod.cn 数据集）。

数据源是 HMCL 随包分发的 assets/mod_data.txt（mcmod.cn 数据，2.8 万行），
每行 6 段:  curseforge_slug;mcmod_id;modids;中文名;英文名;缩写
运行时从 GitHub 下载（fetch_text 自动展开国内代理镜像）并缓存到
cache/mod_data.txt，7 天刷新一次；刷新失败用旧缓存继续，完全拿不到
就静默降级——内置别名目录（catalog.py）仍然可用。

记录是 5 元组 (slug, mcmod_id, name_cn, name_en, abbr)，2.8 万条
用元组存省内存；modids 字段暂不需要（本地模组译名匹配是后续切片）。
"""
from __future__ import annotations

import re
import threading
import time
from pathlib import Path

from . import utils

DATA_URL = ("https://raw.githubusercontent.com/HMCL-dev/HMCL/main/"
            "HMCL/src/main/resources/assets/mod_data.txt")
CACHE_TTL = 7 * 24 * 3600
MCMOD_CLASS = "https://www.mcmod.cn/class/{id}.html"

_CJK = re.compile(r"[\u3400-\u9fff]")

_lock = threading.Lock()
_records: list[tuple] | None = None   # None=未加载
_by_slug: dict[str, tuple] = {}
_by_title: dict[str, tuple] = {}
_load_failed = False


def has_cjk(text) -> bool:
    return bool(_CJK.search(str(text or "")))


def cache_file() -> Path:
    return Path(utils.ROOT) / "cache" / "mod_data.txt"


def parse(text: str) -> list[tuple]:
    """解析 mod_data.txt 为 (slug, mcmod_id, name_cn, name_en, abbr) 列表。"""
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
        name_cn = parts[3].strip()
        name_en = parts[4].strip()
        abbr = ";".join(parts[5:]).strip()
        if not (name_cn or name_en):
            continue
        out.append((slug, mcmod_id, name_cn, name_en, abbr))
    return out


def _build_index(records: list[tuple]):
    global _records, _by_slug, _by_title
    by_slug, by_title = {}, {}
    for rec in records:
        slug, _mid, _cn, name_en, _abbr = rec
        if slug and slug not in by_slug:
            by_slug[slug] = rec
        key = name_en.lower()
        if key and key not in by_title:
            by_title[key] = rec
    _records = records
    _by_slug = by_slug
    _by_title = by_title


def _read_cache() -> str:
    f = cache_file()
    try:
        if f.is_file() and f.stat().st_size > 100_000:
            return f.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        utils.log.warning("读取模组译名缓存失败: %s", e)
    return ""


def _cache_fresh() -> bool:
    f = cache_file()
    try:
        return f.is_file() and (time.time() - f.stat().st_mtime) < CACHE_TTL
    except OSError:
        return False


def _fetch(dm) -> str:
    """拉取数据文件；镜像轮询由 DownloadManager 处理。"""
    if dm is None:
        from .downloader import DownloadManager
        dm = DownloadManager(threads=2)
    text = dm.fetch_text(DATA_URL, timeout=(5, 60))
    # 数据应有 2 万行以上；半截响应（代理截断）不要落缓存
    if len(text) < 100_000 or "mcmod" not in text[:400]:
        raise ValueError(f"模组译名数据不完整（{len(text)} 字节）")
    return text


def load(dm=None, force=False) -> bool:
    """确保数据集已加载；返回是否可用。

    优先用 7 天内的磁盘缓存；过期或没有则下载（约 1.7MB），
    下载失败回退旧缓存；都没有则本进程内不再重试（_load_failed）。
    """
    global _load_failed
    with _lock:
        if _records is not None and not force:
            return True
        if _load_failed and not force:
            return False
        text = ""
        if not force and _cache_fresh():
            text = _read_cache()
        if not text:
            try:
                text = _fetch(dm)
                f = cache_file()
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text(text, encoding="utf-8")
            except Exception as e:
                utils.log.warning("下载模组译名数据失败: %s", e)
                text = _read_cache()   # 过期缓存好过没有
        if not text:
            _load_failed = True
            return False
        records = parse(text)
        if not records:
            _load_failed = True
            return False
        _build_index(records)
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
    """按中文名 / 缩写搜索，返回 [{slug, mcmod_id, name_cn, name_en, abbr}]。

    排序：精确命中 > 前缀 > 包含 > 缩写包含；同分短名优先
    （「机械动力」排在「机械动力：创想附加」前面）。
    """
    q = str(query or "").strip()
    if not q or _records is None:
        return []
    ql = q.lower()
    scored = []
    for rec in _records:
        _slug, _mid, name_cn, _en, abbr = rec
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
    out = []
    for _s, _l, rec in scored[: max(1, int(limit))]:
        out.append(_rec_dict(rec))
    return out


def _rec_dict(rec: tuple) -> dict:
    slug, mcmod_id, name_cn, name_en, abbr = rec
    return {
        "slug": slug,
        "mcmod_id": mcmod_id,
        "name_cn": name_cn,
        "name_en": name_en,
        "abbr": abbr,
    }


def mcmod_url(mcmod_id) -> str:
    mid = str(mcmod_id or "").strip()
    return MCMOD_CLASS.format(id=mid) if mid.isdigit() else ""


def lookup(slug: str = "", title: str = "") -> dict | None:
    """按 CurseForge/Modrinth slug 或英文名查记录（数据未加载返回 None）。"""
    if _records is None:
        return None
    rec = _by_slug.get(str(slug or "").strip())
    if rec is None:
        rec = _by_title.get(str(title or "").strip().lower())
    return _rec_dict(rec) if rec is not None else None


def annotate_hits(hits) -> None:
    """给搜索结果就地补 name_cn / mcmod_url（对标 HMCL 下载列表中文译名）。

    只在数据已加载时生效，否则触发后台预热等下次搜索；
    译名里没有中文的条目（如 Minecraft Forge）不注，避免噪音。
    """
    if _records is None:
        load_async()
        return
    for h in hits or []:
        if not isinstance(h, dict) or h.get("name_cn"):
            continue
        rec = lookup(slug=h.get("slug") or "",
                     title=h.get("title") or h.get("name") or "")
        if not rec:
            continue
        if has_cjk(rec["name_cn"]):
            h["name_cn"] = rec["name_cn"]
        url = mcmod_url(rec["mcmod_id"])
        if url:
            h["mcmod_url"] = url


def _reset_for_tests():
    global _records, _by_slug, _by_title, _load_failed
    with _lock:
        _records = None
        _by_slug = {}
        _by_title = {}
        _load_failed = False
