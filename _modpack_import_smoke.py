# -*- coding: utf-8 -*-
"""整合包导入格式识别冒烟：嵌套 manifest / .minecraft 目录压缩包 / 无效包报错。

覆盖 2026-08-22 的「整合包缺少 manifest.json」问题：此前只认 zip 根目录的
CurseForge manifest，套了一层文件夹的包和直接压缩的 .minecraft 目录全被拒。
"""
import json
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from mclauncher import modpack as mp
from mclauncher.modpack import ModpackError

FAILED = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  {detail}" if not cond and detail else ""))
    if not cond:
        FAILED.append(name)


def make_tree(base: Path, files: dict):
    for rel, content in files.items():
        p = base / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content if isinstance(content, str) else json.dumps(content), encoding="utf-8")


def main():
    tmp = Path(tempfile.mkdtemp(prefix="pymcl_mp_smoke_"))
    try:
        # ---- 1. 嵌套 manifest 定位 --------------------------------------
        t1 = tmp / "nest1"; make_tree(t1, {"MyPack/manifest.json": "{}"})
        r = mp._nested_marker_root(t1, "manifest.json")
        check("nested.one_level", r is not None and r.name == "MyPack", f"got {r}")

        t2 = tmp / "nest2"; make_tree(t2, {"a/b/manifest.json": "{}"})
        r = mp._nested_marker_root(t2, "manifest.json")
        check("nested.two_level", r is not None and r.name == "b", f"got {r}")

        t3 = tmp / "nest3"; make_tree(t3, {"readme.txt": "hi"})
        check("nested.absent", mp._nested_marker_root(t3, "manifest.json") is None)

        # ---- 2. .minecraft 目录识别 --------------------------------------
        p1 = tmp / "plain1"
        make_tree(p1, {"mods/a.jar": "x", "config/t.toml": "y", "options.txt": "z"})
        check("plain.at_root", mp._plain_pack_root(p1) == p1)

        p2 = tmp / "plain2"; make_tree(p2, {"PackName/mods/a.jar": "x", "PackName/config/c": "y"})
        r = mp._plain_pack_root(p2)
        check("plain.one_level_down", r is not None and r.name == "PackName", f"got {r}")

        p3 = tmp / "plain3"; make_tree(p3, {"fabric.mod.json": "{}", "assets/lang/en.json": "{}"})
        check("plain.modjar_rejected", mp._plain_pack_root(p3) is None)

        p4 = tmp / "plain4"; make_tree(p4, {"pack.mcmeta": "{}", "assets/minecraft/a.png": "x"})
        check("plain.resourcepack_rejected", mp._plain_pack_root(p4) is None)

        # ---- 3. versions/ 版本与加载器推断 --------------------------------
        def with_versions(name, vid, extra=None):
            base = tmp / name
            j = {"id": vid, "inheritsFrom": vid.split("-")[0]}
            j.update(extra or {})
            make_tree(base, {f"versions/{vid}/{vid}.json": j})
            return base

        v = mp._plain_pack_version(with_versions("ver_forge", "1.20.1-forge-47.2.0",
                                                 {"libraries": [{"name": "net.minecraftforge:forge:1.20.1-47.2.0"}]}))
        check("ver.forge", v == {"mc": "1.20.1", "loader": "forge", "loader_version": "47.2.0"}, f"got {v}")

        v = mp._plain_pack_version(with_versions("ver_fabric", "fabric-loader-0.15.11-1.20.1"))
        check("ver.fabric", v == {"mc": "1.20.1", "loader": "fabric-loader", "loader_version": "0.15.11"}, f"got {v}")

        v = mp._plain_pack_version(with_versions("ver_vanilla", "1.20.1"))
        check("ver.vanilla", v == {"mc": "1.20.1", "loader": None, "loader_version": ""}, f"got {v}")

        v = mp._plain_pack_version(with_versions("ver_oldforge", "1.12.2-forge1.12.2-14.23.5.2859"))
        check("ver.oldforge_loader", bool(v) and v["loader"] == "forge" and v["mc"] == "1.12.2", f"got {v}")

        check("ver.no_versions", mp._plain_pack_version(p1) is None)

        # ---- 4. 无效包：报错要带 zip 顶层内容与格式指引 -------------------
        badzip = tmp / "not_a_pack.zip"
        with zipfile.ZipFile(badzip, "w") as z:
            z.writestr("fabric.mod.json", "{}")
            z.writestr("META-INF/mods.toml", "")
        try:
            mp.install_cf_zip(None, str(badzip), None)
            check("badzip.error_raised", False, "no error raised")
        except ModpackError as e:
            msg = str(e)
            check("badzip.error_raised", "缺少 manifest.json" in msg)
            check("badzip.lists_top", "zip 顶层内容" in msg and "fabric.mod.json" in msg, msg)
            check("badzip.formats_hint", "支持三种格式" in msg, msg)

        # ---- 5. _copy_embedded_versions plain 根 --------------------------
        src = tmp / "emb"; make_tree(src, {"versions/1.20.1/1.20.1.json": {"id": "1.20.1"}})
        dest = tmp / "emb_dest"; (dest / "versions").mkdir(parents=True)

        class _FakeInst:
            def versions_dir(self):
                return dest / "versions"

        copied = mp._copy_embedded_versions(src, _FakeInst(), plain=True)
        check("embedded.plain_root", copied == ["1.20.1"] and (dest / "versions/1.20.1/1.20.1.json").is_file(),
              f"copied={copied}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if FAILED:
        print(f"FAILED: {len(FAILED)} -> {FAILED}")
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main()
