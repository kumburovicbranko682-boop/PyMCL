# -*- coding: utf-8 -*-
"""实例（版本隔离）管理。每个实例是一个独立的 .minecraft 目录。"""
import re
from pathlib import Path

from . import utils
from .config import CONFIG

INSTANCE_META = ".instance.json"
JAVA_AUTO = "自动选择"
_MAX_INSTANCE_NAME = 48
_ILLEGAL_NAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_WIN_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

_STANDARD_DIRS = [
    "mods", "config", "saves", "resourcepacks", "shaderpacks",
    "datapacks",
    "screenshots", "crash-reports", "logs", "options",
    "screenshots", "crash-reports", "logs", "options",
    "servers", "texturepacks", "versions", "libraries",
]


class InstanceError(Exception):
    pass


def external_instances() -> dict:
    """外部游戏目录注册表 {实例名: 绝对路径}。

    对齐 HMCL「游戏目录」：把电脑上已有的 .minecraft 原地当实例用，不复制文件。
    """
    data = CONFIG.get("external_instances") or {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items() if k and v}


def is_external(name) -> bool:
    return str(name) in external_instances()


def _save_external(registry: dict):
    CONFIG.set("external_instances", dict(registry))
    CONFIG.save()


def link_external_instance(name, path) -> str:
    """把已有游戏目录注册为实例，原地使用。返回最终实例名。"""
    name = sanitize_instance_name(name, fallback="外部目录")
    target = Path(path).expanduser()
    try:
        target = target.resolve()
    except OSError as exc:
        raise InstanceError(f"无法访问目录: {exc}")
    if not target.is_dir():
        raise InstanceError(f"目录不存在: {target}")
    root = CONFIG.instances_dir.resolve()
    if target == root or root in target.parents:
        raise InstanceError("该目录已在启动器的实例目录里，不需要外部接入。")
    registry = external_instances()
    if name in registry or name in list_instances() or (root / name).exists():
        raise InstanceError(f"实例 {name} 已存在。")
    for other, p in registry.items():
        if Path(p) == target:
            raise InstanceError(f"该目录已注册为实例「{other}」。")
    registry[name] = str(target)
    _save_external(registry)
    return name


def unlink_external_instance(name):
    """解除外部目录注册。只删引用，绝不动用户的文件夹。"""
    registry = external_instances()
    if str(name) not in registry:
        raise InstanceError(f"外部实例不存在: {name}")
    registry.pop(str(name))
    _save_external(registry)
    if CONFIG.get("default_instance") == name:
        names = list_instances()
        CONFIG.set("default_instance", names[0] if names else "default")
        CONFIG.save()


def list_instances() -> list:
    """返回所有实例名（含已注册且目录仍存在的外部游戏目录）。"""
    root = CONFIG.instances_dir
    names = []
    if root.is_dir():
        for child in root.iterdir():
            if child.is_dir() and (child / INSTANCE_META).is_file():
                names.append(child.name)
    for name, path in external_instances().items():
        if name not in names and Path(path).is_dir():
            names.append(name)
    return sorted(names)


def sanitize_instance_name(raw, fallback="游戏") -> str:
    """去掉 Windows 非法字符，得到可用的实例名。"""
    s = _ILLEGAL_NAME.sub("-", str(raw or "").strip())
    s = re.sub(r"\s+", " ", s).strip(" .")
    if not s:
        s = fallback
    if s.upper() in _WIN_RESERVED:
        s = f"{s}-游戏"
    if len(s) > _MAX_INSTANCE_NAME:
        s = s[:_MAX_INSTANCE_NAME].rstrip(" .")
    if not s:
        s = fallback
    return s


def unique_instance_name(raw, fallback="游戏") -> str:
    """在已有实例名上自动加 -2、-3，避免覆盖。"""
    base = sanitize_instance_name(raw, fallback)
    existing = set(list_instances())
    root = CONFIG.instances_dir
    name = base
    n = 2
    while name in existing or (root / name).exists():
        suffix = f"-{n}"
        trimmed = base
        limit = _MAX_INSTANCE_NAME - len(suffix)
        if len(trimmed) > limit:
            trimmed = trimmed[:limit].rstrip(" .") or fallback
        name = f"{trimmed}{suffix}"
        n += 1
    return name


def get_instance_path(name) -> Path:
    root = CONFIG.instances_dir.resolve()
    if not name or name in (".", "..") or not re.fullmatch(r"[^\\/:*?\"<>|]+", name):
        raise InstanceError(f"非法实例名: {name!r}")
    ext = external_instances().get(str(name))
    if ext:
        return Path(ext)
    path = (root / name).resolve()
    # 防路径穿越：实例必须直接位于实例目录之下
    if path.parent != root:
        raise InstanceError(f"非法实例名: {name!r}")
    return path


class Instance:
    def __init__(self, name=None):
        if name is None:
            name = CONFIG.get("default_instance", "default")
        self.name = name
        self.path = get_instance_path(name)

    # ---- 创建 / 删除 / 重命名
    def create(self, meta=None):
        if self.path.is_dir():
            raise InstanceError(f"实例 {self.name} 已存在。")
        utils.ensure_dir(self.path)
        for d in _STANDARD_DIRS:
            utils.ensure_dir(self.path / d)
        data = {"name": self.name, "mc_version": None, "modpack": None, "java": JAVA_AUTO, **({} if not meta else meta)}
        utils.write_json(self.path / INSTANCE_META, data)

    def delete(self):
        if is_external(self.name):
            # 外部目录是用户自己的 .minecraft：只解除注册，绝不删文件
            unlink_external_instance(self.name)
            return
        if not self.path.is_dir():
            raise InstanceError(f"实例 {self.name} 不存在。")
        utils.remove_tree(self.path)
        if CONFIG.get("default_instance") == self.name:
            names = list_instances()
            CONFIG.set("default_instance", names[0] if names else "default")
            CONFIG.save()

    def rename(self, new_name):
        if is_external(self.name):
            # 外部目录只改注册名，不动文件夹本身
            if not re.fullmatch(r"[^\\/:*?\"<>|]+", str(new_name or "")):
                raise InstanceError(f"非法实例名: {new_name!r}")
            registry = external_instances()
            if (new_name in registry or new_name in list_instances()
                    or (CONFIG.instances_dir / new_name).exists()):
                raise InstanceError(f"实例 {new_name} 已存在。")
            registry[str(new_name)] = registry.pop(self.name)
            _save_external(registry)
            if CONFIG.get("default_instance") == self.name:
                CONFIG.set("default_instance", new_name)
                CONFIG.save()
            self.name = new_name
            return
        new_path = get_instance_path(new_name)
        if new_path.exists():
            raise InstanceError(f"实例 {new_name} 已存在。")
        self.path.rename(new_path)
        if CONFIG.get("default_instance") == self.name:
            CONFIG.set("default_instance", new_name)
            CONFIG.save()
        self.name = new_name
        self.path = new_path
        self.set_meta("name", new_name)

    def meta(self):
        return utils.read_json(self.path / INSTANCE_META, {}) or {}

    def set_meta(self, key, value):
        data = self.meta()
        data[key] = value
        utils.write_json(self.path / INSTANCE_META, data)

    def java_pref(self) -> str:
        """该实例指定的 Java：自动选择，或 java.exe 路径 / 显示名。"""
        v = (self.meta() or {}).get("java")
        if v is None or str(v).strip() in ("", JAVA_AUTO, "auto", "default"):
            return JAVA_AUTO
        return str(v).strip()

    def set_java_pref(self, value):
        v = (value or "").strip() or JAVA_AUTO
        if v in ("auto", "default"):
            v = JAVA_AUTO
        self.set_meta("java", v)

    # ---- 路径
    def versions_dir(self):
        return self.path / "versions"

    def libraries_dir(self):
        return CONFIG.libraries_dir(self.path)

    def assets_dir(self):
        return CONFIG.assets_dir(self.path)

    def natives_dir(self, version_id, version_json=None):
        """旧版本（<1.6）natives 放在 bin/natives，其余放版本目录下。"""
        from .manifest import is_legacy_version
        if version_json and is_legacy_version(version_json):
            return self.path / "bin" / "natives"
        return self.versions_dir() / version_id / f"{version_id}-natives"

    def ensure_standard_dirs(self):
        for d in _STANDARD_DIRS:
            utils.ensure_dir(self.path / d)

    # ---- 已安装版本
    def installed_versions(self):
        """返回 [(版本id, version json路径), ...]"""
        vdir = self.versions_dir()
        result = []
        if not vdir.is_dir():
            return result
        for child in sorted(vdir.iterdir()):
            if not child.is_dir():
                continue
            jfile = child / f"{child.name}.json"
            if jfile.is_file():
                result.append((child.name, jfile))
        return result

    def installed_ids(self):
        return [vid for vid, _ in self.installed_versions()]

    def version_json(self, version_id):
        jfile = self.versions_dir() / version_id / f"{version_id}.json"
        return utils.read_json(jfile, None)

    def has_version(self, version_id) -> bool:
        return (self.versions_dir() / version_id / f"{version_id}.json").is_file()


def create_unique_instance(raw, fallback="游戏", meta=None) -> Instance:
    """按版本/整合包名称新建一个空实例。"""
    inst = Instance(unique_instance_name(raw, fallback))
    inst.create(meta=meta)
    return inst


# 复制实例时跳过的顶层运行残留（HMCL 复制实例也不带日志）
_SKIP_COPY_DIRS = {"logs", "crash-reports"}


def duplicate_instance(name, new_name="", on_progress=None) -> str:
    """复制实例为一个新实例（对齐 HMCL「复制实例」/ PCL2「复制版本」）。

    整个实例目录原样复制：版本、模组、存档、配置全带走，跳过
    logs / crash-reports 运行残留。外部游戏目录也能复制——得到一份
    托管副本，原目录不动。on_progress(已复制字节, 总字节)。
    返回新实例名（重名自动加 -2/-3）。
    """
    import os
    import shutil

    src = Instance(name).path
    if not src.is_dir():
        raise InstanceError(f"实例 {name} 不存在。")
    new_name = unique_instance_name(new_name or f"{name}-副本", fallback="副本")
    dst = CONFIG.instances_dir / new_name
    if dst.exists():
        raise InstanceError(f"实例 {new_name} 已存在。")
    instances_root = CONFIG.instances_dir.resolve()

    # 一次遍历收集目录/文件/符号链接，顺便统计总字节数用于进度
    dirs: list[Path] = []
    files: list[tuple[Path, Path, int]] = []
    links: list[tuple[Path, Path]] = []
    total = 0
    for dirpath, dirnames, filenames in os.walk(src, followlinks=False):
        cur = Path(dirpath)
        rel = cur.relative_to(src)
        if cur == src:
            dirnames[:] = [d for d in dirnames if d not in _SKIP_COPY_DIRS]
        keep = []
        for d in dirnames:
            child = cur / d
            # 外部目录可能是实例根的祖先：绝不把实例目录复制进自己
            try:
                if child.resolve() == instances_root:
                    continue
            except OSError:
                continue
            # 符号链接目录原样重建为链接，不追进去（防环、防复制半个磁盘）
            if child.is_symlink():
                links.append((child, rel / d))
            else:
                keep.append(d)
        dirnames[:] = keep
        dirs.append(rel)
        for fn in filenames:
            p = cur / fn
            n = 0
            if not p.is_symlink():
                try:
                    n = p.stat().st_size
                except OSError:
                    pass
            files.append((p, rel / fn, n))
            total += n

    done = 0
    try:
        for rel in dirs:
            utils.ensure_dir(dst / rel)
        for srcf, rel, n in files:
            target = dst / rel
            if srcf.is_symlink():
                try:
                    os.symlink(os.readlink(srcf), target)
                except OSError:
                    try:
                        shutil.copy2(srcf, target)
                    except OSError:
                        pass  # 坏链接 / 无权限建链：跳过，不拖垮整个复制
            else:
                shutil.copy2(srcf, target)
            done += n
            if on_progress and total:
                on_progress(done, total)
        for linkp, rel in links:
            try:
                os.symlink(os.readlink(linkp), dst / rel,
                           target_is_directory=True)
            except OSError:
                pass
    except BaseException:
        utils.remove_tree(dst)
        raise

    meta = utils.read_json(dst / INSTANCE_META, {}) or {}
    meta["name"] = new_name
    meta.setdefault("java", JAVA_AUTO)
    utils.write_json(dst / INSTANCE_META, meta)
    Instance(new_name).ensure_standard_dirs()
    return new_name
