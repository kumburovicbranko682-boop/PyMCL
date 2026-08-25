# -*- coding: utf-8 -*-
"""模组/整合包中文名数据库（mcmod.cn 派生，数据文件由 HMCL 项目维护更新）。

对齐 PCL2「Mod 中文数据」与 HMCL ModTranslations：
- 搜索结果 / 详情显示中文名：``[缩写] 中文名 (英文名)``
- 中文关键词搜索：把中文映射到 CurseForge slug / 英文名再去源站搜
- mcmod.cn 百科链接

数据文件不随仓库分发（版权归 mcmod.cn，整理归 HMCL 项目），首次用到时
从 HMCL 仓库在线拉取（fetch_text 自动走 GitHub 国内镜像），缓存 7 天，
拉取失败时继续用旧缓存。行格式（6 列，; 分隔）::

    curseforge_slug;mcmodID;modIds(逗号);中文名;英文名;缩写
"""
from __future__ import annotations

import re
import threading
import time
from pathlib import Path

from . import utils

_BASE = "https://raw.githubusercontent.com/HMCL-dev/HMCL/main/HMCL/src/main/resources/assets"
DATASETS = {
    "mod": {
        "url": f"{_BASE}/mod_data.txt",
        "cache": "mod_data.txt",
        "wiki": "https://www.mcmod.cn/class/{id}.html",
    },
    "modpack": {
        "url": f"{_BASE}/modpack_data.txt",
        "cache": "modpack_data.txt",
        "wiki": "https://www.mcmod.cn/modpack/{id}.html",
    },
}
CACHE_TTL = 7 * 24 * 3600

_lock = threading.Lock()
_records: dict[str, list[dict]] = {}
_slug_index: dict[str, dict] = {}
_modid_index: dict[str, dict] = {}
_warmed = False
# 拉取失败后的退避（秒）：断网时不至于每次搜索都卡在重试上
_FAIL_BACKOFF = 600
_last_fail: dict[str, float] = {}

_CJK_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uf900-\ufaff]")


def has_cjk(text) -> bool:
    return bool(_CJK_RE.search(str(text or "")))


def _cache_file(kind: str) -> Path:
    return utils.ROOT / "cache" / DATASETS[kind]["cache"]


def parse(text: str) -> list[dict]:
    """解析数据文件。# 开头是注释；列数不对的行直接跳过。"""
    out = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(";")
        if len(parts) != 6:
            continue
        curseforge, mcmod, modids, name, subname, abbr = (p.strip() for p in parts)
        if not name:
            continue
        out.append({
            "curseforge": curseforge,
            "mcmod": mcmod,
            "mod_ids": [m.strip() for m in modids.split(",") if m.strip()],
            "name": name,
            "subname": subname,
            "abbr": abbr,
        })
    return out


def _read_cache(kind: str) -> str:
    f = _cache_file(kind)
    try:
        return f.read_text("utf-8")
    except OSError:
        return ""


def _cache_fresh(kind: str) -> bool:
    f = _cache_file(kind)
    try:
        return time.time() - f.stat().st_mtime < CACHE_TTL
    except OSError:
        return False


def _fetch(kind: str, dm=None) -> str:
    if dm is None:
        from .downloader import DownloadManager
        dm = DownloadManager(threads=1)
    text = dm.fetch_text(DATASETS[kind]["url"], timeout=(5, 60))
    # 明显不是数据文件（比如镜像回了错误页）就当失败
    if not text or ";" not in text:
        raise ValueError("数据文件内容异常")
    f = _cache_file(kind)
    utils.ensure_dir(f.parent)
    f.write_text(text, "utf-8")
    return text


def load(kind: str = "mod", dm=None, allow_network: bool = True,
         force: bool = False) -> list[dict]:
    """取数据集（内存 → 磁盘缓存 → 网络）。全部失败返回 []，不抛。"""
    if kind not in DATASETS:
        return []
    with _lock:
        if not force and kind in _records:
            return _records[kind]
        text = ""
        if not force and _cache_fresh(kind):
            text = _read_cache(kind)
        recently_failed = time.time() - _last_fail.get(kind, 0) < _FAIL_BACKOFF
        if not text and allow_network and (force or not recently_failed):
            try:
                text = _fetch(kind, dm)
                _last_fail.pop(kind, None)
            except Exception as e:
                _last_fail[kind] = time.time()
                utils.log.warning("中文名数据 %s 拉取失败: %s", kind, e)
        if not text:
            text = _read_cache(kind)  # 过期缓存兜底
        recs = parse(text)
        if recs or not allow_network:
            _records[kind] = recs
        _slug_index.pop(kind, None)
        if kind == "mod":
            _modid_index.clear()
        return recs


def loaded(kind: str = "mod") -> bool:
    return kind in _records


def warm_async():
    """后台预热两个数据集（幂等），供英文搜索也能顺带标注中文名。"""
    global _warmed
    if _warmed:
        return
    _warmed = True

    def _run():
        for kind in DATASETS:
            try:
                load(kind)
            except Exception:
                pass
    threading.Thread(target=_run, daemon=True, name="mod-translate-warm").start()


def _slugs(kind: str) -> dict:
    idx = _slug_index.get(kind)
    if idx is None:
        idx = {}
        for rec in _records.get(kind) or []:
            slug = (rec.get("curseforge") or "").lower()
            if slug:
                idx.setdefault(slug, rec)
        _slug_index[kind] = idx
    return idx


def _modids() -> dict:
    if not _modid_index:
        for rec in _records.get("mod") or []:
            for mid in rec.get("mod_ids") or []:
                _modid_index.setdefault(mid.lower(), rec)
    return _modid_index


def display_label(rec: dict) -> str:
    """HMCL 同款显示名：[缩写] 中文名 (英文名)。"""
    if not rec:
        return ""
    parts = []
    if rec.get("abbr"):
        parts.append(f"[{rec['abbr'].strip()}]")
    parts.append(rec.get("name") or "")
    if rec.get("subname"):
        parts.append(f"({rec['subname']})")
    return " ".join(p for p in parts if p)


def wiki_url(rec: dict, kind: str = "mod") -> str:
    """mcmod.cn 百科页链接（数据里没有 mcmod id 时为空）。"""
    if not rec or not rec.get("mcmod") or kind not in DATASETS:
        return ""
    return DATASETS[kind]["wiki"].format(id=rec["mcmod"])


def for_slug(slug, kind: str = "mod") -> dict | None:
    """按 CurseForge slug 查记录（只查已加载数据，不碰网络）。

    Modrinth 的 slug 大多与 CurseForge 一致，作 best-effort 复用。
    """
    if not slug:
        return None
    return _slugs(kind).get(str(slug).lower())


def for_modid(modid) -> dict | None:
    """按本地模组的 modId 查记录（只查已加载数据）。"""
    if not modid:
        return None
    return _modids().get(str(modid).lower())


_PUNCT_RE = re.compile(r"[\s:：·\-—～~()（）!！?？'\"“”‘’,，。.]+")


def _squash(text: str) -> str:
    """去掉空白与常见标点，容忍「格雷科技新视野」vs「格雷科技：新视野」这类差异。"""
    return _PUNCT_RE.sub("", text or "")


def search_cn(query, kind: str = "mod", limit: int = 8, dm=None) -> list[dict]:
    """中文（或任意）关键词 → 数据集记录，按匹配度排序。"""
    q = str(query or "").strip().lower()
    if not q:
        return []
    recs = load(kind, dm=dm)
    tokens = [t for t in re.split(r"\s+", q) if t]
    if not tokens:
        return []
    q_sq = _squash(q)
    scored = []
    for rec in recs:
        name = (rec.get("name") or "").lower()
        sub = (rec.get("subname") or "").lower()
        abbr = (rec.get("abbr") or "").lower()
        score = 0
        for t in tokens:
            if t in (name, sub, abbr):
                score += 100
            elif name.startswith(t) or sub.startswith(t):
                score += 40
            elif t in name or t in sub or (abbr and t in abbr):
                score += 20
            else:
                score = 0
                break
        if not score and q_sq:
            # 整句去标点后再试一次（只对名字，防止英文误伤）
            name_sq = _squash(name)
            if name_sq and (q_sq in name_sq or name_sq in q_sq):
                score = 30
        if score:
            # 同分时名字越短越可能是本体而不是附属
            scored.append((-score + min(len(name), 60) * 0.01, len(name), rec))
    scored.sort(key=lambda x: (x[0], x[1]))
    return [rec for _, _, rec in scored[:limit]]


def best_cn_match(query, kind: str = "mod", dm=None) -> dict | None:
    """中文查询的最佳记录；非中文查询返回 None（不拦英文搜索）。"""
    if not has_cjk(query):
        return None
    hits = search_cn(query, kind=kind, limit=1, dm=dm)
    return hits[0] if hits else None


def annotate(rows, kind: str = "mod"):
    """给搜索结果行补 cn_name / cn_label / mcmod_url（就地，返回原列表）。

    只用已加载的数据，不碰网络；顺带发起后台预热让下次能标注。
    """
    warm_async()
    if not rows or not _records.get(kind):
        return rows
    for row in rows:
        if not isinstance(row, dict):
            continue
        rec = for_slug(row.get("slug"), kind)
        if not rec:
            continue
        # 标题本身已是中文（比如 CF 上的中文包）就不用重复标
        if has_cjk(row.get("title") or row.get("name") or ""):
            continue
        row["cn_name"] = rec.get("name") or ""
        row["cn_label"] = display_label(rec)
        url = wiki_url(rec, kind)
        if url:
            row["mcmod_url"] = url
    return rows
