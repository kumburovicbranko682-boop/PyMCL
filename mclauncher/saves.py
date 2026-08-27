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

CHUNKBASE_APPS = ("seed-map", "stronghold-finder", "biome-finder", "slime-finder")


def chunkbase_url(seed, mc_version: str = "", app: str = "seed-map") -> str:
    """Chunk Base 在线工具链接（HMCL 世界管理「Chunk Base」同款入口）。

    seed 必填（level.dat 里的数字种子，可为负）；mc_version 是正式版号
    （如 1.21.4）时带上 platform=java_1_21_4，快照等其他格式交给网站默认。
    """
    from urllib.parse import quote

    seed = str(seed or "").strip()
    if not seed:
        raise SaveError("这个存档的 level.dat 里没有种子信息")
    if app not in CHUNKBASE_APPS:
        app = "seed-map"
    frag = f"seed={quote(seed, safe='-')}"
    m = re.fullmatch(r"(\d+)\.(\d+)(?:\.(\d+))?", str(mc_version or "").strip())
    if m:
        frag += "&platform=java_" + "_".join(g for g in m.groups() if g)
    return f"https://www.chunkbase.com/apps/{app}#{frag}"


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
        "difficulty_code": diff_code,
        "difficulty_locked": bool(data.get("DifficultyLocked")),
        "hardcore": bool(data.get("hardcore")),
        "cheats": bool(data.get("allowCommands")),
        "seed": str(seed) if seed is not None else "",
    }
    try:
        out["last_played"] = int(int(data.get("LastPlayed") or 0) / 1000)
    except (TypeError, ValueError):
        out["last_played"] = 0
    return out


def world_info(world_dir) -> dict:
    """level.dat 世界元数据的另一种视图（键名对齐旧皮肤分支调用方）。

    与 level_summary 的差别：game_type 数字码、硬核显示为「硬核」、
    版本键叫 version。坏档或缺文件返回 {}。
    """
    s = level_summary(world_dir)
    if not s:
        return {}
    mode_code = s.get("game_mode_code")
    hardcore = bool(s.get("hardcore"))
    return {
        "level_name": s.get("level_name", ""),
        "seed": s.get("seed", ""),
        "game_type": mode_code if mode_code is not None else -1,
        "game_mode": ("硬核" if hardcore and mode_code == 0
                      else s.get("game_mode", "")),
        "hardcore": hardcore,
        "cheats": bool(s.get("cheats")),
        "difficulty": s.get("difficulty", ""),
        "version": s.get("mc_version", ""),
        "last_played": int(s.get("last_played") or 0),
    }


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


# ---------------------------------------------------------------- 世界信息编辑

GAME_MODE_CODES = {"survival": 0, "creative": 1, "adventure": 2, "spectator": 3}


def edit_world(instance: Instance, name: str, changes: dict,
               version_id: str = "") -> dict:
    """编辑 level.dat（HMCL 世界信息页同款字段）。返回更新后的 level_summary。

    changes 支持的键（都可选）：
      level_name        世界名（非空字符串）
      allow_cheats      允许作弊 bool → Data.allowCommands
      difficulty        0~3 → Data.Difficulty
      difficulty_locked bool → Data.DifficultyLocked
      game_mode         0~3 / survival/creative/adventure/spectator / "hardcore"
                        → Data.GameType + Data.hardcore + Data.Player.playerGameType
                        （极限 = 生存 + hardcore 标记，同 HMCL）

    写之前把原 level.dat 复制成 level.dat.pymcl_bak（每次编辑刷新）。
    """
    from . import nbt
    save_dir = _safe_child(_game_dir(instance, version_id) / "saves", name)
    f = save_dir / "level.dat"
    if not f.is_file():
        raise SaveError(f"存档没有 level.dat: {name}")
    changes = dict(changes or {})
    raw = f.read_bytes()
    compressed = raw[:2] == b"\x1f\x8b"
    try:
        root_name, root = nbt.loads_typed(raw)
    except nbt.NBTError as e:
        raise SaveError(f"level.dat 损坏，无法编辑: {e}")
    data_tag = root[1].get("Data")
    if not data_tag or data_tag[0] != nbt.TAG_COMPOUND:
        raise SaveError("level.dat 缺少 Data 复合标签")
    d = data_tag[1]

    if "level_name" in changes:
        new_name = str(changes["level_name"] or "").strip()
        if not new_name:
            raise SaveError("世界名不能为空")
        d["LevelName"] = (nbt.TAG_STRING, new_name)
    if "allow_cheats" in changes:
        d["allowCommands"] = (nbt.TAG_BYTE, 1 if changes["allow_cheats"] else 0)
    if "difficulty" in changes:
        try:
            diff = int(changes["difficulty"])
        except (TypeError, ValueError):
            raise SaveError(f"难度无效: {changes['difficulty']}")
        if diff not in (0, 1, 2, 3):
            raise SaveError(f"难度无效: {diff}")
        d["Difficulty"] = (nbt.TAG_BYTE, diff)
    if "difficulty_locked" in changes:
        d["DifficultyLocked"] = (nbt.TAG_BYTE, 1 if changes["difficulty_locked"] else 0)
    if "game_mode" in changes:
        mode_raw = changes["game_mode"]
        hardcore = str(mode_raw).lower() == "hardcore"
        if hardcore:
            mode = 0
        else:
            mode = GAME_MODE_CODES.get(str(mode_raw).lower(), mode_raw)
            try:
                mode = int(mode)
            except (TypeError, ValueError):
                raise SaveError(f"游戏模式无效: {mode_raw}")
            if mode not in (0, 1, 2, 3):
                raise SaveError(f"游戏模式无效: {mode_raw}")
        d["GameType"] = (nbt.TAG_INT, mode)
        d["hardcore"] = (nbt.TAG_BYTE, 1 if hardcore else 0)
        player = d.get("Player")
        if player and player[0] == nbt.TAG_COMPOUND:
            player[1]["playerGameType"] = (nbt.TAG_INT, mode)

    shutil.copy2(f, save_dir / "level.dat.pymcl_bak")
    f.write_bytes(nbt.dumps_typed(root_name, root, compress=compressed))
    return level_summary(save_dir)


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
