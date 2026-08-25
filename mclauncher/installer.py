# -*- coding: utf-8 -*-
"""版本安装：原版 / Fabric / Quilt / Forge / NeoForge。

安装内容：
- version JSON、客户端 jar、依赖库（含 natives 解压）
- 资源文件（assets，支持虚拟索引与 map_to_resources）
- 日志配置
- Forge 处理器（processors）
"""
import json
import os
import re
import shutil
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from . import manifest, utils
from .downloader import DownloadManager, DownloadError
from .instances import Instance


class InstallError(Exception):
    pass


FABRIC_META = "https://meta.fabricmc.net/v2"
QUILT_META = "https://meta.quiltmc.org/v3"
FORGE_MAVEN = "https://maven.minecraftforge.net/net/minecraftforge/forge"
BMCLAPI = "https://bmclapi2.bangbang93.com"
FORGE_MAVEN_MIRRORS = [
    FORGE_MAVEN,
    f"{BMCLAPI}/maven/net/minecraftforge/forge",
]
NEOFORGE_MAVEN = "https://maven.neoforged.net/releases/net/neoforged/neoforge"
MOJANG_LIBRARIES = "https://libraries.minecraft.net/"


def match_forge_artifact(candidates, mc_version, forge_version):
    """把整合包里的 forge-10.13.4.1614 解析成 Maven 构件号。

    1.7.10 官方构件是 ``1.7.10-10.13.4.1614-1.7.10``，不是 ``1.7.10-10.13.4.1614``。
    """
    if not forge_version:
        return None
    if forge_version in candidates:
        return forge_version
    combined = f"{mc_version}-{forge_version}"
    universal = f"{combined}-{mc_version}"
    if universal in candidates:
        return universal
    if combined in candidates:
        return combined
    matched = [v for v in candidates if forge_version in v.split("-")]
    if not matched:
        matched = [v for v in candidates if forge_version in v]
    if not matched:
        return None
    preferred = [v for v in matched if v.startswith(str(mc_version) + "-")]
    pool = preferred or matched
    return sorted(pool, key=lambda v: (len(v), v))[-1]


def forge_artifact_guesses(mc_version, forge_version):
    """整合包只给 43.5.1 时，猜 Maven 构件号，不必先查完整版本列表。"""
    mc = (mc_version or "").strip()
    fv = (forge_version or "").strip()
    if fv.lower().startswith("forge-"):
        fv = fv[6:]
    out = []

    def add(item):
        if item and item not in out:
            out.append(item)

    if not mc or not fv:
        return out
    if fv == mc or fv.startswith(mc + "-"):
        add(fv)
        return out
    add(f"{mc}-{fv}")
    add(f"{mc}-{fv}-{mc}")
    return out


def split_forge_artifact(full, mc_version=None):
    """1.19.2-43.5.1 / 1.7.10-10.13.4.1614-1.7.10 -> (mc, forge, branch)。"""
    full = (full or "").strip()
    mc = (mc_version or "").strip()
    rest = full
    if mc and full.startswith(mc + "-"):
        rest = full[len(mc) + 1:]
    else:
        parts = full.split("-", 1)
        if len(parts) == 2 and re.match(r"^\d+\.\d+", parts[0]):
            mc, rest = parts[0], parts[1]
    branch = None
    ver = rest
    if mc and rest.endswith("-" + mc) and rest[: -(len(mc) + 1)]:
        ver = rest[: -(len(mc) + 1)]
        branch = mc
    return mc, ver, branch


def parse_maven_versions(xml):
    if not xml:
        return []
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return []
    versions = []
    for el in root.iter():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag == "version" and (el.text or "").strip():
            versions.append(el.text.strip())
    return versions


def bmcl_forge_artifacts(data, mc_version):
    mc = (mc_version or "").strip()
    out = []
    if not isinstance(data, list):
        return out
    for item in data:
        if not isinstance(item, dict):
            continue
        ver = str(item.get("version") or "").strip()
        item_mc = str(item.get("mcversion") or mc).strip()
        branch = str(item.get("branch") or "").strip()
        if not ver:
            continue
        if item_mc and (ver == item_mc or ver.startswith(item_mc + "-")):
            art = ver
        elif branch:
            art = f"{item_mc}-{ver}-{branch}"
        else:
            art = f"{item_mc}-{ver}"
        if art not in out:
            out.append(art)
    return out


def forge_sort_key(full, mc_version):
    _mc, ver, branch = split_forge_artifact(full, mc_version)
    nums = []
    for part in re.split(r"[^\d]+", ver or ""):
        if part.isdigit():
            nums.append(int(part))
    return (tuple(nums), 1 if branch else 0, len(full or ""))


def is_forge_installer_jar(path):
    p = Path(path)
    if not p.is_file() or p.stat().st_size < 1024:
        return False
    try:
        with zipfile.ZipFile(p) as zf:
            names = set(zf.namelist())
        return "install_profile.json" in names or "version.json" in names
    except zipfile.BadZipFile:
        return False


def _library_base_url(lib):
    url = (lib.get("url") or MOJANG_LIBRARIES).rstrip("/") + "/"
    if url.startswith("http://files.minecraftforge.net/maven"):
        return "https://maven.minecraftforge.net/"
    return url


def _lib_sha1(lib):
    for h in (lib.get("sha1"),):
        if isinstance(h, str) and len(h) == 40 and all(c in "0123456789abcdefABCDEF" for c in h):
            return h
    art = (lib.get("downloads") or {}).get("artifact") or {}
    h = art.get("sha1")
    if isinstance(h, str) and len(h) == 40 and all(c in "0123456789abcdefABCDEF" for c in h):
        return h
    for h in lib.get("checksums") or []:
        if isinstance(h, str) and len(h) == 40 and all(c in "0123456789abcdefABCDEF" for c in h):
            return h
    return None


def _lib_size(lib):
    for raw in (lib.get("size"), ((lib.get("downloads") or {}).get("artifact") or {}).get("size")):
        if raw is None:
            continue
        try:
            return int(raw)
        except (TypeError, ValueError):
            continue
    return None


def native_arch_token():
    """1.7.10 natives 分类器里的 ${arch} 是 32/64，不是 x86/x64。"""
    return "32" if utils.ARCH == "x86" else "64"


def _maven_relpath(spec: str) -> str:
    """Maven 坐标（可带 [brackets] 与 @ext）-> 仓库相对路径。"""
    s = (spec or "").strip()
    if s.startswith("[") and s.endswith("]"):
        s = s[1:-1]
    ext = "jar"
    if "@" in s:
        s, ext = s.rsplit("@", 1)
    return utils.maven_artifact_path(s, suffix=ext)


def subst_native_key(key):
    if not key:
        return key
    return key.replace("${arch}", native_arch_token())


def select_native_classifier(lib):
    """选出当前平台的 natives 分类器名（已替换 ${arch}）。"""
    classifiers = (lib.get("downloads") or {}).get("classifiers") or {}
    natives_map = lib.get("natives") or {}
    wanted = subst_native_key(natives_map.get(utils.OS_NAME))

    if wanted:
        if wanted in classifiers:
            return wanted
        for k in classifiers:
            if subst_native_key(k) == wanted:
                return subst_native_key(k)
        if not classifiers:
            return wanted

    keys = []
    for k in classifiers:
        sk = subst_native_key(k)
        if sk.startswith("natives-"):
            keys.append(sk)
    if not keys:
        return wanted

    alias = {"osx": ("osx", "macos"), "windows": ("windows",), "linux": ("linux",)}[utils.OS_NAME]
    arch = utils.ARCH
    bits = native_arch_token()

    def score(k):
        rest = k[len("natives-"):] if k.startswith("natives-") else k
        s = 0
        if any(rest == a or rest.startswith(a + "-") for a in alias):
            s += 2
        if rest.endswith("-" + bits) or rest.endswith("-" + arch) or arch in rest.split("-"):
            s += 3
        elif rest.endswith("-32") or rest.endswith("-64") or rest.endswith("-x86"):
            s -= 2
        return s

    keys.sort(key=score, reverse=True)
    if score(keys[0]) <= 0 and wanted:
        return wanted
    return keys[0]


def natives_jar_relpath(lib, nkey):
    downloads = lib.get("downloads") or {}
    classifiers = downloads.get("classifiers") or {}
    entry = classifiers.get(nkey)
    if not entry:
        for k, v in classifiers.items():
            if subst_native_key(k) == nkey:
                entry = v
                break
    entry = entry or {}
    name = lib.get("name") or ""
    return entry.get("path") or utils.maven_artifact_path(f"{name}:{nkey}")


def natives_present(natives_dir) -> bool:
    natives_dir = Path(natives_dir)
    if not natives_dir.is_dir():
        return False
    for p in natives_dir.iterdir():
        if not p.is_file():
            continue
        name = p.name.lower()
        if name.startswith("lwjgl") or name.startswith("liblwjgl"):
            return True
    return False


def extract_natives(instance, resolved, version_id):
    """把 resolved libraries 里的 natives 解压到版本 natives 目录。

    旧版 Forge inheritsFrom 1.7.10 时，安装器只处理了 Forge 自己的库列表，
    启动却把 java.library.path 指到空的 Forge natives 目录，LWJGL 会
    UnsatisfiedLinkError: no lwjgl in java.library.path。
    """
    natives_dir = instance.natives_dir(version_id, resolved)
    utils.ensure_dir(natives_dir)
    libs_dir = instance.libraries_dir()
    extracted = 0
    for lib in resolved.get("libraries") or []:
        if lib.get("clientreq") is False:
            continue
        if not utils.check_rules(lib.get("rules")):
            continue
        nkey = select_native_classifier(lib)
        if not nkey:
            continue
        jarpath = libs_dir / natives_jar_relpath(lib, nkey)
        if not jarpath.is_file():
            utils.log.warning("缺少 natives jar: %s", jarpath)
            continue
        exclude = (lib.get("extract") or {}).get("exclude") or []
        try:
            DownloadManager.extract_jar_natives(jarpath, natives_dir, exclude=exclude)
            extracted += 1
        except Exception as e:
            utils.log.warning("解压 natives 失败 %s: %s", jarpath, e)

    if extracted and natives_present(natives_dir):
        return natives_dir

    parent_id = resolved.get("inheritsFrom") or resolved.get("jar")
    if parent_id and parent_id != version_id:
        parent_json = instance.version_json(parent_id)
        if parent_json:
            src = instance.natives_dir(parent_id, parent_json)
            if natives_present(src) and src.resolve() != natives_dir.resolve():
                for f in src.iterdir():
                    if f.is_file():
                        shutil.copy2(f, natives_dir / f.name)
                utils.log.info("已从父版本 %s 复制 natives 到 %s", parent_id, natives_dir)
    return natives_dir

NEOFORGE_MC_MAP = {
    "1.20.1": "47.1", "1.20.2": "20.2", "1.20.3": "20.3", "1.20.4": "20.4",
    "1.20.5": "20.5", "1.20.6": "20.6", "1.21": "21.0", "1.21.1": "21.1",
}


class Installer:
    def __init__(self, instance: Instance, dm: DownloadManager = None,
                 on_progress=None, cancel=None):
        self.instance = instance
        self.dm = dm or DownloadManager(on_progress=on_progress, cancel=cancel)
        self.on_progress = on_progress
        self.cancel = cancel or (lambda: False)
        self._java_cache = None
        self.skip_assets = False

    def _note(self, msg, done=0, total=1):
        msg = str(msg or "").strip()
        if not msg:
            return
        utils.log.info("%s", msg)
        if self.on_progress:
            self.on_progress(msg, done, total)

    def _proc_line(self, line, done=0, total=1, prefix="Forge 处理器"):
        safe = (line or "").encode("ascii", "replace").decode().strip()
        if not safe:
            return
        # BinaryPatcher 会刷几万行 Patching，界面日志只保留关键步骤
        if safe.startswith("Patching ") or safe.startswith("Processing "):
            return
        if self.on_progress:
            self.on_progress(f"{prefix}: {safe[:240]}", done, total)

    # ================================================================ 原版

    def install_version(self, version_id, force=False, java=None):
        """安装任意版本（原版 / Fabric / Quilt / 带处理器的 Forge JSON）。"""
        inst = self.instance
        inst.ensure_standard_dirs()
        try:
            vjson = manifest.get_version_json(self.dm, version_id, force=force)
        except manifest.VersionNotFound:
            local = inst.version_json(version_id)
            if local:
                vjson = local
            else:
                alt = manifest.resolve_playable_version(self.dm, version_id)
                if alt and alt != version_id:
                    if self.on_progress:
                        self.on_progress(f"版本 {version_id} 不存在，自动改用 {alt}", 0, 1)
                    utils.log.info("版本 %s 不存在，自动改用 %s", version_id, alt)
                    return self.install_version(alt, force=force, java=java)
                raise
        self._install_json(version_id, vjson, force=force, java=java)

    def _install_json(self, version_id, vjson, force=False, java=None):
        inst = self.instance
        vdir = inst.versions_dir() / version_id
        utils.ensure_dir(vdir)
        # 写入实例前去掉我们自己的缓存时间戳字段，保持官方 JSON 格式干净
        clean = {k: v for k, v in vjson.items() if k != "__pymcl_cached_at"}
        utils.write_json(vdir / f"{version_id}.json", clean)

        # 递归安装父版本（如 Fabric -> 原版）
        if vjson.get("inheritsFrom"):
            pid = vjson["inheritsFrom"]
            if not inst.has_version(pid):
                if self.on_progress:
                    self.on_progress(f"安装依赖版本 {pid}", 0, 1)
                parent_json = self._load_parent_json(pid)
                self._install_json(pid, parent_json, force=force, java=java)

        resolved = manifest.resolve_inherits(vjson, self._load_parent_json)

        # 客户端 jar。OptiFine / LiteLoader 复用原版 jar，不重复下 50MB。
        own_client = ((vjson.get("downloads") or {}).get("client") or {}).get("url")
        reuse = vjson.get("jar") or (None if own_client else vjson.get("inheritsFrom"))
        reuse_jar = inst.versions_dir() / reuse / f"{reuse}.jar" if reuse else None
        client = (resolved.get("downloads") or {}).get("client")
        dest_jar = vdir / f"{version_id}.jar"
        if reuse_jar and reuse_jar.is_file():
            if self.on_progress:
                self.on_progress(f"复用客户端 jar {reuse}", 1, 1)
        elif client and client.get("url"):
            if self.on_progress:
                self.on_progress(f"下载客户端 jar {version_id}", 0, 1)
            self.dm.download(client["url"], dest_jar,
                             sha1=client.get("sha1"), size=client.get("size"), force=force)
        else:
            raise InstallError(f"版本 {version_id} 缺少客户端 jar 下载信息")

        # 依赖库 + natives
        self._install_libraries(resolved, version_id, force=force)

        # Forge 处理器
        if vjson.get("processors"):
            vanilla_jar = self._vanilla_jar_for(vjson, resolved)
            self._run_processors(vjson, resolved, vdir, vanilla_jar, force=force, java=java)

        # 资源文件
        if getattr(self, "skip_assets", False):
            self._note("已跳过资源文件校验")
        else:
            self._install_assets(resolved, force=force)

        # 日志配置
        self._install_logging(resolved)

        inst.set_meta("mc_version", version_id)
        utils.log.info("版本 %s 安装完成 -> %s", version_id, vdir)

    def _load_parent_json(self, parent_id):
        """优先从实例本地读取父版本 JSON，否则从远程拉取。"""
        local = self.instance.version_json(parent_id)
        if local:
            return local
        try:
            return manifest.get_version_json(self.dm, parent_id)
        except manifest.VersionNotFound:
            return None

    def _vanilla_jar_for(self, vjson, resolved):
        """处理器需要用到被继承的原版 jar 路径。"""
        pid = vjson.get("inheritsFrom")
        if pid:
            p = self.instance.versions_dir() / pid / f"{pid}.jar"
            if p.is_file():
                return p
        return self.instance.versions_dir() / resolved.get("id") / f"{resolved.get('id')}.jar"

    # ---------------------------------------------------------------- 库

    def _select_native_classifier(self, lib) -> str:
        return select_native_classifier(lib)

    def _install_libraries(self, resolved, version_id, force=False):
        inst = self.instance
        libs_dir = inst.libraries_dir()
        natives_dir = inst.natives_dir(version_id, resolved)
        utils.ensure_dir(natives_dir)

        tasks = []
        natives_tasks = []  # [(jar_path_in_libs, native_classifier, exclude)]
        for lib in resolved.get("libraries", []):
            if lib.get("clientreq") is False:
                continue
            if not utils.check_rules(lib.get("rules")):
                continue
            name = lib.get("name")
            if not name:
                continue
            downloads = lib.get("downloads") or {}

            # 主 artifact
            artifact = downloads.get("artifact")
            if artifact and artifact.get("url"):
                path = artifact.get("path") or utils.maven_artifact_path(name)
                dest = libs_dir / path
                if force or not utils.file_matches(dest, artifact.get("sha1"), artifact.get("size")):
                    tasks.append((artifact["url"], dest, artifact.get("sha1"), artifact.get("size")))
            elif not downloads and not lib.get("natives"):
                # 旧版 Forge：url 缺省则走 libraries.minecraft.net（LaunchWrapper 等）
                path = utils.maven_artifact_path(name)
                dest = libs_dir / path
                sha1, sz = _lib_sha1(lib), _lib_size(lib)
                if force or not utils.file_matches(dest, sha1, sz):
                    tasks.append((_library_base_url(lib) + path, dest, sha1, sz))
            elif not downloads.get("classifiers"):
                utils.log.warning("库 %s 没有可下载的 artifact，跳过", name)

            # natives（旧 JSON 可能没有 downloads.classifiers.url，默认走 libraries.minecraft.net）
            nkey = self._select_native_classifier(lib)
            if nkey:
                classifiers = downloads.get("classifiers") or {}
                entry = classifiers.get(nkey) or {}
                if not entry:
                    for k, v in classifiers.items():
                        if subst_native_key(k) == nkey:
                            entry = v or {}
                            break
                path = entry.get("path") or utils.maven_artifact_path(name + ":" + nkey)
                jarpath = libs_dir / path
                url = entry.get("url") or (_library_base_url(lib) + path.replace("\\", "/"))
                if force or not utils.file_matches(jarpath, entry.get("sha1"), entry.get("size")):
                    tasks.append((url, jarpath, entry.get("sha1"), entry.get("size")))
                natives_tasks.append((jarpath, lib.get("extract") or {}))

        if tasks:
            self.dm.download_all(tasks, message="下载依赖库")
        if natives_tasks:
            if self.on_progress:
                self.on_progress("解压 natives", 0, len(natives_tasks))
            for i, (jarpath, extract) in enumerate(natives_tasks):
                if self.cancel():
                    raise InstallError("用户取消")
                try:
                    self.dm.extract_jar_natives(jarpath, natives_dir,
                                                exclude=extract.get("exclude") or [])
                except Exception as e:
                    raise InstallError(f"解压 natives 失败 {jarpath.name}: {e}") from e
                if self.on_progress:
                    self.on_progress("解压 natives", i + 1, len(natives_tasks))

    # ---------------------------------------------------------------- 处理器

    def _run_processors(self, child_json, resolved, vdir, vanilla_jar, force=False, java=None):
        """运行 Forge 1.13+ 的 version JSON 处理器（官方启动器同款流程）。"""
        if not (child_json.get("processors")):
            return
        java_exe = java or self._ensure_any_java()
        libs_dir = self.instance.libraries_dir()
        libs = resolved.get("libraries", [])

        def find_lib(maven_name):
            for l in libs:
                if l.get("name") == maven_name:
                    return l
            return None

        def lib_path(maven_name):
            l = find_lib(maven_name)
            if not l:
                raise InstallError(f"找不到处理器依赖库: {maven_name}")
            downloads = l.get("downloads") or {}
            artifact = downloads.get("artifact") or {}
            path = artifact.get("path") or utils.maven_artifact_path(maven_name)
            p = libs_dir / path
            if not p.is_file():
                raise InstallError(f"处理器依赖库未下载: {path}")
            return p

        total = len(child_json["processors"])
        for i, proc in enumerate(child_json["processors"]):
            if "client" not in proc.get("sides", ["client"]):
                continue
            data = {
                "SIDE": "client",
                "MINECRAFT_JAR": str(vanilla_jar),
                "ROOT": str(vdir),
                "LIBRARY_DIR": str(libs_dir),
            }
            # Forge 处理器参数使用单花括号占位符（{SIDE}、{MINECRAFT_JAR}、{ROOT}…）
            proc_re = re.compile(r"\{([A-Za-z_]+)\}")

            def sub(s):
                def _rep(m):
                    key = m.group(1)
                    return str(data[key]) if key in data else m.group(0)
                return proc_re.sub(_rep, s)

            # 输出已存在则跳过
            outputs = {sub(k): v for k, v in (proc.get("outputs") or {}).items()}
            if outputs and not force:
                if all(utils.file_matches(Path(p), sha1=h or None) for p, h in outputs.items()):
                    continue

            jar_path = lib_path(proc["jar"])
            cp = [str(jar_path)] + [str(lib_path(n)) for n in (proc.get("classpath") or [])]
            main_class = self._jar_main_class(jar_path)
            if not main_class:
                raise InstallError(f"处理器 jar 没有 Main-Class: {proc['jar']}")
            args = []
            for a in (proc.get("args") or []):
                a = sub(a)
                if re.search(r"\{[A-Za-z_$][^{}]*\}", a):
                    utils.log.warning("处理器参数含未知占位符，跳过该参数: %s", a)
                    continue
                args.append(a)
            if self.on_progress:
                self.on_progress(f"运行 Forge 处理器 {i + 1}/{total}: {main_class}", i, total)
            proc_lines = []

            def on_line(line, _i=i):
                proc_lines.append(line)
                self._proc_line(line, _i + 1, total)

            code = utils.run_process(
                [java_exe, "-cp", os.pathsep.join(cp), main_class] + args,
                cwd=str(vdir),
                on_line=on_line,
            )
            if code != 0:
                tail = " | ".join(
                    x.encode("ascii", "replace").decode() for x in proc_lines[-6:])
                raise InstallError(
                    f"Forge 处理器运行失败 (退出码 {code}): {proc['jar']}"
                    + (f" · {tail}" if tail else "")
                )
            missing = [p for p, h in outputs.items() if not utils.file_matches(Path(p), sha1=h or None)]
            if missing:
                utils.log.warning("处理器未生成预期输出: %s", missing)

    @staticmethod
    def _jar_main_class(jar_path):
        try:
            with zipfile.ZipFile(jar_path) as zf:
                data = zf.read("META-INF/MANIFEST.MF").decode("utf-8", errors="replace")
            for line in data.splitlines():
                if line.lower().startswith("main-class:"):
                    return line.split(":", 1)[1].strip()
        except Exception as e:
            utils.log.warning("读取 %s 的 MANIFEST 失败: %s", jar_path, e)
        return None

    def _ensure_any_java(self):
        if self._java_cache:
            return self._java_cache
        from . import java as java_mod
        for j in java_mod.list_installed_javas():
            self._java_cache = j["exe"]
            return self._java_cache
        for j in java_mod.find_system_javas():
            self._java_cache = j["exe"]
            return self._java_cache
        self._java_cache = java_mod.install_adoptium(self.dm, 8, on_progress=self.on_progress)
        return self._java_cache

    # ---------------------------------------------------------------- 资源

    def _install_assets(self, resolved, force=False):
        inst = self.instance
        idx = resolved.get("assetIndex")
        if not idx or not idx.get("url"):
            return
        assets_dir = inst.assets_dir()
        index_file = assets_dir / "indexes" / f"{idx['id']}.json"
        need_index = force or not utils.file_matches(index_file, idx.get("sha1"), idx.get("size"))
        if not idx.get("sha1") and not idx.get("size"):
            need_index = force or not index_file.is_file()
        if need_index:
            if self.on_progress:
                self.on_progress(f"下载资源索引 {idx['id']}", 0, 1)
            self.dm.download(idx["url"], index_file,
                             sha1=idx.get("sha1"), size=idx.get("size"), force=True)
        index = utils.read_json(index_file, None) or {}
        objects = index.get("objects") or {}
        if not objects and (idx.get("sha1") or idx.get("url")):
            if self.on_progress:
                self.on_progress(f"资源索引无效，重新下载 {idx['id']}", 0, 1)
            self.dm.download(idx["url"], index_file,
                             sha1=idx.get("sha1"), size=idx.get("size"), force=True)
            index = utils.read_json(index_file, None) or {}
            objects = index.get("objects") or {}
        if not objects:
            raise InstallError(f"资源索引为空或损坏: {idx.get('id')}")

        tasks = []
        for name, obj in objects.items():
            h = obj.get("hash")
            if not h:
                continue
            dest = assets_dir / "objects" / h[:2] / h
            if utils.file_matches(dest, h, obj.get("size")):
                continue
            tasks.append((f"https://resources.download.minecraft.net/{h[:2]}/{h}",
                          dest, h, obj.get("size")))
        if tasks:
            self.dm.download_all(tasks, message=f"下载资源文件 {idx['id']}")

        # 虚拟索引：把对象复制成完整目录树
        if index.get("virtual"):
            vbase = assets_dir / "virtual" / idx["id"]
            self._copy_objects(objects, assets_dir, vbase)
        # 远古版本：把资源映射到游戏目录的 resources 文件夹
        if index.get("map_to_resources"):
            rbase = inst.path / "resources"
            self._copy_objects(objects, assets_dir, rbase)

    @staticmethod
    def _copy_objects(objects, assets_dir, base):
        base = utils.ensure_dir(base)
        copied = 0
        for name, obj in objects.items():
            h = obj.get("hash")
            if not h:
                continue
            src = assets_dir / "objects" / h[:2] / h
            if not src.is_file():
                continue
            dst = base / name
            if dst.is_file() and utils.sha1_file(dst) == h:
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            copied += 1
        if copied:
            utils.log.info("复制资源 %s 个 -> %s", copied, base)

    # ---------------------------------------------------------------- 日志配置

    def _install_logging(self, resolved):
        logging_client = (resolved.get("logging") or {}).get("client")
        if not logging_client:
            return
        f = logging_client.get("file") or {}
        if not f.get("url"):
            return
        dest = self.instance.assets_dir() / "log_configs" / (f.get("id") or "client")
        try:
            self.dm.download(f["url"], dest, sha1=f.get("sha1"), size=f.get("size"))
        except Exception as e:
            utils.log.warning("下载日志配置失败: %s", e)

    # ================================================================ Fabric / Quilt

    def install_fabric(self, mc_version, loader_version=None, force=False):
        url = f"{FABRIC_META}/versions/loader/{mc_version}"
        data = self.dm.fetch_json(url)
        if not data:
            raise InstallError(f"Fabric 不支持 Minecraft {mc_version}")
        if loader_version:
            chosen = next((d for d in data if d["loader"]["version"] == loader_version), None)
            if not chosen:
                raise InstallError(f"找不到 Fabric Loader {loader_version} (MC {mc_version})")
        else:
            chosen = next((d for d in data if d["loader"].get("stable")), data[0])
        loader = chosen["loader"]["version"]
        if self.on_progress:
            self.on_progress(f"下载 Fabric Loader {loader} 版本 JSON", 0, 1)
        profile = self.dm.fetch_json(f"{FABRIC_META}/versions/loader/{mc_version}/{loader}/profile/json")
        self._install_json(profile["id"], profile, force=force)
        return profile["id"]

    def install_quilt(self, mc_version, loader_version=None, force=False):
        url = f"{QUILT_META}/versions/loader/{mc_version}"
        data = self.dm.fetch_json(url)
        if not data:
            raise InstallError(f"Quilt 不支持 Minecraft {mc_version}")
        if loader_version:
            chosen = next((d for d in data if d["loader"]["version"] == loader_version), None)
            if not chosen:
                raise InstallError(f"找不到 Quilt Loader {loader_version} (MC {mc_version})")
        else:
            chosen = data[0]
        loader = chosen["loader"]["version"]
        if self.on_progress:
            self.on_progress(f"下载 Quilt Loader {loader} 版本 JSON", 0, 1)
        profile = self.dm.fetch_json(f"{QUILT_META}/versions/loader/{mc_version}/{loader}/profile/json")
        self._install_json(profile["id"], profile, force=force)
        return profile["id"]

    # ================================================================ Forge / NeoForge

    def install_forge(self, mc_version, forge_version=None, force=False):
        mc_version = (mc_version or "").strip()
        forge_version = (forge_version or "").strip() or None
        if not mc_version:
            raise InstallError("安装 Forge 时缺少 Minecraft 版本")

        cache = utils.ROOT / "cache"
        utils.ensure_dir(cache)
        guessed = forge_artifact_guesses(mc_version, forge_version) if forge_version else []
        full = None
        installer_jar = None
        last_guess_err = None

        existing = self._find_installed_forge(mc_version, forge_version)
        if existing and not force:
            self._note(f"实例已安装 {existing}，跳过 Forge 安装")
            return existing

        if guessed:
            self._note(f"按整合包声明直装 Forge {forge_version} (Minecraft {mc_version})，不查残缺版本列表")
            self._note(f"尝试构件: {', '.join(guessed)}")
        for art in guessed:
            jar = cache / f"forge-{art}-installer.jar"
            if not force and is_forge_installer_jar(jar):
                self._note(f"使用缓存的 Forge 安装器 {art} ({jar.name})")
                full, installer_jar = art, jar
                break
            try:
                self._note(f"下载 Forge 安装器 {art}")
                self._download_forge_installer(art, mc_version, jar, force=force)
                full, installer_jar = art, jar
                break
            except (DownloadError, InstallError) as e:
                last_guess_err = e
                self._note(f"Forge 安装器 {art} 不可用: {e}")

        if installer_jar is None:
            artifacts = self._list_forge_artifacts(mc_version)
            stable = [v for v in artifacts if "-pre" not in v.lower()]
            pool = stable or artifacts
            if not pool:
                if last_guess_err:
                    raise InstallError(
                        f"无法安装 Forge {forge_version} (Minecraft {mc_version}): {last_guess_err}"
                    ) from last_guess_err
                if guessed:
                    raise InstallError(
                        f"无法下载 Forge {forge_version} 安装器（Minecraft {mc_version}，已尝试 {', '.join(guessed)}）"
                    )
                raise InstallError(f"Forge 没有支持 Minecraft {mc_version} 的版本")
            if forge_version:
                full = match_forge_artifact(pool, mc_version, forge_version)
                if not full:
                    latest = max(pool, key=lambda v: forge_sort_key(v, mc_version))
                    self._note(f"列表中没有 Forge {forge_version}，改用最新版 {latest}")
                    full = latest
                else:
                    self._note(f"版本列表命中 Forge {full}")
            else:
                full = max(pool, key=lambda v: forge_sort_key(v, mc_version))
                self._note(f"使用 Minecraft {mc_version} 最新 Forge {full}")
            installer_jar = cache / f"forge-{full}-installer.jar"
            if force or not is_forge_installer_jar(installer_jar):
                self._note(f"下载 Forge 安装器 {full}")
                self._download_forge_installer(full, mc_version, installer_jar, force=force)
            else:
                self._note(f"使用缓存的 Forge 安装器 {full}")

        return self._run_forge_installer(installer_jar, mc_version, full, force=force)

    def _find_installed_forge(self, mc_version, forge_version):
        """实例里已经有对应 Forge 版本时直接复用，避免再查 Maven 列表。"""
        vroot = self.instance.versions_dir()
        if not vroot.is_dir():
            return None
        fv = (forge_version or "").strip()
        if fv.lower().startswith("forge-"):
            fv = fv[6:]
        names = []
        if fv:
            if fv.startswith(mc_version + "-"):
                names.append(f"{mc_version}-forge-{fv[len(mc_version) + 1:]}")
            names.append(f"{mc_version}-forge-{fv}")
        for n in names:
            if n and self.instance.has_version(n):
                return n
        if not fv:
            return None
        needle = fv[len(mc_version) + 1:] if fv.startswith(mc_version + "-") else fv
        for p in vroot.iterdir():
            n = p.name
            if p.is_dir() and "forge" in n.lower() and mc_version in n and needle in n:
                if self.instance.has_version(n):
                    return n
        return None

    def _download_forge_installer(self, full, mc_version, dest, force=False):
        dest = Path(dest)
        if dest.is_file() and not is_forge_installer_jar(dest):
            dest.unlink(missing_ok=True)
            force = True
        urls = self._forge_installer_urls(full, mc_version)
        self.dm.download(urls[0], dest, force=force, urls=urls)
        if not is_forge_installer_jar(dest):
            dest.unlink(missing_ok=True)
            raise InstallError(f"下载的 Forge 安装器无效: {full}")

    def _forge_installer_urls(self, full, mc_version):
        urls = [f"{base}/{full}/forge-{full}-installer.jar" for base in FORGE_MAVEN_MIRRORS]
        mc, ver, branch = split_forge_artifact(full, mc_version)
        if mc and ver:
            q = f"mcversion={mc}&version={ver}&category=installer&format=jar"
            if branch:
                q += f"&branch={branch}"
            urls.append(f"{BMCLAPI}/forge/download?{q}")
        return urls

    def _list_forge_artifacts(self, mc_version):
        found, seen = [], set()

        def add_many(items, source):
            n = 0
            for v in items or []:
                v = (v or "").strip()
                if not v or v in seen:
                    continue
                if v != mc_version and not v.startswith(mc_version + "-"):
                    continue
                seen.add(v)
                found.append(v)
                n += 1
            if n:
                self._note(f"{source}: 匹配到 {n} 个 Forge 版本")
            return n

        self._note(f"查询 Forge 版本列表 (Minecraft {mc_version})")
        try:
            data = self.dm.fetch_json(f"{BMCLAPI}/forge/minecraft/{mc_version}", timeout=60)
            mapped = bmcl_forge_artifacts(data, mc_version)
            add_many(mapped, "BMCLAPI")
        except Exception as e:
            self._note(f"BMCLAPI Forge 列表失败: {e}")

        if found:
            found.sort(key=lambda v: forge_sort_key(v, mc_version))
            self._note(f"可用 Forge 共 {len(found)} 个，最新 {found[-1]}")
            return found

        sources = [
            (f"{BMCLAPI}/maven/net/minecraftforge/forge/maven-metadata.xml", True, "BMCLAPI maven-metadata"),
            (f"{FORGE_MAVEN}/maven-metadata.xml", False, "Forge 官方 maven-metadata"),
        ]
        for url, expand, label in sources:
            try:
                xml = self.dm.fetch_text(url, timeout=90, expand=expand)
            except Exception as e:
                self._note(f"{label} 失败: {e}")
                continue
            vers = parse_maven_versions(xml)
            self._note(f"{label}: 解析到 {len(vers)} 条版本")
            if add_many(vers, label):
                break

        found.sort(key=lambda v: forge_sort_key(v, mc_version))
        if found:
            self._note(f"可用 Forge 共 {len(found)} 个，最新 {found[-1]}")
        return found

    def _run_forge_installer(self, installer_jar, mc_version, full, force=False):
        profile = self._read_forge_install_profile(installer_jar)
        if profile and profile.get("versionInfo") and not profile.get("processors"):
            self._note(f"旧版 Forge 安装流程 {full}")
            return self._install_forge_legacy(installer_jar, profile, mc_version, force=force)
        if profile and (profile.get("processors") or profile.get("json")):
            self._note(f"离线安装 Forge {full}（不调用官方安装器）")
            return self._install_forge_modern(installer_jar, profile, mc_version, force=force)

        from . import java as java_mod
        legacy = self._mc_tuple(mc_version) < (1, 17)
        java_exe = java_mod.java_for_installer("forge-legacy" if legacy else "forge",
                                               self.dm, on_progress=self.on_progress)
        self._note(f"回退官方 Forge 安装器 {full}")
        return self._install_forge_cli(installer_jar, mc_version, full, java_exe)

    def _read_forge_install_profile(self, installer_jar):
        try:
            with zipfile.ZipFile(installer_jar) as zf:
                with zf.open("install_profile.json") as f:
                    return json.loads(f.read().decode("utf-8"))
        except Exception as e:
            utils.log.warning("读取 Forge install_profile.json 失败: %s", e)
            return None

    def _install_forge_modern(self, installer_jar, profile, mc_version, force=False):
        """1.13+：解析 install_profile.json 自己下库、跑处理器，不调用官方安装器。

        官方 --installClient 在中文路径下经常直接退出码 1，且不会走国内 Maven 镜像。
        """
        from . import java as java_mod
        inst = self.instance
        installer_jar = Path(installer_jar)
        json_entry = (profile.get("json") or "/version.json").lstrip("/")
        with zipfile.ZipFile(installer_jar) as zf:
            vjson = json.loads(zf.read(json_entry).decode("utf-8"))

        vid = vjson.get("id") or profile.get("version")
        if not vid:
            raise InstallError("Forge 安装器缺少 version id")
        vanilla = profile.get("minecraft") or vjson.get("inheritsFrom") or mc_version
        self._note(f"Forge 版本 id={vid}，依赖原版 {vanilla}")
        if not inst.has_version(vanilla):
            self._note(f"安装 Minecraft {vanilla}")
            self.install_version(vanilla, force=force)
        vanilla_jar = inst.versions_dir() / vanilla / f"{vanilla}.jar"
        if not vanilla_jar.is_file():
            raise InstallError(f"缺少原版客户端 jar: {vanilla_jar}")

        libs = list(profile.get("libraries") or []) + list(vjson.get("libraries") or [])
        self._note(f"下载 Forge 依赖库（{len(libs)} 个条目）")
        self._install_libraries({"libraries": libs, "id": vid}, vid, force=force)

        tmp_data = Path(tempfile.mkdtemp(prefix="pymcl_fgdata_"))
        try:
            data = self._forge_processor_data(installer_jar, profile, vanilla_jar, tmp_data)
            self._prefetch_mojmaps(data, vanilla)
            java_exe = java_mod.java_for_installer("forge", self.dm, on_progress=self.on_progress)
            procs = [p for p in (profile.get("processors") or [])
                     if "client" in (p.get("sides") or ["client"])]
            self._note(f"开始运行 {len(procs)} 个 Forge 客户端处理器")
            self._run_install_profile_processors(profile, data, java_exe, force=force)
        finally:
            shutil.rmtree(tmp_data, ignore_errors=True)

        vdir = inst.versions_dir() / vid
        utils.ensure_dir(vdir)
        utils.write_json(vdir / f"{vid}.json", vjson)
        inst.set_meta("mc_version", vid)
        self._note(f"Forge/NeoForge {vid} 安装完成")
        return vid

    def _forge_processor_data(self, installer_jar, profile, vanilla_jar, tmp_data) -> dict:
        libs_dir = self.instance.libraries_dir()
        data = {
            "SIDE": "client",
            "MINECRAFT_JAR": str(Path(vanilla_jar).resolve()),
            "ROOT": str(self.instance.path.resolve()),
            "INSTALLER": str(Path(installer_jar).resolve()),
            "LIBRARY_DIR": str(libs_dir.resolve()),
        }
        with zipfile.ZipFile(installer_jar) as zf:
            for key, spec in (profile.get("data") or {}).items():
                raw = spec.get("client") if isinstance(spec, dict) else spec
                data[key] = self._resolve_forge_data_value(raw, libs_dir, zf, tmp_data)
        return data

    def _resolve_forge_data_value(self, raw, libs_dir, zf, tmp_data) -> str:
        if raw is None:
            return ""
        s = str(raw)
        if len(s) >= 2 and s[0] == "'" and s[-1] == "'":
            return s[1:-1]
        if s.startswith("[") and s.endswith("]"):
            path = Path(libs_dir) / _maven_relpath(s)
            path.parent.mkdir(parents=True, exist_ok=True)
            return str(path.resolve())
        if s.startswith("/"):
            inner = s.lstrip("/")
            dest = Path(tmp_data) / Path(inner).name
            dest.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(inner) as src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out)
            return str(dest.resolve())
        return s

    def _prefetch_mojmaps(self, data: dict, vanilla: str):
        dest = data.get("MOJMAPS")
        if not dest:
            return
        vjson = self.instance.version_json(vanilla)
        if not vjson:
            try:
                vjson = manifest.get_version_json(self.dm, vanilla)
            except Exception:
                return
        info = (vjson.get("downloads") or {}).get("client_mappings") or {}
        if not info.get("url"):
            return
        path = Path(dest)
        path.parent.mkdir(parents=True, exist_ok=True)
        if self.on_progress:
            self.on_progress("下载 Mojang mappings", 0, 1)
        self.dm.download(info["url"], path, sha1=info.get("sha1"), size=info.get("size"))

    def _sub_forge_arg(self, raw, data: dict) -> str:
        s = str(raw)

        def _rep(m):
            key = m.group(1)
            return str(data[key]) if key in data else m.group(0)

        s = re.sub(r"\{([A-Za-z0-9_]+)\}", _rep, s)
        if s.startswith("[") and s.endswith("]"):
            path = self.instance.libraries_dir() / _maven_relpath(s)
            path.parent.mkdir(parents=True, exist_ok=True)
            return str(path.resolve())
        return s

    def _library_file(self, maven_name: str) -> Path:
        p = self.instance.libraries_dir() / _maven_relpath(maven_name)
        if not p.is_file():
            raise InstallError(f"处理器依赖库未下载: {p}")
        return p

    def _run_install_profile_processors(self, profile, data: dict, java_exe, force=False):
        procs = [p for p in (profile.get("processors") or [])
                 if "client" in (p.get("sides") or ["client"])]
        total = len(procs)
        for i, proc in enumerate(procs):
            task = None
            raw_args = proc.get("args") or []
            if "--task" in raw_args:
                try:
                    task = raw_args[raw_args.index("--task") + 1]
                except Exception:
                    task = None
            if task == "DOWNLOAD_MOJMAPS" and Path(data.get("MOJMAPS") or "").is_file():
                self._note(f"跳过处理器 {i + 1}/{total}（mappings 已就绪）")
                continue

            outputs = {}
            for k, v in (proc.get("outputs") or {}).items():
                outputs[self._sub_forge_arg(k, data)] = self._sub_forge_arg(v, data)
            if outputs and not force:
                if all(utils.file_matches(Path(p), sha1=h or None) for p, h in outputs.items()):
                    self._note(f"跳过处理器 {i + 1}/{total}（输出已存在）")
                    continue

            jar_path = self._library_file(proc["jar"])
            cp = [str(jar_path)] + [str(self._library_file(n)) for n in (proc.get("classpath") or [])]
            main_class = self._jar_main_class(jar_path)
            if not main_class:
                raise InstallError(f"处理器 jar 没有 Main-Class: {proc['jar']}")
            args = [self._sub_forge_arg(a, data) for a in (proc.get("args") or [])]
            leftover = [a for a in args if re.search(r"\{[A-Za-z0-9_]+\}", a)]
            if leftover:
                raise InstallError(f"处理器参数含未替换占位符: {leftover}")
            self._note(f"运行 Forge 处理器 {i + 1}/{total}: {main_class}", i, total)
            lines = []

            def on_line(line, _i=i):
                lines.append(line)
                self._proc_line(line, _i + 1, total)

            code = utils.run_process(
                [java_exe, "-cp", os.pathsep.join(cp), main_class] + args,
                cwd=str(self.instance.path),
                on_line=on_line,
                timeout=1800,
            )
            if code != 0:
                tail = " | ".join((x.encode("ascii", "replace").decode() for x in lines[-6:]))
                if outputs and all(Path(p).is_file() for p in outputs):
                    utils.log.warning("处理器退出码 %s，但输出已生成，继续: %s", code, proc["jar"])
                    continue
                raise InstallError(
                    f"Forge 处理器失败 (退出码 {code}): {proc['jar']}"
                    + (f" · {tail}" if tail else "")
                )
            missing = [p for p, h in outputs.items() if not utils.file_matches(Path(p), sha1=h or None)]
            if missing:
                raise InstallError(f"处理器未生成预期输出: {missing}")

    def _install_forge_cli(self, installer_jar, mc_version, full, java_exe, label="Forge"):
        """最后手段：在纯英文临时目录跑官方安装器，再拷回实例（避开中文路径）。"""
        work = Path(tempfile.mkdtemp(prefix="pymclfg", dir=Path(tempfile.gettempdir())))
        try:
            vanilla_src = self.instance.versions_dir() / mc_version
            vanilla_dst = work / "versions" / mc_version
            if vanilla_src.is_dir():
                shutil.copytree(vanilla_src, vanilla_dst, dirs_exist_ok=True)
            if self.on_progress:
                self.on_progress(f"运行 {label} 安装器 {full}", 0, 1)
            lines = []

            def on_line(line):
                lines.append(line)
                self._proc_line(line, prefix=label)

            code = utils.run_process(
                [java_exe, "-jar", str(installer_jar), "--installClient", str(work)],
                cwd=str(work),
                on_line=on_line,
                timeout=1800,
            )
            if code != 0:
                tail = " | ".join(x.encode("ascii", "replace").decode() for x in lines[-8:])
                raise InstallError(f"{label} 安装器退出码 {code}" + (f": {tail}" if tail else ""))
            versions_src = work / "versions"
            if versions_src.is_dir():
                for child in versions_src.iterdir():
                    if child.is_dir():
                        dest = self.instance.versions_dir() / child.name
                        if dest.exists():
                            shutil.copytree(child, dest, dirs_exist_ok=True)
                        else:
                            shutil.copytree(child, dest)
            libs_src = work / "libraries"
            if libs_src.is_dir():
                dest_libs = self.instance.libraries_dir()
                for src in libs_src.rglob("*"):
                    if src.is_file():
                        dst = dest_libs / src.relative_to(libs_src)
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        if not dst.is_file():
                            shutil.copy2(src, dst)
            self._merge_libraries_to_shared()
            found = [i for i in self.instance.installed_ids()
                     if "forge" in i.lower() or "neoforge" in i.lower()]
            vid = found[-1] if found else f"{mc_version}-forge-{full}"
            self.instance.set_meta("mc_version", vid)
            return vid
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def _install_forge_legacy(self, installer_jar, profile, mc_version, force=False):
        """1.12 及更早：从安装器里抽出 versionInfo + universal.jar，不跑 --installClient。

        旧安装器会装进 %APPDATA%\\.minecraft，忽略实例目录，导致 mods 永远不会被加载。
        """
        install = profile.get("install") or {}
        vinfo = profile.get("versionInfo") or {}
        vid = vinfo.get("id") or install.get("target")
        if not vid:
            raise InstallError("旧版 Forge 安装器缺少版本 id")
        vanilla = install.get("minecraft") or mc_version
        if not self.instance.has_version(vanilla):
            if self.on_progress:
                self.on_progress(f"安装 Minecraft {vanilla}", 0, 1)
            self.install_version(vanilla, force=force)

        vdir = self.instance.versions_dir() / vid
        utils.ensure_dir(vdir)
        utils.write_json(vdir / f"{vid}.json", vinfo)

        src_jar = self.instance.versions_dir() / vanilla / f"{vanilla}.jar"
        dest_jar = vdir / f"{vid}.jar"
        if not src_jar.is_file():
            raise InstallError(f"缺少原版客户端 jar: {src_jar}")
        if force or not dest_jar.is_file():
            shutil.copy2(src_jar, dest_jar)

        maven_name = install.get("path")
        file_path = install.get("filePath")
        if maven_name and file_path:
            dest = self.instance.libraries_dir() / utils.maven_artifact_path(maven_name)
            dest.parent.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(installer_jar) as zf:
                try:
                    with zf.open(file_path) as src, open(dest, "wb") as out:
                        shutil.copyfileobj(src, out)
                except KeyError as e:
                    raise InstallError(f"Forge 安装器中找不到 {file_path}") from e

        if self.on_progress:
            self.on_progress(f"安装 Forge 依赖库 {vid}", 0, 1)
        # 必须合并 inheritsFrom 的原版库，否则 LWJGL natives 不会进 Forge 版本目录
        resolved = manifest.resolve_inherits(vinfo, self._load_parent_json)
        self._install_libraries(resolved, vid, force=force)
        self.instance.set_meta("mc_version", vid)
        utils.log.info("旧版 Forge %s 安装完成", vid)
        return vid

    def install_neoforge(self, mc_version, version=None, force=False):
        xml = self.dm.fetch_text(f"{NEOFORGE_MAVEN}/maven-metadata.xml")
        versions = parse_maven_versions(xml)

        if version:
            if version in versions:
                full = version
            elif f"{mc_version}-{version}" in versions:
                full = f"{mc_version}-{version}"
            else:
                utils.log.warning("NeoForge %s 不存在 (MC %s)，改用该版本最新 NeoForge",
                                  version, mc_version)
                if self.on_progress:
                    self.on_progress(f"NeoForge {version} 不可用，改用最新版", 0, 1)
                version = None
        if not version:
            mc_tuple = self._mc_tuple(mc_version)
            if mc_tuple >= (1, 20, 2):
                # 新版本号方案：1.20.4 -> 20.4.x
                prefix = ".".join(mc_version.split(".")[1:])
                mapped = [v for v in versions if v.startswith(prefix + ".")]
            else:
                mapped = [v for v in versions if v.startswith(mc_version + "-")]
                if not mapped:
                    prefix = NEOFORGE_MC_MAP.get(mc_version)
                    mapped = [v for v in versions if prefix and v.startswith(prefix + ".")]
            if not mapped:
                raise InstallError(f"NeoForge 没有支持 Minecraft {mc_version} 的版本")
            full = mapped[-1]

        installer_url = f"{NEOFORGE_MAVEN}/{full}/neoforge-{full}-installer.jar"
        cache = utils.ROOT / "cache"
        installer_jar = cache / f"neoforge-{full}-installer.jar"
        if self.on_progress:
            self.on_progress(f"下载 NeoForge 安装器 {full}", 0, 1)
        self.dm.download(installer_url, installer_jar, force=force)

        profile = self._read_forge_install_profile(installer_jar)
        if profile and (profile.get("processors") or profile.get("json")):
            if self.on_progress:
                self.on_progress(f"离线安装 NeoForge {full}", 0, 1)
            return self._install_forge_modern(installer_jar, profile, mc_version, force=force)

        from . import java as java_mod
        java_exe = java_mod.java_for_installer("neoforge", self.dm, on_progress=self.on_progress)
        return self._install_forge_cli(installer_jar, mc_version, full, java_exe, label="NeoForge")

    def _merge_libraries_to_shared(self):
        """共享库模式下，把官方安装器写入实例目录的库合并到共享目录。"""
        from .config import CONFIG
        if not CONFIG.get("shared_libraries"):
            return
        local_libs = self.instance.path / "libraries"
        shared_libs = self.instance.libraries_dir()
        if not local_libs.is_dir() or local_libs.resolve() == shared_libs.resolve():
            return
        moved = 0
        for src in local_libs.rglob("*"):
            if not src.is_file():
                continue
            dst = shared_libs / src.relative_to(local_libs)
            if not dst.is_file():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                moved += 1
        if moved:
            utils.log.info("已把 %s 个库文件合并到共享目录 %s", moved, shared_libs)

    @staticmethod
    def _mc_tuple(mc_version):
        parts = re.split(r"[.\-]", mc_version)
        try:
            return (int(parts[0]), int(parts[1]), int(parts[2]) if len(parts) > 2 else 0)
        except (IndexError, ValueError):
            return (0, 0, 0)

    def install_optifine(self, mc_version, typ="", patch="", force=False):
        from . import optifine as optifine_mod
        return optifine_mod.install(self, mc_version, typ=typ, patch=patch, force=force)

    def install_liteloader(self, mc_version, force=False):
        from . import liteloader as liteloader_mod
        return liteloader_mod.install(self, mc_version, force=force)

    # ================================================================ 卸载

    def uninstall_version(self, version_id):
        vdir = self.instance.versions_dir() / version_id
        if not vdir.is_dir():
            raise InstallError(f"版本 {version_id} 未安装")
        from . import trash
        disposition = trash.trash_or_delete(vdir)
        utils.log.info("已卸载版本 %s%s", version_id,
                       "（已移入回收站）" if disposition == "trash" else "")
