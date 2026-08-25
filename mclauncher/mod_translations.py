# -*- coding: utf-8 -*-
"""mcmod.cn 中文数据库：模组/整合包中文名搜索、显示、百科链接。

对齐 PCL2 / HMCL：两者都内置了 mcmod.cn 的中英文对照表（2 万+ 条），
支撑三条用户路径——用中文名搜任意模组、列表里显示中文名、一键跳
mcmod.cn 百科页。数据版权归 mcmod.cn，PyMCL 不随包分发，首次用到时
从 HMCL 仓库下载并缓存（14 天刷新一次），离线时优雅退化为无翻译。

行格式（6 个分号字段，与 HMCL ModTranslations 一致）:
    curseforge_slug;mcmod_id;modIds(逗号分隔);中文名;英文名;缩写
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

from . import utils
from .mirrors import github_candidates

_HMCL_RAW = ("https://raw.githubusercontent.com/HMCL-dev/HMCL/main/"
             "HMCL/src/main/resources/assets/{name}")

# kind -> (数据文件名, mcmod.cn 路径段)
_KINDS = {
    "mod": ("mod_data.txt", "class"),
    "modpack": ("modpack_data.txt", "modpack"),
}

REFRESH_SECONDS = 14 * 86400

_lock = threading.Lock()
# kind -> {"mtime": float, "records": [...], "by_slug": {}, "by_id": {},
#          "by_subname": {}, "keywords": [(关键词小写, record), ...]}
_cache: dict = {}


def contains_cjk(text: str) -> bool:
    """查询里带中文时才走中文搜索路径（英文交给 Modrinth/CF 全文索引）。"""
    return any("\u4e00" <= ch <= "\u9fff" for ch in (text or ""))


def data_path(kind: str = "mod") -> Path:
    return utils.ROOT / "cache" / _KINDS[kind][0]


def available(kind: str = "mod") -> bool:
    """本地已有缓存数据（不触发网络）。"""
    try:
        return data_path(kind).is_file()
    except KeyError:
        return False


def ensure_data(kind: str = "mod", dm=None, max_age=REFRESH_SECONDS):
    """确保本地缓存存在且不过旧；失败时保留旧数据。返回 Path 或 None。"""
    path = data_path(kind)
    try:
        fresh = path.is_file() and (time.time() - path.stat().st_mtime) < max_age
    except OSError:
        fresh = False
    if fresh:
        return path
    url = _HMCL_RAW.format(name=_KINDS[kind][0])
    text = None
    if dm is not None:
        try:
            text = dm.fetch_text(url, timeout=(4, 30), expand=False)
        except Exception as exc:
            utils.log.warning("mcmod 数据库下载失败（DownloadManager）: %s", exc)
    if text is None:
        import requests
        for cand in github_candidates(url):
            try:
                resp = requests.get(cand, timeout=(4, 30))
                resp.raise_for_status()
                text = resp.text
                break
            except Exception as exc:
                utils.log.warning("mcmod 数据库下载失败 %s: %s", cand, exc)
    if text is None or ";" not in text:
        return path if path.is_file() else None
    utils.ensure_dir(path.parent)
    tmp = path.with_suffix(".txt.part")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
    return path


# ---------------------------------------------------------------- 解析与索引

def clean_subname(subname: str) -> str:
    """HMCL cleanSubname：去掉空白与装饰符号；含不认识的字符则弃用该英文名。"""
    if not subname:
        return ""
    out = []
    for ch in subname:
        if ch.isascii() and (ch.isalnum() or ch in ".+\\"):
            out.append(ch)
        elif (ch.isspace() or ch in "':_-/&()[]{}|,!?~•"
              or 0x1F300 <= ord(ch) <= 0x1FAFF):
            continue
        else:
            return ""
    return "".join(out)


def _parse(path: Path) -> list[dict]:
    records = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return records
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        items = line.split(";")
        if len(items) != 6:
            continue
        cf, mcmod, mod_ids, name, subname, abbr = items
        records.append({
            "curseforge": cf.strip(),
            "mcmod": mcmod.strip(),
            "mod_ids": [x for x in (m.strip() for m in mod_ids.split(",")) if x],
            "name": name.strip(),
            "subname": subname.strip(),
            "abbr": abbr.strip(),
        })
    return records


def _load(kind: str = "mod"):
    """带索引的懒加载；文件更新（mtime 变化）后自动重建。"""
    path = data_path(kind)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    with _lock:
        got = _cache.get(kind)
        if got and got["mtime"] == mtime:
            return got
        records = _parse(path)
        by_slug, by_id, by_subname = {}, {}, {}
        keywords = []
        for rec in records:
            slug = rec["curseforge"]
            if slug:
                by_slug.setdefault(slug.lower(), rec)
            for mid in rec["mod_ids"]:
                by_id.setdefault(mid, rec)
            sub = clean_subname(rec["subname"])
            if sub:
                by_subname.setdefault(sub.lower(), rec)
            for kw in (rec["name"], rec["subname"], rec["abbr"]):
                if kw:
                    keywords.append((kw.lower(), rec))
        got = {"mtime": mtime, "records": records, "by_slug": by_slug,
               "by_id": by_id, "by_subname": by_subname, "keywords": keywords}
        _cache[kind] = got
        return got


# ---------------------------------------------------------------- 查询

def lookup_by_slug(slug: str, kind: str = "mod"):
    """按 CurseForge slug 找记录（Modrinth slug 大多与之相同，也能命中）。"""
    if not slug:
        return None
    data = _load(kind)
    if not data:
        return None
    return data["by_slug"].get(str(slug).strip().lower())


def lookup_local(mod_id: str = "", name: str = "", kind: str = "mod"):
    """本地已装模组 → 记录。与 HMCL 相同：先按英文名（须与 modid 一致），再按 modid。"""
    data = _load(kind)
    if not data:
        return None
    sub = clean_subname(name or "").lower()
    if sub:
        rec = data["by_subname"].get(sub)
        if rec and (not mod_id or mod_id in rec["mod_ids"]):
            return rec
    if mod_id:
        return data["by_id"].get(mod_id)
    return None


def mcmod_url(record: dict, kind: str = "mod") -> str:
    mcmod_id = (record or {}).get("mcmod") or ""
    if not mcmod_id:
        return ""
    return f"https://www.mcmod.cn/{_KINDS[kind][1]}/{mcmod_id}.html"


def display_name(record: dict) -> str:
    """HMCL 同款展示名：[缩写] 中文名 (英文名)。"""
    parts = []
    if record.get("abbr"):
        parts.append(f"[{record['abbr'].strip()}]")
    parts.append(record.get("name") or "")
    if record.get("subname"):
        parts.append(f"({record['subname']})")
    return " ".join(p for p in parts if p)


def _lcs_len(a: str, b: str) -> int:
    """最长公共子序列长度（滚动数组），与 HMCL 搜索评分一致。"""
    if not a or not b:
        return 0
    prev = [0] * (len(b) + 1)
    for ca in a:
        cur = [0]
        for j, cb in enumerate(b):
            cur.append(prev[j] + 1 if ca == cb else max(prev[j + 1], cur[j]))
        prev = cur
    return prev[-1]


def search(query: str, kind: str = "mod", limit: int = 10) -> list[dict]:
    """中文/英文名模糊搜索。评分规则与 HMCL 一致：
    去空格后按 LCS 匹配关键词（中文名/英文名/缩写），LCS ≥ len(query)-3 记为命中。
    为了速度先用「共享字符」粗筛（中文查询下候选极少）。"""
    data = _load(kind)
    if not data:
        return []
    q = "".join((query or "").split()).lower()
    if not q:
        return []
    qchars = set(q)
    min_score = max(1, len(q) - 3)
    scored: dict[int, tuple[int, dict]] = {}
    for kw, rec in data["keywords"]:
        if not qchars & set(kw):
            continue
        # 子串命中直接给满分，跳过 LCS
        if q in kw:
            score = len(q)
        else:
            if len(kw) < min_score:
                continue
            score = _lcs_len(q, kw)
        if score < min_score:
            continue
        key = id(rec)
        if key not in scored or scored[key][0] < score:
            scored[key] = (score, rec)
    ranked = sorted(scored.values(), key=lambda t: -t[0])
    return [rec for _s, rec in ranked[:limit]]


def annotate_hits(hits, kind: str = "mod"):
    """给搜索结果补 chinese_name / mcmod_url 字段（只读本地缓存，不触发网络）。"""
    if not hits or not available(kind):
        return hits
    for h in hits:
        if not isinstance(h, dict) or h.get("chinese_name"):
            continue
        rec = lookup_by_slug(str(h.get("slug") or ""), kind)
        if not rec:
            continue
        if rec.get("name"):
            h["chinese_name"] = rec["name"]
        url = mcmod_url(rec, kind)
        if url:
            h["mcmod_url"] = url
    return hits
