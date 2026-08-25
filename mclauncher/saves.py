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


_GAME_MODES = {0: "生存", 1: "创造", 2: "冒险", 3: "旁观"}
_DIFFICULTIES = {0: "和平", 1: "简单", 2: "普通", 3: "困难"}


def read_level_meta(save_dir) -> dict:
    """解析存档 level.dat（gzip NBT），失败时返回空 dict。"""
    from . import nbt_lite as nbt
    p = Path(save_dir) / "level.dat"
    if not p.is_file():
        return {}
    try:
        _root_name, root = nbt.loads(p.read_bytes())
    except Exception:
        return {}
    data_tag = root.get("Data")
    if not (isinstance(data_tag, tuple) and data_tag[0] == nbt.TAG_COMPOUND):
        return {}
    data = data_tag[1]

    def val(key, default=None):
        tag = data.get(key)
        return tag[1] if isinstance(tag, tuple) else default

    version_name = ""
    vtag = data.get("Version")
    if isinstance(vtag, tuple) and vtag[0] == nbt.TAG_COMPOUND:
        ntag = vtag[1].get("Name")
        version_name = ntag[1] if isinstance(ntag, tuple) else ""
    game_type = int(val("GameType", 0) or 0)
    hardcore = bool(val("hardcore", 0))
    mode = "硬核" if hardcore else _GAME_MODES.get(game_type, "?")
    # 种子：1.16+ 在 WorldGenSettings.seed，更早在 Data.RandomSeed
    seed = None
    wg = data.get("WorldGenSettings")
    if isinstance(wg, tuple) and wg[0] == nbt.TAG_COMPOUND:
        stag = wg[1].get("seed")
        if isinstance(stag, tuple):
            seed = int(stag[1])
    if seed is None:
        rtag = data.get("RandomSeed")
        if isinstance(rtag, tuple):
            seed = int(rtag[1])
    diff = val("Difficulty")
    return {
        "level_name": str(val("LevelName", "") or ""),
        "version_name": str(version_name),
        "game_type": game_type,
        "mode": mode,
        "hardcore": hardcore,
        "cheats": bool(val("allowCommands", 0)),
        # Difficulty 在老版本 level.dat 里可能不存在（当时难度是全局设置）
        "difficulty": int(diff) if diff is not None else None,
        "difficulty_locked": bool(val("DifficultyLocked", 0)),
        "seed": seed,
        # LastPlayed 是毫秒时间戳
        "last_played": int(val("LastPlayed", 0) or 0) // 1000,
    }


def world_info(instance: Instance, name: str, version_id: str = "") -> dict:
    """单个存档的详细信息（供「修改世界信息」对话框使用）。"""
    save_dir = _safe_child(_game_dir(instance, version_id) / "saves", name)
    if not save_dir.is_dir():
        raise SaveError(f"存档不存在: {name}")
    meta = read_level_meta(save_dir)
    if not meta:
        raise SaveError(f"无法读取 level.dat: {name}")
    meta["name"] = name
    meta["path"] = str(save_dir)
    return meta


# 可编辑字段 → (Data 里的键, NBT tag 类型, 取值转换)。对齐 HMCL 世界信息编辑。
def _world_fields():
    from . import nbt_lite as nbt

    def _mode(v):
        n = int(v)
        if n not in _GAME_MODES:
            raise SaveError(f"游戏模式必须是 0-3，收到 {v}")
        return n

    def _diff(v):
        n = int(v)
        if n not in _DIFFICULTIES:
            raise SaveError(f"难度必须是 0-3，收到 {v}")
        return n

    def _name(v):
        s = str(v).strip()
        if not s:
            raise SaveError("世界名称不能为空")
        return s

    return {
        "level_name": ("LevelName", nbt.TAG_STRING, _name),
        "game_type": ("GameType", nbt.TAG_INT, _mode),
        "difficulty": ("Difficulty", nbt.TAG_BYTE, _diff),
        "difficulty_locked": ("DifficultyLocked", nbt.TAG_BYTE, lambda v: 1 if v else 0),
        "cheats": ("allowCommands", nbt.TAG_BYTE, lambda v: 1 if v else 0),
        "hardcore": ("hardcore", nbt.TAG_BYTE, lambda v: 1 if v else 0),
    }


def edit_world(instance: Instance, name: str, changes: dict, version_id: str = "") -> dict:
    """改写存档 level.dat 的基本信息（世界名/模式/难度/作弊……）。

    只动指定键，其余 NBT 原样保留；写入前把旧文件备份成 level.dat_old
    （与游戏自身的保存行为一致）。返回修改后的 world_info。
    """
    import gzip

    from . import nbt_lite as nbt
    save_dir = _safe_child(_game_dir(instance, version_id) / "saves", name)
    level = save_dir / "level.dat"
    if not level.is_file():
        raise SaveError(f"存档不存在或缺少 level.dat: {name}")

    fields = _world_fields()
    unknown = set(changes or {}) - set(fields)
    if unknown:
        raise SaveError("不支持修改这些字段: " + ", ".join(sorted(unknown)))
    if not changes:
        return world_info(instance, name, version_id)

    raw = level.read_bytes()
    was_gzip = raw[:2] == b"\x1f\x8b"
    try:
        root_name, root = nbt.loads(raw)
    except Exception as exc:
        raise SaveError(f"level.dat 解析失败: {exc}") from exc
    data_tag = root.get("Data")
    if not (isinstance(data_tag, tuple) and data_tag[0] == nbt.TAG_COMPOUND):
        raise SaveError("level.dat 缺少 Data 标签，不是有效的存档")
    data = data_tag[1]

    for key, value in changes.items():
        nbt_key, tag_type, convert = fields[key]
        data[nbt_key] = (tag_type, convert(value))

    out = nbt.dumps(root, root_name)
    if was_gzip:
        out = gzip.compress(out)
    # 与游戏一致：旧内容挪到 level.dat_old，再原子替换
    tmp = level.with_suffix(".dat.pymcl-new")
    tmp.write_bytes(out)
    try:
        shutil.copy2(level, save_dir / "level.dat_old")
    except OSError:
        pass
    tmp.replace(level)
    return world_info(instance, name, version_id)


def list_saves(instance: Instance, version_id: str = "") -> list[dict]:
    folder = _game_dir(instance, version_id) / "saves"
    if not folder.is_dir():
        return []
    rows = []
    for p in sorted(folder.iterdir(), key=lambda x: x.name.lower()):
        if not p.is_dir() or p.name.startswith("."):
            continue
        icon = p / "icon.png"
        rows.append({
            "name": p.name,
            "path": str(p),
            "icon": str(icon) if icon.is_file() else "",
            "bytes": _dir_size(p),
            "mtime": int(p.stat().st_mtime),
            **read_level_meta(p),
        })
    return rows


def delete_save(instance: Instance, name: str, version_id: str = ""):
    target = _safe_child(_game_dir(instance, version_id) / "saves", name)
    if not target.exists():
        raise SaveError(f"存档不存在: {name}")
    utils.remove_tree(target)


def open_save(instance: Instance, name: str, version_id: str = "") -> str:
    folder = _game_dir(instance, version_id) / "saves" / name
    if not folder.is_dir():
        raise SaveError(f"存档不存在: {name}")
    open_path(folder)
    return str(folder)


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
    archive.unlink()


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
