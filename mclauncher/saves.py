# -*- coding: utf-8 -*-
"""存档 / 截图 / 崩溃报告 / 日志浏览 / 存档备份。"""
from __future__ import annotations

import re
import shutil
import time
import zipfile
from pathlib import Path

from . import utils
from . import version_settings as vs
from .crash import open_path
from .instances import Instance

BACKUP_DIR_NAME = "backups"
_STAMP_RE = re.compile(r"-(\d{8}-\d{6})$")


class SaveError(Exception):
    pass


def _game_dir(instance: Instance, version_id: str = "") -> Path:
    if version_id:
        return vs.game_dir(instance, version_id)
    return Path(instance.path)


def _safe_child(folder: Path, name: str, what: str = "存档") -> Path:
    """只允许访问 folder 的直接子项，挡掉 ../ 之类的路径穿越。"""
    folder = folder.resolve()
    target = (folder / name).resolve()
    if target.parent != folder:
        raise SaveError(f"非法{what}名: {name}")
    return target


GAME_MODES = {0: "生存", 1: "创造", 2: "冒险", 3: "旁观"}
DIFFICULTIES = {0: "和平", 1: "简单", 2: "普通", 3: "困难"}


def level_summary(save_dir) -> dict:
    """解析 level.dat（NBT）：世界名、版本、模式、难度、种子、上次游玩。

    存档损坏 / 格式不认识时返回 {}，不影响列表其余字段。
    """
    from pathlib import Path as _P

    from . import nbt
    f = _P(save_dir) / "level.dat"
    if not f.is_file():
        return {}
    try:
        data = (nbt.read_file(f) or {}).get("Data") or {}
    except Exception as e:
        utils.log.debug("level.dat 解析失败 %s: %s", f, e)
        return {}
    if not isinstance(data, dict):
        return {}
    version = data.get("Version") if isinstance(data.get("Version"), dict) else {}
    # 1.16+ 种子在 WorldGenSettings.seed，更早在 RandomSeed
    wgs = data.get("WorldGenSettings") if isinstance(data.get("WorldGenSettings"), dict) else {}
    seed = wgs.get("seed", data.get("RandomSeed"))
    mode_code = data.get("GameType")
    try:
        mode_code = int(mode_code)
    except (TypeError, ValueError):
        mode_code = None
    diff_code = data.get("Difficulty")
    try:
        diff_code = int(diff_code)
    except (TypeError, ValueError):
        diff_code = None
    out = {
        "level_name": str(data.get("LevelName") or ""),
        "mc_version": str(version.get("Name") or ""),
        "game_mode": GAME_MODES.get(mode_code, ""),
        "game_mode_code": mode_code,
        "difficulty": DIFFICULTIES.get(diff_code, ""),
        "hardcore": bool(data.get("hardcore")),
        "cheats": bool(data.get("allowCommands")),
        "seed": str(seed) if seed is not None else "",
    }
    try:
        out["last_played"] = int(int(data.get("LastPlayed") or 0) / 1000)
    except (TypeError, ValueError):
        out["last_played"] = 0
    return out


def list_saves(instance: Instance, version_id: str = "") -> list[dict]:
    folder = _game_dir(instance, version_id) / "saves"
    if not folder.is_dir():
        return []
    rows = []
    for p in sorted(folder.iterdir(), key=lambda x: x.name.lower()):
        if not p.is_dir() or p.name.startswith("."):
            continue
        icon = p / "icon.png"
        row = {
            "name": p.name,
            "path": str(p),
            "icon": str(icon) if icon.is_file() else "",
            "bytes": _dir_size(p),
            "mtime": int(p.stat().st_mtime),
        }
        row.update(level_summary(p))
        rows.append(row)
    return rows


def delete_save(instance: Instance, name: str, version_id: str = ""):
    target = _safe_child(_game_dir(instance, version_id) / "saves", name)
    if not target.exists():
        raise SaveError(f"存档不存在: {name}")
    from . import trash
    trash.trash_or_delete(target)


def open_save(instance: Instance, name: str, version_id: str = "") -> str:
    folder = _game_dir(instance, version_id) / "saves" / name
    if not folder.is_dir():
        raise SaveError(f"存档不存在: {name}")
    open_path(folder)
    return str(folder)


def _mcmeta_description(raw) -> str:
    """pack.mcmeta 的 description：可能是字符串 / 文本组件 / 组件列表。"""
    if isinstance(raw, str):
        return raw.strip()
    if isinstance(raw, dict):
        return str(raw.get("text") or "").strip()
    if isinstance(raw, list):
        bits = []
        for item in raw:
            if isinstance(item, str):
                bits.append(item)
            elif isinstance(item, dict):
                bits.append(str(item.get("text") or ""))
        return "".join(bits).strip()
    return ""


def _read_pack_mcmeta(path: Path) -> dict:
    """读数据包的 pack.mcmeta（zip 或文件夹包）。坏包返回 {}。"""
    import json
    try:
        if path.is_dir():
            f = path / "pack.mcmeta"
            if not f.is_file():
                return {}
            data = json.loads(f.read_text("utf-8", errors="replace"))
        else:
            with zipfile.ZipFile(path) as zf:
                data = json.loads(zf.read("pack.mcmeta").decode("utf-8", errors="replace"))
    except (OSError, ValueError, KeyError, zipfile.BadZipFile):
        return {}
    pack = data.get("pack") if isinstance(data, dict) else None
    if not isinstance(pack, dict):
        return {}
    fmt = pack.get("pack_format")
    return {
        "description": _mcmeta_description(pack.get("description")),
        "pack_format": int(fmt) if isinstance(fmt, (int, float)) else 0,
    }


def _datapack_states(save_dir: Path) -> dict:
    """level.dat 里 Data.DataPacks 的启用状态：{名: True/False}。

    条目形如 "file/foo.zip"。level.dat 缺失或没有 DataPacks 段返回 {}，
    调用方把状态标为未知。
    """
    from . import nbt
    f = save_dir / "level.dat"
    if not f.is_file():
        return {}
    try:
        data = (nbt.read_file(f) or {}).get("Data") or {}
    except Exception:
        return {}
    packs = data.get("DataPacks") if isinstance(data, dict) else None
    if not isinstance(packs, dict):
        return {}
    out = {}
    for key, flag in (("Enabled", True), ("Disabled", False)):
        for entry in packs.get(key) or []:
            s = str(entry)
            if s.startswith("file/"):
                out[s[len("file/"):]] = flag
    return out


def list_world_datapacks(instance: Instance, save_name: str,
                         version_id: str = "") -> list[dict]:
    """某个世界里已装的数据包（对标 HMCL 世界管理的数据包页）。

    返回 [{name, is_dir, bytes, description, pack_format, enabled}]；
    enabled 从 level.dat 的 DataPacks 段读，读不到为 None（未知）。
    """
    world = _safe_child(_game_dir(instance, version_id) / "saves", save_name)
    if not world.is_dir():
        raise SaveError(f"存档不存在: {save_name}")
    dp_dir = world / "datapacks"
    if not dp_dir.is_dir():
        return []
    states = _datapack_states(world)
    rows = []
    for p in sorted(dp_dir.iterdir(), key=lambda x: x.name.lower()):
        if p.is_file() and p.suffix.lower() != ".zip":
            continue
        meta = _read_pack_mcmeta(p)
        if p.is_dir():
            size = sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
        else:
            size = p.stat().st_size
        rows.append({
            "name": p.name,
            "is_dir": p.is_dir(),
            "bytes": size,
            "description": meta.get("description") or "",
            "pack_format": meta.get("pack_format") or 0,
            "enabled": states.get(p.name),
        })
    return rows


def delete_world_datapack(instance: Instance, save_name: str, filename: str,
                          version_id: str = ""):
    """从世界里删除一个数据包（尽量移入回收站）。"""
    world = _safe_child(_game_dir(instance, version_id) / "saves", save_name)
    target = _safe_child(world / "datapacks", filename, "数据包")
    if not target.exists():
        raise SaveError(f"数据包不存在: {filename}")
    from . import trash
    trash.trash_or_delete(target)


def install_datapack_into_save(instance: Instance, filename: str, save_name: str,
                               version_id: str = "") -> str:
    src = (instance.path / "datapacks" / filename).resolve()
    root = (instance.path / "datapacks").resolve()
    if src.parent != root or not src.is_file():
        raise SaveError(f"数据包不存在: {filename}")
    dest_dir = _game_dir(instance, version_id) / "saves" / save_name / "datapacks"
    utils.ensure_dir(dest_dir)
    dest = dest_dir / src.name
    shutil.copy2(src, dest)
    return str(dest)


def list_media(instance: Instance, kind: str, version_id: str = "") -> list[dict]:
    mapping = {
        "screenshots": ("screenshots", (".png", ".jpg", ".jpeg")),
        "crash-reports": ("crash-reports", (".txt",)),
        "logs": ("logs", (".log", ".gz", ".txt")),
    }
    if kind not in mapping:
        raise SaveError(f"未知类型: {kind}")
    sub, exts = mapping[kind]
    folder = _game_dir(instance, version_id) / sub
    if not folder.is_dir():
        return []
    rows = []
    for p in sorted(folder.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if p.is_file() and p.suffix.lower() in exts:
            rows.append({
                "name": p.name,
                "path": str(p),
                "bytes": p.stat().st_size,
                "mtime": int(p.stat().st_mtime),
            })
    return rows[:200]


def backups_dir(instance: Instance, version_id: str = "") -> Path:
    """备份跟着存档走：版本隔离时进版本目录，否则进实例目录。"""
    return _game_dir(instance, version_id) / BACKUP_DIR_NAME


def backup_save(instance: Instance, name: str, version_id: str = "",
                on_progress=None) -> dict:
    """把一个存档打包成 zip 存到 backups/，返回备份信息。"""
    src = _safe_child(_game_dir(instance, version_id) / "saves", name)
    if not src.is_dir():
        raise SaveError(f"存档不存在: {name}")
    dest_dir = backups_dir(instance, version_id)
    utils.ensure_dir(dest_dir)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = dest_dir / f"{src.name}-{stamp}.zip"
    n = 1
    while dest.exists():
        dest = dest_dir / f"{src.name}-{stamp}-{n}.zip"
        n += 1

    files = [p for p in src.rglob("*") if p.is_file()]
    total = len(files) or 1
    tmp = dest.with_suffix(".zip.part")
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for i, p in enumerate(files, 1):
                zf.write(p, str(Path(src.name) / p.relative_to(src)))
                if on_progress:
                    on_progress(f"备份 {src.name}", i, total)
        tmp.replace(dest)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    return {
        "name": dest.name,
        "path": str(dest),
        "save": src.name,
        "bytes": dest.stat().st_size,
        "mtime": int(dest.stat().st_mtime),
    }


def list_backups(instance: Instance, save_name: str = "", version_id: str = "") -> list[dict]:
    folder = backups_dir(instance, version_id)
    if not folder.is_dir():
        return []
    rows = []
    for p in folder.glob("*.zip"):
        if not p.is_file():
            continue
        origin = _STAMP_RE.sub("", p.stem)
        if save_name and origin != save_name:
            continue
        rows.append({
            "name": p.name,
            "path": str(p),
            "save": origin,
            "bytes": p.stat().st_size,
            "mtime": int(p.stat().st_mtime),
        })
    rows.sort(key=lambda r: r["mtime"], reverse=True)
    return rows


def restore_backup(instance: Instance, backup_name: str, version_id: str = "",
                   target_name: str = "", overwrite: bool = False) -> dict:
    """把备份还原回 saves/。默认不覆盖同名存档，而是加 -还原 后缀另存。"""
    archive = _safe_child(backups_dir(instance, version_id), backup_name, "备份")
    if not archive.is_file():
        raise SaveError(f"备份不存在: {backup_name}")
    saves_root = _game_dir(instance, version_id) / "saves"
    utils.ensure_dir(saves_root)
    origin = target_name or _STAMP_RE.sub("", archive.stem)
    dest = _safe_child(saves_root, origin)
    if dest.exists():
        if not overwrite:
            n = 1
            while dest.exists():
                dest = _safe_child(saves_root, f"{origin}-还原{n if n > 1 else ''}")
                n += 1
        else:
            utils.remove_tree(dest)

    try:
        zf = zipfile.ZipFile(archive)
    except zipfile.BadZipFile as exc:
        raise SaveError(f"备份文件损坏: {exc}") from exc
    with zf:
        names = [n for n in zf.namelist() if not n.endswith("/")]
        if not names:
            raise SaveError("备份是空的")
        roots = {n.replace("\\", "/").split("/")[0] for n in names}
        # 备份是我们自己打的，正常只有一个顶层目录；解到临时目录再挪，避免污染 saves/
        staging = saves_root / f".restore-{int(time.time())}"
        utils.remove_tree(staging)
        utils.ensure_dir(staging)
        try:
            for member in names:
                if Path(member).is_absolute() or ".." in Path(member).parts:
                    raise SaveError(f"备份包含非法路径: {member}")
            zf.extractall(staging)
            inner = staging / roots.pop() if len(roots) == 1 else staging
            shutil.move(str(inner), str(dest))
        finally:
            utils.remove_tree(staging)
    return {"name": dest.name, "path": str(dest), "from": archive.name}


def delete_backup(instance: Instance, backup_name: str, version_id: str = ""):
    archive = _safe_child(backups_dir(instance, version_id), backup_name, "备份")
    if not archive.is_file():
        raise SaveError(f"备份不存在: {backup_name}")
    from . import trash
    trash.trash_or_delete(archive)


def export_save(instance: Instance, name: str, dest: str, version_id: str = "",
                on_progress=None) -> str:
    """把存档导出成任意位置的 zip，便于分享或搬到别的启动器。"""
    src = _safe_child(_game_dir(instance, version_id) / "saves", name)
    if not src.is_dir():
        raise SaveError(f"存档不存在: {name}")
    out = Path(dest)
    if out.is_dir():
        out = out / f"{src.name}.zip"
    if out.suffix.lower() != ".zip":
        out = out.with_suffix(".zip")
    utils.ensure_dir(out.parent)
    files = [p for p in src.rglob("*") if p.is_file()]
    total = len(files) or 1
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for i, p in enumerate(files, 1):
            zf.write(p, str(Path(src.name) / p.relative_to(src)))
            if on_progress:
                on_progress(f"导出 {src.name}", i, total)
    return str(out)


def _dir_size(path: Path, limit=80) -> int:
    total = 0
    n = 0
    try:
        for p in path.rglob("*"):
            if p.is_file():
                total += p.stat().st_size
                n += 1
                if n >= limit:
                    break
    except OSError:
        pass
    return total
