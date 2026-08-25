# -*- coding: utf-8 -*-
"""实例（版本隔离）管理。每个实例是一个独立的 .minecraft 目录。"""
import re
import shutil
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


def list_instances() -> list:
    """返回所有实例名。"""
    root = CONFIG.instances_dir
    if not root.is_dir():
        return []
    names = []
    for child in root.iterdir():
        if child.is_dir() and (child / INSTANCE_META).is_file():
            names.append(child.name)
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
        if not self.path.is_dir():
            raise InstanceError(f"实例 {self.name} 不存在。")
        utils.remove_tree(self.path)
        if CONFIG.get("default_instance") == self.name:
            names = list_instances()
            CONFIG.set("default_instance", names[0] if names else "default")
            CONFIG.save()

    def rename(self, new_name):
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


# 复制实例时跳过的顶层目录：运行垃圾，副本里不需要。
_DUPLICATE_SKIP = frozenset(("logs", "crash-reports"))


def duplicate_instance(src_name, new_name="", on_progress=None) -> str:
    """复制整个实例（版本、mods、config、存档、资源包等）。

    对标 HMCL 的「复制实例」/ PCL2 隔离版本复制：给整合包实例留试验
    副本、升级前留退路。logs 与 crash-reports 是运行垃圾不带。
    new_name 留空自动用「原名-副本」，重名自动加序号。返回新实例名。
    """
    src = Instance(src_name)
    if not src.path.is_dir():
        raise InstanceError(f"实例 {src_name} 不存在。")
    base = str(new_name or "").strip() or f"{src_name}-副本"
    dest_name = unique_instance_name(base, fallback=f"{src_name}-2")
    dest = get_instance_path(dest_name)

    files = []
    for item in src.path.iterdir():
        if item.name in _DUPLICATE_SKIP:
            continue
        if item.is_file():
            files.append(item)
        elif item.is_dir():
            files.extend(p for p in item.rglob("*") if p.is_file())
    total = len(files)

    utils.ensure_dir(dest)
    try:
        for i, p in enumerate(files):
            target = dest / p.relative_to(src.path)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, target)
            if on_progress and (i % 50 == 0 or i == total - 1):
                on_progress(i + 1, total)
        inst = Instance(dest_name)
        inst.ensure_standard_dirs()
        inst.set_meta("name", dest_name)
        return dest_name
    except Exception:
        utils.remove_tree(dest)
        raise
