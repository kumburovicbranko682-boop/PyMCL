# -*- coding: utf-8 -*-
"""崩溃分析与全局钩子单测。"""
import os
import sys
import tempfile
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from mclauncher.crash import (  # noqa: E402
    analyze_launch, export_report, filter_secrets, normalize_exit, exit_hint,
)
from mclauncher.guard import install, write_log  # noqa: E402

FAILED = []


def check(name, cond):
    print(("  PASS  " if cond else "  FAIL  ") + name)
    if not cond:
        FAILED.append(name)


def _inst(tmp: Path, latest="", crash="", hs=""):
    (tmp / "logs").mkdir(parents=True, exist_ok=True)
    (tmp / "crash-reports").mkdir(parents=True, exist_ok=True)
    if latest:
        (tmp / "logs" / "latest.log").write_text(latest, encoding="utf-8")
    if crash:
        (tmp / "crash-reports" / "crash-2099-01-01_00.00.00-client.txt").write_text(crash, encoding="utf-8")
    if hs:
        (tmp / "hs_err_pid1.log").write_text(hs, encoding="utf-8")
    return tmp


print("[1] 退出码")
check("normalize 无符号 NTSTATUS", normalize_exit(0xC0000409) == -1073740791)
check("exit_hint GPU", "显卡" in exit_hint(-1073740791) or "0xC0000409" in exit_hint(-1073740791))
check("exit 0 无提示", exit_hint(0) == "")

print("[2] 规则：内存 / Java / Fabric / Mixin / 重复")
with tempfile.TemporaryDirectory() as d:
    p = _inst(Path(d), latest="java.lang.OutOfMemoryError: Java heap space\n")
    r = analyze_launch(p, exit_code=1, output_lines=["java.lang.OutOfMemoryError"], started_at=time.time())
    codes = [x["code"] for x in r["reasons"]]
    check("OOM 命中", r["is_crash"] and "oom" in codes)
    check("OOM 文案", "内存不足" in r["detail"])

with tempfile.TemporaryDirectory() as d:
    p = _inst(Path(d), latest="Unsupported class file major version 65\n")
    r = analyze_launch(p, exit_code=1, started_at=time.time())
    codes = [x["code"] for x in r["reasons"]]
    check("Java 不兼容", "java_mismatch" in codes)

with tempfile.TemporaryDirectory() as d:
    text = (
        "A potential solution has been determined:\n"
        " - Install fabric-api\n"
        " - Remove sodium\n"
    )
    p = _inst(Path(d), latest=text)
    r = analyze_launch(p, exit_code=1, output_lines=text.splitlines(), started_at=time.time())
    codes = [x["code"] for x in r["reasons"]]
    check("Fabric 解决方案", "fabric_solution" in codes)
    check("Fabric 文案含 Install", "Install fabric-api" in r["detail"])

with tempfile.TemporaryDirectory() as d:
    p = _inst(Path(d), latest="MixinApplyError: mixin apply failed from mod sodium] from\n")
    r = analyze_launch(p, exit_code=1, started_at=time.time())
    codes = [x["code"] for x in r["reasons"]]
    check("Mixin 失败", "mixin" in codes)

with tempfile.TemporaryDirectory() as d:
    p = _inst(Path(d), latest="Found a duplicate mod: mods/jei-1.jar and mods/jei-2.jar\n")
    r = analyze_launch(p, exit_code=1, started_at=time.time())
    codes = [x["code"] for x in r["reasons"]]
    check("Mod 重复", "mod_dup" in codes)

with tempfile.TemporaryDirectory() as d:
    text = (
        'Exception in thread "main" java.lang.module.ResolutionException: '
        "Module it.unimi.dsi.fastutil reads more than one module named "
        "cpw.mods.bootstraplauncher\n"
        "\tat java.base/java.lang.module.Resolver.resolveFail(Unknown Source)\n"
        "\tat cpw.mods.bootstraplauncher@1.1.2/cpw.mods.bootstraplauncher."
        "BootstrapLauncher.main(BootstrapLauncher.java:129)\n"
    )
    p = _inst(Path(d), latest=text)
    r = analyze_launch(p, exit_code=1, output_lines=text.splitlines(), started_at=time.time())
    codes = [x["code"] for x in r["reasons"]]
    check("模块重复加载命中", r["is_crash"] and "dup_module" in codes)
    check("模块重复文案", "cpw.mods.bootstraplauncher" in r["detail"] and "模块" in r["detail"])
    check("模块重复给修复动作", any(a.get("id") == "repair_version"
                                    for a in analyze_launch(
                                        p, exit_code=1, output_lines=text.splitlines(),
                                        started_at=time.time(), version="1.20.1-forge-x",
                                    ).get("actions", [])))

print("[3] 退出码 0 + Game crashed")
with tempfile.TemporaryDirectory() as d:
    p = _inst(
        Path(d),
        crash="---- Minecraft Crash Report ----\nManually triggered debug crash\n",
        latest="#@!@# Game crashed! Crash report saved to:\n",
    )
    r = analyze_launch(p, exit_code=0, output_lines=["#@!@# Game crashed! Crash report saved to:"], started_at=time.time())
    check("exit 0 仍判崩溃", r["is_crash"] is True)
    codes = [x["code"] for x in r["reasons"]]
    check("调试崩溃", "debug_crash" in codes)

print("[4] 正常退出不误报")
with tempfile.TemporaryDirectory() as d:
    p = _inst(Path(d), latest="[INFO] Stopping!\n")
    r = analyze_launch(p, exit_code=0, output_lines=["Stopping!"], started_at=time.time())
    check("正常退出不是崩溃", r["is_crash"] is False)

print("[5] 用户取消")
with tempfile.TemporaryDirectory() as d:
    p = _inst(Path(d))
    r = analyze_launch(p, exit_code=1, cancelled=True, started_at=time.time())
    check("取消不当崩溃", r["is_crash"] is False)

print("[6] 显卡 hs_err")
with tempfile.TemporaryDirectory() as d:
    p = _inst(Path(d), hs="EXCEPTION_ACCESS_VIOLATION\n# C [nvoglv64.dll+0x1]\n")
    r = analyze_launch(p, exit_code=-1073740791, started_at=time.time())
    codes = [x["code"] for x in r["reasons"]]
    check("NVIDIA AV", "nvidia_av" in codes)
    check("求助页脚", "而不是发送这个窗口的照片" in r["detail"])

print("[7] 导出 zip + 脱敏")
check("脱敏 token", "***" in filter_secrets("accessToken: abcdef123456") and "abcdef123456" not in filter_secrets("accessToken: abcdef123456"))
with tempfile.TemporaryDirectory() as d:
    p = Path(d) / "out.zip"
    report = {
        "instance": "t", "version": "1.20.1", "exit_code": 1, "exit_hint": "x",
        "detail": "内存不足", "output_tail": "accessToken: SECRETTOKEN",
        "files": [],
    }
    dest = export_report(report, p)
    check("zip 写出", Path(dest).is_file())
    with zipfile.ZipFile(dest) as zf:
        names = zf.namelist()
        check("含分析结论", "分析结论.txt" in names)
        raw = zf.read("游戏崩溃前的输出.txt").decode("utf-8")
        check("zip 内脱敏", "SECRETTOKEN" not in raw and "***" in raw)

print("[8] 全局钩子写日志")
log = ROOT / "_test_guard.log"
try:
    if log.exists():
        log.unlink()
    install(log)
    path = write_log("test", "hello-guard")
    check("guard 日志", path.is_file() and "hello-guard" in path.read_text(encoding="utf-8"))
finally:
    try:
        if log.exists() and log.stat().st_size > 2_000_000:
            log.unlink()
    except OSError:
        pass

if FAILED:
    sys.stderr.write("FAIL " + " | ".join(FAILED) + "\n")
    sys.exit(1)
print("ALL PASS")
