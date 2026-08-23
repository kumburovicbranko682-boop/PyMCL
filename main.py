# -*- coding: utf-8 -*-
"""PyMCL 命令行入口。

用法:
    python main.py                              # 打开图形界面
    python main.py gui                          # 同上
    python main.py versions [关键字]            # 列出可下载的 Minecraft 版本
    python main.py install <版本>               # 下载安装版本
    python main.py install-fabric <MC版本>      # 安装 Fabric
    python main.py install-quilt <MC版本>       # 安装 Quilt
    python main.py install-forge <MC版本>       # 安装 Forge
    python main.py install-neoforge <MC版本>    # 安装 NeoForge
    python main.py launch <版本> --username 名  # 启动游戏
    python main.py list                         # 查看实例与已装版本
    python main.py java list|install <8|11|17|21>
    python main.py login                        # 微软正版登录
    python main.py search <关键词>              # 搜索 Modrinth 整合包
    python main.py modpack <链接或文件>         # 安装整合包
    python main.py mods search <关键词>         # 搜索模组
    python main.py mods install <slug/链接/jar> # 安装模组
    python main.py mods list                    # 列出已装模组
    python main.py instance create|delete|rename|list
    python main.py uninstall <版本>
"""
import argparse
import sys
from pathlib import Path

from mclauncher import APP_DISPLAY_NAME, APP_VERSION, utils
from mclauncher.guard import install as install_guard
from mclauncher.config import CONFIG

# GUI 启动也会走本模块（launcher_entry -> import main）。CLI 专属的重依赖
# （downloader 连带 requests/urllib3、installer、launcher 等）在用到它的
# 函数体内局部 import，GUI 冷启动不会加载。
#
# 注意不能用「模块级 __getattr__ + 函数体裸引用」的假懒加载：PEP 562 只在
# 属性访问时生效，函数内 LOAD_GLOBAL 不经过它，首次调用就是 NameError
# （此前 `python main.py list / versions` 就是这么崩的）。


# ---------------------------------------------------------------- 工具

def make_dm(cancel=None):
    from mclauncher.downloader import DownloadManager
    threads = CONFIG.get("download_threads", 8)
    return DownloadManager(threads=threads, on_progress=_cli_progress, cancel=cancel)


def _cli_progress(message, done, total):
    bar_len = 28
    if total:
        filled = max(0, min(bar_len, int(bar_len * done / total)))
        bar = "#" * filled + "-" * (bar_len - filled)
        if total >= 4096:
            extra = f"{utils.format_size(done)}/{utils.format_size(total)} {done * 100 / total:.1f}%"
        else:
            extra = f"{done}/{total}"
        sys.stdout.write(f"\r  [{bar}] {extra}  {message}    ")
        sys.stdout.flush()
        if done >= total:
            sys.stdout.write("\n")
    else:
        sys.stdout.write(f"\r  {message}    ")
        sys.stdout.flush()


def get_instance(name=None):
    from mclauncher.instances import Instance, InstanceError
    name = name or CONFIG.get("default_instance", "default")
    inst = Instance(name)
    if not inst.path.is_dir():
        if name == CONFIG.get("default_instance", "default"):
            inst.create()  # 默认实例不存在时自动创建
        else:
            raise InstanceError(f"实例 {name} 不存在，请先创建（GUI 的“实例”页或 `instance create`）。")
    return inst


def _exit(msg, code=1):
    try:
        print(msg)
    except Exception:
        pass  # 无控制台窗口的 exe 里 stdout 可能为 None
    try:
        with open(utils.ROOT / "pymcl-error.log", "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except OSError:
        pass
    sys.exit(code)


# ---------------------------------------------------------------- 子命令

def cmd_versions(args):
    from mclauncher import manifest as manifest_mod
    dm = make_dm()
    print("正在获取 Minecraft 版本清单…")
    versions = manifest_mod.list_remote_versions(dm, force=args.refresh)
    rows = []
    for vid, v in versions.items():
        vtype = v.get("type", "?")
        if args.type and args.type != "all" and vtype != args.type:
            continue
        if args.filter and args.filter.lower() not in vid.lower():
            continue
        rows.append((vid, vtype, v.get("releaseTime", "")[:10]))
    rows.sort(key=lambda r: r[0])
    print(f"共 {len(rows)} 个版本:")
    for vid, vtype, date in rows:
        print(f"  {vid:<24} [{vtype}] {date}")


def cmd_install(args):
    from mclauncher.installer import Installer
    inst = get_instance(args.instance)
    dm = make_dm()
    installer = Installer(inst, dm, on_progress=_cli_progress)
    print(f"安装版本 {args.version} -> 实例 {inst.name} …")
    try:
        installer.install_version(args.version, force=args.force)
    except Exception as e:
        _exit(f"安装失败: {e}")
    print(f"安装完成。启动: python main.py launch {args.version} --instance {inst.name} --username 你的名字")


def _install_loader_cmd(args, kind):
    from mclauncher.installer import Installer
    inst = get_instance(args.instance)
    dm = make_dm()
    installer = Installer(inst, dm, on_progress=_cli_progress)
    print(f"为实例 {inst.name} 安装 {kind} (MC {args.mc}) …")
    try:
        if kind == "fabric":
            installer.install_fabric(args.mc, args.loader)
        elif kind == "quilt":
            installer.install_quilt(args.mc, args.loader)
        elif kind == "forge":
            installer.install_forge(args.mc, args.version, force=args.force)
        else:
            installer.install_neoforge(args.mc, args.version, force=args.force)
    except Exception as e:
        _exit(f"安装失败: {e}")
    print("安装完成。")


def cmd_uninstall(args):
    from mclauncher.installer import Installer
    inst = get_instance(args.instance)
    Installer(inst).uninstall_version(args.version)
    print(f"已卸载 {args.version}")


def cmd_list(args):
    from mclauncher.instances import Instance, list_instances
    from mclauncher.auth import AccountManager
    from mclauncher import java as java_mod
    print(f"启动器主目录: {utils.ROOT}")
    names = list_instances()
    if not names:
        print("（没有实例）")
    for name in names:
        inst = Instance(name)
        ids = inst.installed_ids()
        print(f"实例 [{name}]: {', '.join(ids) if ids else '（未安装版本）'}")
    accounts = AccountManager()
    print("账号:", ", ".join(a["name"] + (f"({a['type']})" if a.get("type") == "microsoft" else "(离线)") for a in accounts.accounts) or "无")
    javas = java_mod.all_javas()
    if javas:
        print("Java:", "; ".join(j["name"] for j in javas[:10]))
    else:
        print("Java: 未检测到，会自动下载")


def cmd_launch(args):
    from mclauncher.auth import AccountManager
    from mclauncher import java as java_mod, manifest as manifest_mod
    from mclauncher.launcher import LaunchError, build_launch_command, GameProcess
    inst = get_instance(args.instance)
    if not inst.has_version(args.version):
        _exit(f"版本 {args.version} 未安装。先运行: python main.py install {args.version} --instance {inst.name}")

    manager = AccountManager()
    if args.offline:
        account = manager.offline_account(args.username or "Player")
    elif args.account:
        account = manager.get_account(args.account)
        if not account:
            _exit(f"找不到账号 {args.account}")
        account = manager.ensure_valid(account)
    else:
        account = manager.get_active()
        if account:
            account = manager.ensure_valid(account)
        else:
            account = manager.offline_account(args.username or "Player")
    props = manager.launch_props(account)
    props["name"] = args.username or props["name"]

    vjson = inst.version_json(args.version) or {}
    dm = make_dm()
    try:
        resolved = manifest_mod.resolve_inherits(vjson, lambda pid: inst.version_json(pid))
    except Exception:
        resolved = vjson
    prefer = args.java
    if not prefer:
        pref = inst.java_pref()
        if pref and pref != "自动选择":
            prefer = pref
            print(f"使用实例指定的 Java: {prefer}")
    if prefer and not Path(prefer).is_file():
        _exit(f"指定的 Java 不存在: {prefer}")
    need = java_mod.required_java_major(resolved)
    java_exe = java_mod.pick_java_for_version(resolved, prefer=prefer)
    if prefer and java_exe != prefer:
        got = java_mod.get_java_major(prefer)
        print(f"指定的 Java {got or '?'} 无法启动此版本（需要 Java {need}+），已改自动匹配")
    if not java_exe:
        print(f"未找到 Java {need}，正在自动下载…")
        java_exe = java_mod.ensure_java_for_version(resolved, dm, on_progress=_cli_progress)
    print(f"使用 Java {java_mod.get_java_major(java_exe) or '?'}: {java_exe}")
    print(f"账号: {props['name']} ({'正版' if props['user_type'] == 'msa' else '离线'})")

    try:
        cmd, natives_dir, vdir, _gdir = build_launch_command(
            inst, args.version, props, java_exe,
            memory_mb=args.memory or CONFIG.get("memory_mb", 4096),
            width=args.width, height=args.height,
        )
    except LaunchError as e:
        _exit(f"启动失败: {e}")

    print("启动 Minecraft…（Ctrl+C 结束游戏）")
    proc = GameProcess(cmd, cwd=inst.path, on_line=lambda line: print(line, flush=True))
    try:
        code = proc.wait()
    except KeyboardInterrupt:
        proc.kill()
        print("已停止游戏")
        return
    from mclauncher.crash import analyze_launch, export_report
    report = analyze_launch(
        inst, exit_code=code, output_lines=proc.last_lines(),
        started_at=getattr(proc, "started_at", None),
        cancelled=False, version=args.version,
    )
    if report.get("is_crash"):
        print("\n======== Minecraft 出现错误 ========")
        print(report.get("detail") or report.get("summary"))
        print("\n" + (report.get("help") or ""))
        try:
            zip_path = export_report(report)
            print(f"错误报告已导出: {zip_path}")
        except OSError:
            pass
        sys.exit(code if code not in (0, None) else 1)
    if code not in (0, None):
        print(f"游戏退出，代码 {code}")
        sys.exit(code)


def cmd_java(args):
    from mclauncher import java as java_mod
    dm = make_dm()
    if args.java_cmd == "list":
        javas = java_mod.all_javas()
        if not javas:
            print("未检测到任何 Java（会在需要时自动下载）")
            return
        for j in javas:
            print(f"  {j['name']}   [major={j.get('major')}]")
            print(f"      {j['exe']}")
    elif args.java_cmd == "install":
        try:
            major = int(args.major)
        except (TypeError, ValueError):
            _exit(f"用法: python main.py java install <8|11|17|21>，当前值: {args.major!r}")
        print(f"下载 Adoptium Java {major} …")
        exe = java_mod.install_adoptium(dm, major, on_progress=_cli_progress)
        print(f"完成: {exe}")


def cmd_login(args):
    from mclauncher.auth import AccountManager
    manager = AccountManager()
    client_id = args.client_id or CONFIG.get("microsoft_client_id")
    from mclauncher.auth import MicrosoftAuthenticator
    print("微软账号登录（设备代码流）")
    account = MicrosoftAuthenticator(client_id=client_id).login(
        on_code=lambda code, uri, exp: print(
            f"\n请在浏览器打开: {uri}\n输入代码: {code}\n（代码 {exp // 60} 分钟内有效）"
        ),
        on_status=lambda s: None,
        open_browser=True,
    )
    manager.add_account(account)
    print(f"登录成功！欢迎，{account['name']} ({account['uuid']})")


def cmd_search(args):
    from mclauncher import modpack as modpack_mod
    dm = make_dm()
    source = getattr(args, "source", "modrinth")
    print(f"搜索 {'CurseForge' if source == 'curseforge' else '中文' if source == 'chinese' else 'Modrinth'} 整合包: {args.query}")
    try:
        if source == "curseforge":
            key = CONFIG.get("curseforge_api_key") or None
            hits = modpack_mod.search_cf_modpacks(dm, args.query, limit=args.limit, api_key=key)
        elif source == "chinese":
            key = CONFIG.get("curseforge_api_key") or None
            hits = modpack_mod.search_modpacks_chinese(dm, args.query, limit=args.limit, api_key=key)
        else:
            hits = modpack_mod.modrinth_search(dm, args.query, limit=args.limit)
    except Exception as e:
        _exit(f"搜索失败: {e}")
    if not hits:
        print("没有结果")
        return
    for h in hits:
        extra = f"  [id={h.get('id')}]" if h.get("source") == "curseforge" else f"  [{h.get('slug')}]"
        print(f"  {h['title']}{extra}  下载量: {h['downloads']}")
        print(f"      {h.get('description') or ''}")


def cmd_modpack(args):
    from mclauncher import modpack as modpack_mod
    inst = get_instance(args.instance)
    dm = make_dm()
    src = args.source
    is_mrpack = src.lower().endswith(".mrpack") or "modrinth" in src.lower()
    print(f"安装整合包 -> 实例 {inst.name}")
    try:
        if is_mrpack:
            meta = modpack_mod.install_mrpack(dm, src, inst)
        else:
            meta = modpack_mod.install_cf_zip(dm, src, inst)
    except Exception as e:
        _exit(f"安装整合包失败: {e}")
    print(f"整合包安装完成: {meta['name']}")


def cmd_mods(args):
    from mclauncher import mods as mods_mod
    inst = get_instance(args.instance)
    dm = make_dm()
    if args.mods_cmd == "search":
        source = getattr(args, "source", "modrinth")
        print(f"搜索 {'CurseForge' if source == 'curseforge' else '中文' if source == 'chinese' else 'Modrinth'} 模组: {args.query}")
        try:
            if source == "curseforge":
                key = CONFIG.get("curseforge_api_key") or None
                hits = mods_mod.search_curseforge(dm, args.query, limit=args.limit, api_key=key)
            elif source == "chinese":
                key = CONFIG.get("curseforge_api_key") or None
                hits = mods_mod.search_mods_chinese(dm, args.query, limit=args.limit, api_key=key)
            else:
                hits = mods_mod.search_mods(dm, args.query, limit=args.limit)
        except Exception as e:
            _exit(f"搜索失败: {e}")
        if not hits:
            print("没有结果")
            return
        for h in hits:
            extra = f"  [id={h.get('id')}]" if h.get("source") == "curseforge" else f"  [{h.get('slug')}]"
            print(f"  {h['title']}{extra}  下载量: {h['downloads']}")
            print(f"      {h.get('description') or h.get('summary') or ''}")
    elif args.mods_cmd == "install":
        print(f"安装模组 -> 实例 {inst.name} …")
        try:
            result = mods_mod.install_mod_from_source(
                dm, args.source, inst,
                mc_version=args.mc, loader=args.loader,
                on_progress=_cli_progress,
            )
        except Exception as e:
            _exit(f"安装模组失败: {e}")
        print(f"安装完成: {result.get('slug') or result.get('title') or result.get('source')} "
              f"({', '.join(result.get('files', []))})")
    else:  # list
        mods = mods_mod.list_instance_mods(inst)
        print(f"实例 [{inst.name}] 的模组 ({len(mods)} 个):")
        for p in mods:
            print(f"  {p.name}")


def cmd_instance(args):
    from mclauncher.instances import Instance, InstanceError, list_instances
    if args.inst_cmd == "create":
        inst = Instance(args.name)
        try:
            inst.create()
        except InstanceError as e:
            _exit(str(e))
        print(f"实例 {args.name} 已创建: {inst.path}")
    elif args.inst_cmd == "delete":
        Instance(args.name).delete()
        print(f"实例 {args.name} 已删除")
    elif args.inst_cmd == "rename":
        inst = Instance(args.name)
        try:
            inst.rename(args.new_name)
        except InstanceError as e:
            _exit(str(e))
        print(f"已重命名为 {args.new_name}")
    else:
        names = list_instances()
        print("实例列表:", ", ".join(names) if names else "（空）")


def cmd_sysinfo(args):
    from mclauncher import sysinfo as sysinfo_mod
    print(sysinfo_mod.format_text(sysinfo_mod.collect(force=True, scan_system_java=True)))


def cmd_feedback(args):
    from mclauncher import feedback as fb
    if args.i_agree:
        fb.set_consent(True)
    data = fb.submit(
        category=args.category,
        title=args.title or "",
        body=args.body or "",
        contact=args.contact or "",
        include_sysinfo=not args.no_sysinfo,
    )
    print("已提交", data.get("id") or "")


# ---------------------------------------------------------------- 入口

def build_parser():
    p = argparse.ArgumentParser(prog="pymcl", description=f"{APP_DISPLAY_NAME} v{APP_VERSION}")
    p.add_argument("--instance", "-i", dest="instance", help="实例名（版本隔离目录）")
    sub = p.add_subparsers(dest="command")

    sp = sub.add_parser("gui", help="打开图形界面")
    sp.set_defaults(func=lambda a: gui_main())

    sp = sub.add_parser("versions", help="列出可下载的 Minecraft 版本")
    sp.add_argument("filter", nargs="?", help="名称过滤关键字")
    sp.add_argument("--type", choices=["release", "snapshot", "old_beta", "old_alpha", "all"], default=None)
    sp.add_argument("--refresh", action="store_true", help="强制刷新清单")
    sp.set_defaults(func=cmd_versions)

    sp = sub.add_parser("install", help="下载安装 Minecraft 版本")
    sp.add_argument("version", help="版本号，如 1.21.4、1.8.9、b1.7.3")
    sp.add_argument("--force", action="store_true")
    sp.set_defaults(func=cmd_install)

    for kind, kw in (("fabric", "loader"), ("quilt", "loader"), ("forge", "version"), ("neoforge", "version")):
        sp = sub.add_parser(f"install-{kind}", help=f"安装 {kind.capitalize()} 加载器")
        sp.add_argument("mc", help="Minecraft 版本")
        sp.add_argument(f"--{kw}", dest=kw, help=f"{kind} 版本（默认最新）")
        sp.add_argument("--force", action="store_true")
        sp.set_defaults(func=lambda a, k=kind: _install_loader_cmd(a, k))

    sp = sub.add_parser("uninstall", help="卸载版本")
    sp.add_argument("version")
    sp.set_defaults(func=cmd_uninstall)

    sp = sub.add_parser("launch", help="启动游戏")
    sp.add_argument("version", help="要启动的版本")
    sp.add_argument("--username", "-u", help="游戏内名字（离线模式）")
    sp.add_argument("--offline", action="store_true", help="强制离线模式")
    sp.add_argument("--account", help="使用已登录的正版账号")
    sp.add_argument("--java", help="指定 Java 可执行文件路径")
    sp.add_argument("--memory", type=int, help="最大内存 MB")
    sp.add_argument("--width", type=int, help="窗口宽度")
    sp.add_argument("--height", type=int, help="窗口高度")
    sp.set_defaults(func=cmd_launch)

    sp = sub.add_parser("list", help="查看实例与已安装版本")
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("java", help="Java 管理")
    sp.add_argument("java_cmd", choices=["list", "install"])
    sp.add_argument("major", nargs="?", help="大版本号，如 17")
    sp.set_defaults(func=cmd_java)

    sp = sub.add_parser("login", help="微软正版登录")
    sp.add_argument("--client-id", help="自定义 OAuth 客户端 ID")
    sp.set_defaults(func=cmd_login)

    sp = sub.add_parser("search", help="搜索整合包（Modrinth / CurseForge / 中文）")
    sp.add_argument("query")
    sp.add_argument("--limit", type=int, default=25)
    sp.add_argument("--source", choices=["modrinth", "curseforge", "chinese"], default="modrinth",
                    help="搜索来源（默认 modrinth，chinese 支持中文别名查找）")
    sp.set_defaults(func=cmd_search)

    sp = sub.add_parser("modpack", help="安装整合包（.mrpack / CurseForge zip / 直链）")
    sp.add_argument("source", help="本地文件路径或下载链接")
    sp.set_defaults(func=cmd_modpack)

    sp = sub.add_parser("mods", help="模组管理（搜索/安装/列表）")
    mods_sub = sp.add_subparsers(dest="mods_cmd")
    sp1 = mods_sub.add_parser("search", help="搜索模组（Modrinth / CurseForge）")
    sp1.add_argument("query")
    sp1.add_argument("--limit", type=int, default=30)
    sp1.add_argument("--source", choices=["modrinth", "curseforge", "chinese"], default="modrinth",
                     help="搜索来源（默认 modrinth，chinese 支持中文别名查找）")
    sp1.set_defaults(func=cmd_mods)
    sp2 = mods_sub.add_parser("install", help="安装模组（Modrinth slug/链接、CurseForge 链接、.jar 文件/直链）")
    sp2.add_argument("source")
    sp2.add_argument("--mc", help="MC 版本（默认自动检测实例版本）")
    sp2.add_argument("--loader", choices=["fabric", "forge", "neoforge", "quilt"], default=None,
                     help="加载器（默认自动检测）")
    sp2.set_defaults(func=cmd_mods)
    sp3 = mods_sub.add_parser("list", help="列出实例中已安装的模组")
    sp3.set_defaults(func=cmd_mods)

    sp = sub.add_parser("instance", help="实例管理（版本隔离）")
    sp.add_argument("inst_cmd", choices=["create", "delete", "rename", "list"])
    sp.add_argument("name", nargs="?", help="实例名")
    sp.add_argument("--new-name", help="重命名目标")
    sp.set_defaults(func=cmd_instance)

    sp = sub.add_parser("sysinfo", help="打印本机配置（反馈系统会附带这些信息）")
    sp.set_defaults(func=cmd_sysinfo)

    sp = sub.add_parser("feedback", help="向反馈中心提交一条反馈")
    sp.add_argument("--category", default="other",
                    choices=["bug", "crash", "download", "multiplayer", "ai", "ui", "suggest", "other"])
    sp.add_argument("--title", default="")
    sp.add_argument("--body", default="")
    sp.add_argument("--contact", default="")
    sp.add_argument("--no-sysinfo", action="store_true")
    sp.add_argument("--i-agree", action="store_true", help="确认同意上传诊断数据")
    sp.set_defaults(func=cmd_feedback)

    return p


def gui_main():
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication
        from qfluentwidgets import Theme, setTheme, setThemeColor
        from app.main_window import MainWindow
        from app.pcl_chrome import PCL_GREEN
    except ImportError as exc:
        utils.log.error("Fluent UI 导入失败，回退旧界面: %s", exc)
        from gui import PyMCLApp
        PyMCLApp().run()
        return

    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    qt_app = QApplication(sys.argv)
    qt_app.setApplicationName("PyMCL")
    setTheme(Theme.LIGHT)
    setThemeColor(PCL_GREEN, save=False)
    from mclauncher.i18n import init_language
    init_language()
    window = MainWindow()

    def _ui_hook(kind, text, path):
        from PySide6.QtCore import QTimer
        from app.pages.crash_dialog import show_launcher_error
        QTimer.singleShot(0, lambda: show_launcher_error(window, kind, text, path))

    install_guard(ui_hook=_ui_hook)
    window.show()
    sys.exit(qt_app.exec())


def main(argv=None):
    install_guard()
    args = build_parser().parse_args(argv)
    if not args.command:
        try:
            gui_main()
        except Exception as e:
            utils.log.error("GUI 启动失败: %s", e)
            _exit(f"图形界面启动失败: {e}\n详情见 {utils.ROOT / 'pymcl-error.log'}")
        return
    try:
        args.func(args)
    except KeyboardInterrupt:
        print("\n已取消")
        sys.exit(130)


if __name__ == "__main__":
    main()
