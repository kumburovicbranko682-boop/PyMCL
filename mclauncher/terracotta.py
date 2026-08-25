# -*- coding: utf-8 -*-
"""陶瓦联机：下载官方内核，通过 --hmcl HTTP API 开房 / 加入。

内核是 burningtnt/Terracotta 的未修改官方二进制（AGPL，允许打包二进制 + HTTP 交互）。
"""
from __future__ import annotations

import atexit
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urlencode, urlparse

from . import utils


def _rq():
    """requests 延迟到真正发 HTTP 时再 import（GUI 启动路径不背这几十毫秒）。"""
    import requests
    return requests

VERSION = "0.4.2"
NODE_LIST_URL = "https://terracotta.glavo.site/nodes"
# HMCL 内置清单：https://raw.githubusercontent.com/HMCL-dev/HMCL/main/HMCL/src/main/resources/assets/terracotta.json
METADATA_URL = (
    "https://raw.githubusercontent.com/HMCL-dev/HMCL/main/"
    "HMCL/src/main/resources/assets/terracotta.json"
)
HOME = "https://github.com/burningtnt/Terracotta"
COPYRIGHT = "Terracotta | 陶瓦联机  © burningtnt  ·  基于 EasyTier"

# 与 burningtnt/Terracotta v0.4.2 src/controller/rooms/scaffolding/room.rs 一致
_ROOM_CHARS = "0123456789ABCDEFGHJKLMNPQRSTUVWXYZ"
_ROOM_WIDTH = len("U/XXXX-XXXX-XXXX-XXXX")
_ROOM_BODY = "XXXX-XXXX-XXXX-XXXX"

# SHA-512，与 HMCL terracotta.json 0.4.2 一致
_PACKAGES = {
    "windows-x86_64": {
        "hash": "6a98f524d4f00373696517306af8aa50d01d55ce4eadb27e9e4bc2f882707a0b5f20d5d4c33371d1459dcf5bf144ffed9beb414202d9ccf32b11dbbfcf19d650",
        "files": {
            "VCRUNTIME140.DLL": "3d4b24061f72c0e957c7b04a0c4098c94c8f1afb4a7e159850b9939c7210d73398be6f27b5ab85073b4e8c999816e7804fef0f6115c39cd061f4aaeb4dcda8cf",
            "terracotta-0.4.2-windows-x86_64.exe": "6e98d1f2380ed22fb5a2dd4aafce6c773e9cf69100c8bb8e49e7d6983756bdb9a31f80e06bcfbe5a2742144fe806d3d687dec54d8f09d87c659341f99dd9fd80",
        },
        "exe": "terracotta-0.4.2-windows-x86_64.exe",
    },
    "windows-arm64": {
        "hash": "fc1077247014ac0c712469498bde2ef7f6d881d5fcb7bdd5e11ebe20218fed365be19afdb8d453a79d77b729f866058522b910741767f4df947faa891434b463",
        "files": {
            "VCRUNTIME140.DLL": "5cb5ce114614101d260f4754c09e8a0dd57e4da885ebb96b91e274326f3e1dd95ed0ade9f542f1922fad0ed025e88a1f368e791e1d01fae69718f0ec3c7b98c8",
            "terracotta-0.4.2-windows-arm64.exe": "30a15c5c53e5817c5a3634532172559327474741d3b2c7ef4e8a30acc6f59cdcf3570bf5f583e3cbe9e2abc8253e977c1abda1e9f36c88c4e99240da257347d0",
        },
        "exe": "terracotta-0.4.2-windows-arm64.exe",
    },
    "linux-x86_64": {
        "hash": "d326ad95815d04568d485b5038e40ffc47ca54292fa0925eee6f5cea014024f901d661708aac2a743037b990882ad82b4d0b7bb03dc3b2fe720dbf0f3efe1c98",
        "files": {
            "terracotta-0.4.2-linux-x86_64": "fac328ba8957a711b03557bb913940f22d61b76608cd203fdf51024b6f94b19f5bc91c9b8a9fa80baf6968e1e6873c1880fd4cf54a2f8e3c6cf1e6ac161f8d0c",
        },
        "exe": "terracotta-0.4.2-linux-x86_64",
    },
    "linux-arm64": {
        "hash": "57c08f48d9535e93ad547d2dfc852d267992cc164a7208b42a2da0a6cbc2f21862f610e02a746b4b67150f4dec26b86a4f96eb9bd2f58d124d5b40ba50c6d55e",
        "files": {
            "terracotta-0.4.2-linux-arm64": "d807744c2041c98686e4b505324713badea7a0f31e8810be49ae053a63fb6dfc474ac58d678fb93eea0dd5cccff7372d9ec6135a1046f4b306cad35cd90ecacd",
        },
        "exe": "terracotta-0.4.2-linux-arm64",
    },
    "macos-x86_64": {
        "hash": "a762e4b2d6f84e899292b9e3856d009411a516d3c47f54575f843ce082f63dff2baa68ba0faa844b8b64fb12e91017386f15f5e7f975f8ee605bf8d4217cb091",
        "files": {
            "terracotta-0.4.2-macos-x86_64": "24efb85390eff88a538ed7e503fb1488e5e622730ca30c741a0e8b4c8f4e8d4868a2f9f38da8de540aeb535af2fd1e41c7081dae9c700e8a1a03b6c540218164",
        },
        "exe": "terracotta-0.4.2-macos-x86_64",
    },
    "macos-arm64": {
        "hash": "09e444fea2d9fd19f3e5cb62e29055228345be163924cbd408d947646fafed1012cf48508ee6a155ede3d571e2ffaa72d09ceeb1493c8a60feb05e0699f19ba3",
        "files": {
            "terracotta-0.4.2-macos-arm64": "8e59a9d78acd57702dc044d6f2799c6af586b075a262f7d4dbbf0876e1af8d8271e04783c24ff820b801e3b14cd0190ab8403d097f3f2d98b6d911f95ed1e972",
        },
        "exe": "terracotta-0.4.2-macos-arm64",
    },
}

# 文案与 HMCL I18N_zh_CN.properties 陶瓦条目对齐
_EXC = (
    "加入房间失败：找不到房主。房间已关闭，或尚未连上公共中继",
    "房间连接断开：房间已关闭或网络不稳定",
    "加入房间失败：EasyTier 已崩溃，请向开发者反馈该问题",
    "创建房间失败：EasyTier 已崩溃，请向开发者反馈该问题",
    "房间已关闭：您已退出游戏世界，房间已自动关闭",
    "协议错误：房主发送了错误的响应数据，请向开发者反馈该问题",
)

_PING_HOST_HINT = (
    "陶瓦是 EasyTier P2P 打洞，不是 FRP 隧道。官方公共节点连不上时，必须和 HMCL 用同一条自定义会合节点。"
    "请完全退出后重试；已带上本机 HMCL 成功加入时用的那条 terracotta.glavo.site 节点。"
)

_DIFF = {
    "EASIEST": "当前网络状态极好：稍等一下就成功！",
    "SIMPLE": "当前网络状态较好：建立连接需要一段时间……",
    "MEDIUM": "当前网络状态中等：已启用抗干扰备用线路，连接可能失败",
    "TOUGH": "当前网络状态极差：已启用抗干扰备用线路，连接可能失败",
}

_STATE_LABEL = {
    "missing": "未下载联机核心",
    "unsupported": "当前系统架构暂不支持陶瓦联机",
    "installing": "正在下载联机核心…",
    "launching": "正在初始化联机核心",
    "unknown": "正在初始化联机核心",
    "waiting": "联机核心已就绪",
    "host-scanning": "正在扫描局域网世界",
    "host-starting": "正在启动房间",
    "host-ok": "已启动房间",
    "guest-connecting": "正在加入房间",
    "guest-starting": "正在加入房间",
    "guest-ok": "已加入房间",
    "exception": "联机出错",
    "fatal": "联机内核已停止",
}

# 与官方 FakeServer MOTD / HMCL guest_ok 一致：多人游戏里双击这个名字
LOBBY_NAME = "陶瓦联机大厅"
_last_lobby = ""

_lock = threading.Lock()
_proc = None
_port = 0
_nodes = None
_allowed_fw = set()
# Windows 上 --hmcl 会再拉起 --hmcl2 后自己退出 0，端口文件才是内核就绪的标志。


class TerracottaError(Exception):
    pass


def _lookup_room_char(char: str):
    if char == "I":
        char = "1"
    elif char == "O":
        char = "0"
    idx = _ROOM_CHARS.find(char)
    return idx if idx >= 0 else None


def _room_from_value(value: int) -> str:
    code = ["U/"]
    for i in range(16):
        ch = _ROOM_CHARS[value % 34]
        value //= 34
        if i == 4 or i == 8 or i == 12:
            code.append("-")
        code.append(ch)
    return "".join(code)


def parse_room(text: str) -> str | None:
    """官方 Room::from / scaffolding::parse：滑动窗口找 U/XXXX-XXXX-XXXX-XXXX，种子须能被 7 整除。"""
    chars = list((text or "").upper())
    if len(chars) < _ROOM_WIDTH:
        return None
    body_len = len(_ROOM_BODY)
    for start in range(0, len(chars) - _ROOM_WIDTH + 1):
        window = chars[start:start + _ROOM_WIDTH]
        if window[0] != "U" or window[1] != "/":
            continue
        body = window[2:]
        value = 0
        ok = True
        for i in range(body_len - 1, -1, -1):
            if i in (4, 9, 14):
                if body[i] != "-":
                    ok = False
                    break
                continue
            digit = _lookup_room_char(body[i])
            if digit is None:
                ok = False
                break
            value = value * 34 + digit
        if ok and value % 7 == 0:
            return _room_from_value(value)
    return None


def looks_like_legacy_room(text: str) -> bool:
    """v0.3.14 TerracottaLegacy：XXXXX-XXXXX-XXXXX-XXXXX-XXXXX（已从 0.4.2 移除）。"""
    chars = list((text or "").upper())
    if len(chars) < 29:
        return False

    def lookup(char: str):
        if char == "I":
            char = "1"
        elif char == "O":
            char = "0"
        idx = _ROOM_CHARS.find(char)
        return idx if idx >= 0 else None

    for start in range(0, len(chars) - 28):
        seg = chars[start:start + 29]
        array = []
        good = True
        for i in range(5):
            for j in range(5):
                v = lookup(seg[i * 6 + j])
                if v is None:
                    good = False
                    break
                array.append(v)
            if not good:
                break
            if i != 4 and seg[i * 6 + 5] != "-":
                good = False
                break
        if not good or len(array) != 25:
            continue
        checking = 0
        for i in range(24):
            checking = (checking + array[i]) % 34
        if checking == array[24]:
            return True
    return False


def looks_like_pcl2ce_room(text: str) -> bool:
    """v0.3.14 PCL2CE：不超过 10 个字符（已从 0.4.2 移除）。"""
    chars = list((text or "").strip().upper())
    if not chars or len(chars) > 10:
        return False
    value = 0
    for char in chars:
        if "2" <= char <= "9":
            value = value * 32 + (ord(char) - ord("2"))
        elif "A" <= char <= "H":
            value = value * 32 + (ord(char) - ord("A") + 8)
        elif "J" <= char <= "N":
            value = value * 32 + (ord(char) - ord("J") + 16)
        elif "P" <= char <= "Z":
            value = value * 32 + (ord(char) - ord("P") + 21)
        else:
            return False
    if value >= 99_999_999_99_65536:
        return False
    s = str(value)
    if len(s) == 14:
        return True
    if len(s) == 15:
        return (value % 100000) < 65536
    return False


def room_error(text: str) -> str:
    raw = (text or "").strip()
    if not raw:
        return "请输入房间号。"
    if looks_like_legacy_room(raw):
        return (
            "这是陶瓦旧版房间号。官方 0.4.2 已移除 TerracottaLegacy 格式，"
            "请让房主用当前陶瓦 / HMCL 重新开房（房间号以 U/ 开头）。"
        )
    if looks_like_pcl2ce_room(raw):
        return (
            "这是 PCL CE 旧房间号。官方 0.4.2 已移除 PCL2CE 格式，"
            "请双方都用当前陶瓦 / HMCL 开房，房间号形如 U/XXXX-XXXX-XXXX-XXXX。"
        )
    if "U/" in raw.upper() or raw.upper().startswith("U"):
        return "房间号校验失败。请向房主重新复制完整的 U/XXXX-XXXX-XXXX-XXXX。"
    return (
        "房间号须为陶瓦 0.4.2 格式 U/XXXX-XXXX-XXXX-XXXX。"
        "官方内核已不再接受旧版 PCL / 旧陶瓦房间号。"
    )


def classifier() -> str:
    osn = utils.OS_NAME
    arch = utils.ARCH
    if osn == "windows":
        return "windows-x86_64" if arch != "arm64" else "windows-arm64"
    if osn == "osx":
        return "macos-arm64" if arch == "arm64" else "macos-x86_64"
    if osn == "linux":
        return "linux-arm64" if arch == "arm64" else "linux-x86_64"
    return ""


def package_meta() -> dict:
    return dict(_PACKAGES.get(classifier()) or {})


def install_dir() -> Path:
    return utils.ROOT / "terracotta" / VERSION


def executable() -> Path:
    meta = package_meta()
    if not meta:
        raise TerracottaError("当前系统架构暂不支持陶瓦联机。")
    return install_dir() / meta["exe"]


def _short_path(path: Path) -> str:
    text = str(Path(path).resolve())
    if not utils.IS_WINDOWS:
        return text
    import ctypes
    buf = ctypes.create_unicode_buffer(520)
    n = ctypes.windll.kernel32.GetShortPathNameW(text, buf, 520)
    return buf.value if n else text


def firewall_programs() -> list[Path]:
    """陶瓦本体 + 临时目录里解压出的 EasyTier。"""
    found = []
    seen = set()

    def add(path: Path):
        try:
            path = path.resolve()
        except OSError:
            return
        if not path.is_file():
            return
        key = str(path).lower()
        if key in seen:
            return
        seen.add(key)
        found.append(path)

    try:
        add(executable())
    except TerracottaError:
        pass
    tmp = Path(tempfile.gettempdir()) / "terracotta"
    if tmp.is_dir():
        for path in tmp.rglob("*.exe"):
            name = path.name.lower()
            if "easytier" in name or "terracotta" in name:
                add(path)
    return found


def open_firewall_settings():
    if not utils.IS_WINDOWS:
        return
    try:
        subprocess.Popen(["control", "firewall.cpl"], close_fds=True)
    except OSError:
        pass


def allow_firewall() -> str:
    """弹出 UAC，为陶瓦 / EasyTier 添加入站出站允许规则。"""
    if not utils.IS_WINDOWS:
        raise TerracottaError("当前系统请在系统防火墙里手动放行陶瓦联机。")
    programs = firewall_programs()
    if not programs:
        raise TerracottaError("还没找到陶瓦内核，请先点下载/启动，再允许防火墙。")
    lines = ["@echo off", "chcp 65001 >nul"]
    for index, path in enumerate(programs):
        prog = _short_path(path)
        for direction in ("in", "out"):
            name = f"PyMCL Terracotta {index} {direction}"
            lines.append(f'netsh advfirewall firewall delete rule name="{name}" >nul 2>nul')
            lines.append(
                f'netsh advfirewall firewall add rule name="{name}" dir={direction} '
                f'action=allow program="{prog}" enable=yes profile=any'
            )
    lines.append(
        'netsh advfirewall firewall delete rule name="PyMCL Terracotta ICMPv4" >nul 2>nul'
    )
    lines.append(
        'netsh advfirewall firewall add rule name="PyMCL Terracotta ICMPv4" '
        "protocol=icmpv4:8,any dir=in action=allow enable=yes profile=any"
    )
    lines.append("exit /b 0")
    bat = utils.ROOT / "terracotta" / "allow-firewall.bat"
    utils.ensure_dir(bat.parent)
    bat.write_text("\r\n".join(lines) + "\r\n", encoding="gbk", errors="replace")
    cmd = (
        "Start-Process -FilePath " + repr(str(bat)) +
        " -Verb RunAs -Wait -WindowStyle Hidden"
    )
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", cmd],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        if "canceled" in err.lower() or "1223" in err or proc.returncode == 1223:
            raise TerracottaError("已取消管理员授权，防火墙规则没有写入。")
        raise TerracottaError(err or "写入防火墙规则失败，请在 UAC 窗口点「是」。")
    global _allowed_fw
    _allowed_fw = {str(p.resolve()).lower() for p in programs}
    names = "、".join(p.name for p in programs[:4])
    return f"已允许 {names} 通过防火墙。若装着电脑管家/360/火绒，还要在它们里面放行。"


def is_installed() -> bool:
    meta = package_meta()
    if not meta:
        return False
    root = install_dir()
    for name in meta["files"]:
        path = root / name
        if not path.is_file():
            return False
    return executable().is_file()


def download_urls() -> list[str]:
    """只返回 HMCL terracotta.json 的 downloads_CN + downloads，不附加其它代理。"""
    cls = classifier()
    name = f"terracotta-{VERSION}-{cls}-pkg.tar.gz"
    return [
        f"https://gitee.com/burningtnt/Terracotta/releases/download/v{VERSION}/{name}",
        f"https://cnb.cool/HMCL-Terracotta/Terracotta/-/releases/download/v{VERSION}/{name}",
        f"https://alist.8mi.tech/d/mirror/HMCL-Terracotta/Auto/v{VERSION}/{name}",
        f"https://github.com/burningtnt/Terracotta/releases/download/v{VERSION}/{name}",
    ]


def _extract_named(archive: Path, dest_dir: Path, files: dict):
    dest_dir.mkdir(parents=True, exist_ok=True)
    wanted = {name.lower(): name for name in files}
    found = set()
    with tarfile.open(archive, "r:gz") as tf:
        for member in tf.getmembers():
            if not member.isfile():
                continue
            base = Path(member.name).name
            real = wanted.get(base.lower())
            if not real:
                continue
            out = dest_dir / real
            src = tf.extractfile(member)
            if src is None:
                continue
            with src, open(out, "wb") as fh:
                shutil.copyfileobj(src, fh)
            if not utils.IS_WINDOWS:
                os.chmod(out, 0o755)
            want = files[real].lower()
            got = utils.sha512_file(out)
            if got != want:
                raise TerracottaError(f"内核文件校验失败: {real}")
            found.add(real)
    missing = set(files) - found
    if missing:
        raise TerracottaError("安装包不完整，缺少: " + "、".join(sorted(missing)))


def install(dm, log=None):
    """下载并解压官方平台包。dm 为 DownloadManager。"""
    meta = package_meta()
    if not meta:
        raise TerracottaError("当前系统架构暂不支持陶瓦联机。")
    if is_installed():
        if log:
            log("陶瓦联机内核已安装")
        return install_dir()
    write = log or (lambda *_: None)
    write(f"下载陶瓦联机 {VERSION}（{classifier()}）")
    cache = utils.ROOT / "cache" / "terracotta"
    utils.ensure_dir(cache)
    pkg = cache / f"terracotta-{VERSION}-{classifier()}-pkg.tar.gz"
    urls = download_urls()
    dm.download(urls[0], pkg, sha512=meta["hash"], urls=urls[1:], timeout=600, expand=False)
    write("正在解压内核…")
    root = install_dir()
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)
    try:
        _extract_named(pkg, root, meta["files"])
    except Exception:
        shutil.rmtree(root, ignore_errors=True)
        raise
    write(f"已安装到 {root}")
    return root


def _runtime_file() -> Path:
    return utils.ROOT / "terracotta" / "runtime.json"


def _save_port(port: int):
    global _port
    _port = int(port or 0)
    if _port:
        utils.write_json(_runtime_file(), {"port": _port})


def _load_port() -> int:
    data = utils.read_json(_runtime_file(), {}) or {}
    try:
        return int(data.get("port") or 0)
    except (TypeError, ValueError):
        return 0


def _http_ok(port: int, timeout=1.5) -> bool:
    if not port:
        return False
    try:
        resp = _rq().get(f"http://127.0.0.1:{int(port)}/state", timeout=timeout)
        return resp.status_code < 500
    except Exception:
        return False


def running() -> bool:
    global _port
    if _http_ok(_port):
        return True
    saved = _load_port()
    if saved and saved != _port and _http_ok(saved):
        _port = saved
        return True
    return False


def _is_china_mainland() -> bool:
    """对应 HMCL LocaleUtils.IS_CHINA_MAINLAND：本启动器默认按中国大陆处理。"""
    try:
        import locale
        tag = (locale.getdefaultlocale()[0] or "").replace("-", "_").lower()
    except Exception:
        tag = ""
    if not tag:
        return True
    if tag.startswith(("zh_tw", "zh_hk", "zh_mo")):
        return False
    return tag.startswith("zh") or tag.startswith("zh_cn")


def _valid_node_url(url: str) -> bool:
    """HMCL TerracottaNode.validate：必须能解析成 URI。"""
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    return bool(parsed.scheme and parsed.netloc)


# 本机 HMCL 2026-08-19 10:01 成功加入时多传的自定义节点（官方 /nodes 表没有）。
# GET 该地址返回 tcp://103.239.245.69:38867，EasyTier 用它做会合。
HMCL_CUSTOM_NODE = "https://terracotta.glavo.site/acebc7d8-1208-47fd-b212-d03ac49e36e0"

# 内核 fetch_public_nodes 还会再附带这 4 条（burningtnt/Terracotta v0.4.2 publics.rs）
KERNEL_DEFAULT_NODES = (
    "tcp://public.easytier.top:11010",
    "tcp://public2.easytier.cn:54321",
    "https://etnode.zkitefly.eu.org/node1",
    "https://etnode.zkitefly.eu.org/node2",
)


def _extra_nodes() -> list[str]:
    extra = [HMCL_CUSTOM_NODE]
    try:
        from .config import CONFIG
        configured = CONFIG.get("terracotta_extra_nodes") or []
        if isinstance(configured, str):
            configured = [configured]
        for item in configured:
            url = str(item or "").strip()
            if url:
                extra.append(url)
    except Exception:
        pass
    return extra


def public_nodes() -> list[str]:
    """HMCL TerracottaNodeList.fetch 的官方表，再加上 HMCL 实际用过的自定义节点。

    官方表 https://terracotta.glavo.site/nodes 只有 etnode1/2。
    本机 HMCL 能进、这里不能，是因为 HMCL 还传了 HMCL_CUSTOM_NODE。
    不展开、不改写成 IP，原样交给内核（与 HMCL NetworkUtils.withQuery 相同）。
    """
    global _nodes
    if _nodes is not None:
        return _nodes
    listed = []
    seen = set()

    def add(url: str):
        url = (url or "").strip()
        if url and url not in seen and _valid_node_url(url):
            seen.add(url)
            listed.append(url)

    for url in _extra_nodes():
        add(url)
    try:
        resp = _rq().get(NODE_LIST_URL, timeout=10)
        resp.raise_for_status()
        rows = resp.json()
        if isinstance(rows, list):
            mainland = _is_china_mainland()
            for row in rows:
                if not isinstance(row, dict):
                    continue
                url = (row.get("url") or "").strip()
                region = (row.get("region") or "").strip()
                if not url or not _valid_node_url(url):
                    continue
                region_cn = region.lower() == "cn"
                if region and mainland != region_cn:
                    continue
                add(url)
    except Exception:
        pass
    _nodes = listed
    return listed


def _http(path: str, params=None, timeout=4):
    if not _port:
        raise TerracottaError("联机内核尚未就绪。")
    q = []
    if params:
        for key, value in params:
            if value not in (None, ""):
                q.append((key, str(value)))
    url = f"http://127.0.0.1:{_port}{path}"
    if q:
        url += "?" + urlencode(q, doseq=True)
    resp = _rq().get(url, timeout=timeout)
    if resp.status_code == 400:
        raise TerracottaError(
            "联机内核拒绝了这次请求。房间号须为 U/XXXX-XXXX-XXXX-XXXX，且内核要处于就绪状态。"
        )
    resp.raise_for_status()
    text = resp.text or ""
    if text[:1] in "{[":
        try:
            return resp.json()
        except Exception:
            return text
    return text


def _read_port(path: Path):
    if not path.is_file():
        return 0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return int(data.get("port") or 0)
    except Exception:
        return 0


def _kernel_version(port: int) -> str:
    try:
        data = _rq().get(f"http://127.0.0.1:{int(port)}/meta", timeout=2).json()
        return str(data.get("version") or "")
    except Exception:
        return ""


def _peaceful_stop(port: int):
    try:
        _rq().get(f"http://127.0.0.1:{int(port)}/panic?peaceful=true", timeout=2)
    except Exception:
        pass


def _recover_waiting():
    """残留 exception 会让 /state/guesting 直接 400，先回到 waiting。"""
    if not _port:
        return
    try:
        data = _rq().get(f"http://127.0.0.1:{_port}/state", timeout=2).json()
        if str(data.get("state") or "") == "exception":
            _rq().get(f"http://127.0.0.1:{_port}/state/ide", timeout=2)
    except Exception:
        pass


def start(log=None):
    """拉起官方内核，等待它写入本地 HTTP 端口。"""
    global _proc, _port
    write = log or (lambda *_: None)
    with _lock:
        last_error = None
        for attempt in range(2):
            if running():
                ver = _kernel_version(_port)
                if ver and ver != VERSION and attempt == 0:
                    write(f"发现其它版本陶瓦内核 {ver}，正在切换到官方 {VERSION}")
                    _peaceful_stop(_port)
                    time.sleep(3)
                    _port = 0
                    continue
                _recover_waiting()
                write(f"联机内核已在运行（127.0.0.1:{_port}）")
                return _port
            exe = executable()
            if not exe.is_file():
                raise TerracottaError("请先安装陶瓦联机内核。")
            tmp = Path(tempfile.mkdtemp(prefix="pymcl-terracotta-"))
            marker = (tmp / "http").resolve()
            flags = 0
            if utils.IS_WINDOWS:
                flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            write(f"启动 {exe.name}")
            # Windows：--hmcl 会再拉起 --hmcl2 后台进程，自己在端口文件出现后以 0 退出。
            proc = subprocess.Popen(
                [str(exe), "--hmcl", str(marker)],
                cwd=str(exe.parent),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=flags,
            )
            _proc = proc
            deadline = time.time() + 40
            port = 0
            while time.time() < deadline:
                port = _read_port(marker)
                if port:
                    break
                code = proc.poll()
                if code is None:
                    time.sleep(0.15)
                    continue
                if code == 0:
                    for _ in range(10):
                        port = _read_port(marker)
                        if port:
                            break
                        time.sleep(0.2)
                    if port:
                        break
                    last_error = TerracottaError("陶瓦联机已退出，但没有写出端口文件。")
                    break
                last_error = TerracottaError(f"陶瓦联机进程提前退出，代码 {code}。")
                break
            if not port:
                try:
                    proc.kill()
                except Exception:
                    pass
                if last_error and attempt == 1:
                    raise last_error
                if not last_error:
                    last_error = TerracottaError("等待联机内核端口超时。")
                continue
            _save_port(port)
            ready_until = time.time() + 20
            while time.time() < ready_until and not _http_ok(port):
                time.sleep(0.2)
            if not _http_ok(port):
                raise TerracottaError(f"端口 {port} 已写出，但联机接口还没有响应。")
            ver = _kernel_version(port)
            if ver and ver != VERSION and attempt == 0:
                write(f"发现其它版本陶瓦内核 {ver}，正在切换到官方 {VERSION}")
                _peaceful_stop(port)
                time.sleep(3)
                _port = 0
                continue
            _proc = proc if proc.poll() is None else None
            _recover_waiting()
            write(f"联机内核已就绪（127.0.0.1:{port}）")
            return port
        raise last_error or TerracottaError("无法启动陶瓦联机内核。")


def stop():
    global _proc, _port
    port = _port or _load_port()
    if port:
        try:
            _rq().get(f"http://127.0.0.1:{int(port)}/panic?peaceful=true", timeout=2)
        except Exception:
            pass
    proc, _proc, _port = _proc, None, 0
    if proc and proc.poll() is None:
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
    try:
        _runtime_file().unlink(missing_ok=True)
    except OSError:
        pass


def _player_params(player: str, room: str | None = None):
    params = [("player", player or "Player")]
    if room:
        params.append(("room", room.strip()))
    for node in public_nodes():
        params.append(("public_nodes", node))
    return params


def set_waiting():
    return _http("/state/ide")


def set_scanning(player: str):
    _recover_waiting()
    return _http("/state/scanning", _player_params(player))


def set_guesting(room: str, player: str):
    room = (room or "").strip()
    if not room:
        raise TerracottaError("请输入邀请码。")
    if not parse_room(room):
        raise TerracottaError(room_error(room))
    # 和 HMCL 一样把原始邀请码交给内核解析，不改写成另一串。
    _recover_waiting()
    return _http("/state/guesting", _player_params(player, room))


def fetch_state() -> dict:
    if not running():
        return {}
    data = _http("/state")
    return data if isinstance(data, dict) else {}


def split_join_url(url: str) -> tuple[str, int]:
    """官方 guest-ok 的 url：127.0.0.1 或 127.0.0.1:端口。"""
    text = (url or "").strip()
    if not text:
        raise TerracottaError("还没有联机地址。")
    if "://" in text:
        text = text.split("://", 1)[1]
    if ":" in text:
        host, port_s = text.rsplit(":", 1)
        return (host or "127.0.0.1"), int(port_s)
    return text or "127.0.0.1", 25565


def _default_game_dir() -> Path:
    from .config import CONFIG
    from .instances import DEFAULT_INSTANCE_NAME, Instance
    return Instance(CONFIG.get("default_instance") or DEFAULT_INSTANCE_NAME).path


def remember_lobby(url: str, game_dir=None):
    """把官方大厅写进当前实例 servers.dat，多人列表里直接出现「陶瓦联机大厅」。"""
    global _last_lobby
    dest = Path(game_dir) if game_dir else _default_game_dir()
    key = f"{dest}|{url}"
    if _last_lobby == key:
        return dest
    write_lobby_server(dest, url)
    _last_lobby = key
    return dest


def write_lobby_server(game_dir, url: str) -> Path:
    """把大厅写进 servers.dat（游戏格式：未压缩 NBT），保留已有条目。"""
    from . import servers as servers_mod
    host, port = split_join_url(url)
    path = Path(game_dir) / "servers.dat"
    utils.ensure_dir(path.parent)
    entry = {"name": LOBBY_NAME, "ip": host, "port": port, "hidden": False}
    out = [entry]
    for row in servers_mod.read_servers_dat(path):
        same_addr = (str(row.get("ip") or "") == host
                     and int(row.get("port") or 25565) == int(port))
        if str(row.get("name") or "") == LOBBY_NAME or same_addr:
            continue
        out.append(row)
    servers_mod.write_servers_dat(path, out)
    return path


def snapshot(player: str = "Player", game_running: bool = False) -> dict:
    meta = package_meta()
    info = {
        "supported": bool(meta),
        "installed": is_installed(),
        "running": running(),
        "port": _port,
        "state": "missing",
        "label": _STATE_LABEL["missing"],
        "room": "",
        "url": "",
        "difficulty": "",
        "difficulty_hint": "",
        "profiles": [],
        "error": "",
        "player": player or "Player",
        "game_running": game_running,
        "copyright": COPYRIGHT,
        "home": HOME,
        "version": VERSION,
        "nodes": list(_nodes or []),
        "firewall_stale": False,
    }
    if not meta:
        info["state"] = "unsupported"
        info["label"] = _STATE_LABEL["unsupported"]
        return info
    if not info["installed"]:
        return info
    if not running():
        info["state"] = "idle"
        info["label"] = "内核已安装，打开本页会自动启动"
        return info
    try:
        data = fetch_state() or {}
    except Exception as exc:
        info["state"] = "fatal"
        info["label"] = _STATE_LABEL["fatal"]
        info["error"] = str(exc)
        return info
    raw = str(data.get("state") or "unknown")
    info["state"] = raw
    info["label"] = _STATE_LABEL.get(raw, raw)
    info["room"] = str(data.get("room") or data.get("code") or "")
    info["url"] = str(data.get("url") or "")
    diff = str(data.get("difficulty") or "").upper()
    info["difficulty"] = diff
    info["difficulty_hint"] = _DIFF.get(diff, "")
    profiles = data.get("profiles") or []
    rows = []
    if isinstance(profiles, list):
        for item in profiles:
            if not isinstance(item, dict):
                continue
            rows.append({
                "name": item.get("name") or "玩家",
                "vendor": item.get("vendor") or "",
                "kind": item.get("kind") or "",
            })
    info["profiles"] = rows
    if raw == "exception":
        try:
            typ = int(data.get("type") or 0)
        except (TypeError, ValueError):
            typ = 0
        info["error"] = _EXC[typ] if 0 <= typ < len(_EXC) else "联机出错"
        info["label"] = info["error"]
        if typ == 0:
            info["error_hint"] = _PING_HOST_HINT
    if raw == "guest-ok" and info["url"]:
        try:
            remember_lobby(info["url"])
        except Exception:
            pass
    stale = False
    for path in firewall_programs():
        if "easytier" in path.name.lower() and str(path.resolve()).lower() not in _allowed_fw:
            stale = True
            break
    info["firewall_stale"] = stale
    return info


atexit.register(stop)
