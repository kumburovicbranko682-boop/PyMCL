# -*- coding: utf-8 -*-
"""启动命令模块路径回归测试。

用户报告：带模组启动 Forge/NeoForge 时游戏退出码 1，日志为
    java.lang.module.ResolutionException:
    Module it.unimi.dsi.fastutil reads more than one module named
    cpw.mods.bootstraplauncher
成因是 -p（模块路径）上的 jar 同时经 classpath 进入 BootstrapLauncher 的
MC-BOOTSTRAP 层（-DignoreList 未覆盖），或 inherit 合并叠出重复的 -p。
本测试用合成 version JSON 断言：
  1. 最终命令只有一个 --module-path，bootstraplauncher 只出现一次；
  2. -p 上每个 jar 的文件名都被 -DignoreList 前缀覆盖；
  3. ignoreList 整个缺失时会补一条；
  4. classpath 上 bootstraplauncher 只出现一次；
  5. 正常 Forge JSON 的 ignoreList 前缀原样保留。
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mclauncher import launcher
from mclauncher.launcher import (
    _merge_module_paths,
    _module_entry_key,
    build_launch_command,
)

BSL = "cpw/mods/bootstraplauncher/1.1.2/bootstraplauncher-1.1.2.jar"
BSL_OLD = "cpw/mods/bootstraplauncher/1.0.0/bootstraplauncher-1.0.0.jar"
ASM = "org/ow2/asm/asm/9.7/asm-9.7.jar"
SJH = "cpw/mods/securejarhandler/2.1.10/securejarhandler-2.1.10.jar"

VANILLA = {
    "id": "1.20.1",
    "type": "release",
    "mainClass": "net.minecraft.client.main.Main",
    "assetIndex": {"id": "5"},
    "javaVersion": {"majorVersion": 17},
    "arguments": {
        "game": ["--username", "${auth_player_name}", "--gameDir", "${game_directory}"],
        "jvm": ["-Djava.library.path=${natives_directory}", "-cp", "${classpath}"],
    },
    "libraries": [
        {"name": "com.mojang:patchy:2.2.10",
         "downloads": {"artifact": {"path": "com/mojang/patchy/2.2.10/patchy-2.2.10.jar"}}},
    ],
}


def _forge(ignore_list="-DignoreList=securejarhandler,asm,client-extra,${version_name}.jar"):
    jvm = [
        "-DlibraryDirectory=${library_directory}",
        "-p",
        "${library_directory}/" + BSL + "${classpath_separator}${library_directory}/" + ASM
        + "${classpath_separator}${library_directory}/" + SJH,
        "--add-modules",
        "ALL-MODULE-PATH",
    ]
    if ignore_list:
        jvm.insert(0, ignore_list)
    return {
        "id": "1.20.1-forge-x",
        "inheritsFrom": "1.20.1",
        "mainClass": "cpw.mods.bootstraplauncher.BootstrapLauncher",
        "arguments": {"game": ["--launchTarget", "forgeclient"], "jvm": jvm},
        "libraries": [
            {"name": "cpw.mods:bootstraplauncher:1.1.2",
             "downloads": {"artifact": {"path": BSL}}},
            {"name": "org.ow2.asm:asm:9.7",
             "downloads": {"artifact": {"path": ASM}}},
            {"name": "cpw.mods:securejarhandler:2.1.10",
             "downloads": {"artifact": {"path": SJH}}},
        ],
    }


# 整合包/二次导出的版本 JSON：inherit Forge 的同时又复制了一份 -p，
# libraries 里 bootstraplauncher 还重复声明了两个版本。
PACK = {
    "id": "pack-1.20.1",
    "inheritsFrom": "1.20.1-forge-x",
    "arguments": {
        "game": [],
        "jvm": ["-p", "${library_directory}/" + BSL_OLD],
    },
    "libraries": [
        {"name": "cpw.mods:bootstraplauncher:1.0.0",
         "downloads": {"artifact": {"path": BSL_OLD}}},
        {"name": "cpw.mods:bootstraplauncher:1.1.2",
         "downloads": {"artifact": {"path": BSL}}},
    ],
}


class FakeInstance:
    def __init__(self, root: Path, jsons: dict):
        self.name = "test"
        self.path = root
        self._jsons = jsons
        for vid in jsons:
            vdir = root / "versions" / vid
            vdir.mkdir(parents=True, exist_ok=True)
            (vdir / f"{vid}.jar").write_bytes(b"jar")

    def version_json(self, vid):
        data = self._jsons.get(vid)
        return json.loads(json.dumps(data)) if data else None

    def versions_dir(self):
        return self.path / "versions"

    def libraries_dir(self):
        return self.path / "libraries"

    def assets_dir(self):
        return self.path / "assets"

    def natives_dir(self, version_id, version_json=None):
        return self.versions_dir() / version_id / f"{version_id}-natives"


PROPS = {"name": "Steve", "uuid": "0" * 32, "token": "0",
         "user_type": "legacy", "xuid": ""}


def _build(jsons, vid):
    with tempfile.TemporaryDirectory() as td:
        inst = FakeInstance(Path(td), jsons)
        with mock.patch.object(launcher.java_mod, "java_usable_for", return_value=True), \
                mock.patch.object(launcher.java_mod, "get_java_major", return_value=21):
            cmd, _natives, _vdir, _gdir = build_launch_command(
                inst, vid, PROPS, "/fake/java", memory_mb=1024)
        return cmd


def _module_path_of(cmd):
    values = [cmd[i + 1] for i, a in enumerate(cmd) if a in ("-p", "--module-path")]
    return values


def _ignore_prefixes(cmd):
    for a in cmd:
        if isinstance(a, str) and a.startswith("-DignoreList="):
            return [p for p in a.split("=", 1)[1].split(",") if p]
    return None


class ModuleEntryKeyTests(unittest.TestCase):
    def test_same_artifact_different_versions_share_key(self):
        a = _module_entry_key("/lib/cpw/mods/bootstraplauncher/1.1.2/bootstraplauncher-1.1.2.jar")
        b = _module_entry_key("/lib/cpw/mods/bootstraplauncher/1.0.0/bootstraplauncher-1.0.0.jar")
        self.assertEqual(a, b)

    def test_different_artifacts_do_not_collide(self):
        a = _module_entry_key("/lib/org/ow2/asm/asm/9.7/asm-9.7.jar")
        b = _module_entry_key("/lib/org/ow2/asm/asm-commons/9.7/asm-commons-9.7.jar")
        self.assertNotEqual(a, b)

    def test_non_maven_layout_falls_back_to_full_path(self):
        a = _module_entry_key("/opt/jars/foo.jar")
        b = _module_entry_key("/opt/jars/bar.jar")
        self.assertNotEqual(a, b)


class MergeModulePathsTests(unittest.TestCase):
    def test_two_flags_merged_into_one(self):
        args = ["-p", "/l/a/1.0/a-1.0.jar", "-Dx=1", "--module-path", "/l/b/2.0/b-2.0.jar"]
        out, entries = _merge_module_paths(args)
        self.assertEqual(out.count("--module-path"), 1)
        self.assertNotIn("-p", out)
        self.assertEqual(entries, ["/l/a/1.0/a-1.0.jar", "/l/b/2.0/b-2.0.jar"])

    def test_duplicate_artifact_keeps_later_path_at_first_position(self):
        args = ["-p", os.pathsep.join(["/l/bsl/1.1.2/bsl-1.1.2.jar", "/l/asm/9.7/asm-9.7.jar"]),
                "-p", "/l/bsl/1.0.0/bsl-1.0.0.jar"]
        out, entries = _merge_module_paths(args)
        self.assertEqual(entries, ["/l/bsl/1.0.0/bsl-1.0.0.jar", "/l/asm/9.7/asm-9.7.jar"])

    def test_exact_duplicate_removed(self):
        args = ["-p", os.pathsep.join(["/l/bsl/1.1.2/bsl-1.1.2.jar"] * 2)]
        _out, entries = _merge_module_paths(args)
        self.assertEqual(entries, ["/l/bsl/1.1.2/bsl-1.1.2.jar"])

    def test_no_module_flags_untouched(self):
        args = ["-Xmx1G", "-cp", "a.jar"]
        out, entries = _merge_module_paths(args)
        self.assertEqual(out, args)
        self.assertEqual(entries, [])


class BuildLaunchCommandTests(unittest.TestCase):
    def test_normal_forge_single_module_path_and_covered_ignore(self):
        cmd = _build({"1.20.1": VANILLA, "1.20.1-forge-x": _forge()}, "1.20.1-forge-x")
        values = _module_path_of(cmd)
        self.assertEqual(len(values), 1)
        entries = values[0].split(os.pathsep)
        self.assertEqual(sum("bootstraplauncher" in e for e in entries), 1)
        prefixes = _ignore_prefixes(cmd)
        self.assertIsNotNone(prefixes)
        # 原有前缀保留
        for keep in ("securejarhandler", "asm", "client-extra"):
            self.assertIn(keep, prefixes)
        # -p 上每个 jar 的文件名都被前缀覆盖
        for e in entries:
            base = os.path.basename(e)
            self.assertTrue(any(base.startswith(p) for p in prefixes),
                            f"{base} 未被 ignoreList 覆盖: {prefixes}")

    def test_user_report_ignore_list_without_bootstraplauncher(self):
        # 复现用户崩溃输入：Forge JSON 的 ignoreList 缺 bootstraplauncher 前缀
        forge = _forge("-DignoreList=securejarhandler,asm,client-extra,${version_name}.jar")
        pfx = [p for p in forge["arguments"]["jvm"][0].split("=", 1)[1].split(",")]
        self.assertNotIn("bootstraplauncher", pfx)
        cmd = _build({"1.20.1": VANILLA, "1.20.1-forge-x": forge}, "1.20.1-forge-x")
        prefixes = _ignore_prefixes(cmd)
        base = "bootstraplauncher-1.1.2.jar"
        self.assertTrue(any(base.startswith(p) for p in prefixes),
                        f"模块 jar 未被补进 ignoreList: {prefixes}")

    def test_ignore_list_added_when_missing(self):
        forge = _forge(ignore_list=None)
        self.assertFalse(any(str(a).startswith("-DignoreList=")
                             for a in forge["arguments"]["jvm"]))
        cmd = _build({"1.20.1": VANILLA, "1.20.1-forge-x": forge}, "1.20.1-forge-x")
        prefixes = _ignore_prefixes(cmd)
        self.assertIsNotNone(prefixes, "缺失的 ignoreList 应被补上")
        for base in ("bootstraplauncher-1.1.2.jar", "asm-9.7.jar",
                     "securejarhandler-2.1.10.jar"):
            self.assertTrue(any(base.startswith(p) for p in prefixes))

    def test_pack_inherit_merge_dedupes_module_path_and_classpath(self):
        cmd = _build({"1.20.1": VANILLA, "1.20.1-forge-x": _forge(),
                      "pack-1.20.1": PACK}, "pack-1.20.1")
        values = _module_path_of(cmd)
        self.assertEqual(len(values), 1, f"应只有一个 --module-path: {cmd}")
        entries = values[0].split(os.pathsep)
        self.assertEqual(sum("bootstraplauncher" in e for e in entries), 1,
                         f"bootstraplauncher 在 -p 上重复: {entries}")
        # classpath 上也只有一个 bootstraplauncher
        cp = next(cmd[i + 1] for i, a in enumerate(cmd) if a in ("-cp", "--class-path"))
        self.assertEqual(sum("bootstraplauncher" in e for e in cp.split(os.pathsep)), 1)
        # 覆盖检查：-p 上每个 jar 均被 ignoreList 前缀覆盖
        prefixes = _ignore_prefixes(cmd)
        for e in entries:
            base = os.path.basename(e)
            self.assertTrue(any(base.startswith(p) for p in prefixes))

    def test_vanilla_untouched(self):
        cmd = _build({"1.20.1": VANILLA}, "1.20.1")
        self.assertEqual(_module_path_of(cmd), [])
        self.assertIsNone(_ignore_prefixes(cmd))


if __name__ == "__main__":
    unittest.main()
