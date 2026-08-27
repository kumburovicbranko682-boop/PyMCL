# -*- coding: utf-8 -*-
"""下载管理器：多线程、断点续传、哈希校验、进度回调、失败换源。"""
import hashlib
import os
import re
import shutil
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urljoin

from . import APP_NAME, APP_VERSION
from . import utils
from .download_status import DownloadTracker
from .mirrors import expand_download_urls
from .source import is_github_url

# requests + urllib3 推迟到 DownloadManager.__init__ 再 import：
# java/installer/launcher/mods/modpack 都在模块顶层引用 downloader，
# 这里 eager 拉 requests 会把整条链变重，GUI 冷启动白付几百毫秒。


class DownloadError(Exception):
    pass


CONNECT_TIMEOUT = 8.0
READ_TIMEOUT_DEFAULT = 90.0
MIN_FREE_PAD = 64 * 1024 * 1024


def _request_timeout(timeout):
    """连接超时短、读超时按文件；int 300 不再把握手也卡 300 秒。"""
    if isinstance(timeout, (tuple, list)) and len(timeout) >= 2:
        return (max(1.0, float(timeout[0])), max(1.0, float(timeout[1])))
    if timeout is None:
        return (CONNECT_TIMEOUT, READ_TIMEOUT_DEFAULT)
    t = float(timeout)
    return (min(CONNECT_TIMEOUT, max(2.0, t)), max(t, CONNECT_TIMEOUT))


def _free_bytes(path):
    try:
        p = Path(path)
        return shutil.disk_usage(p.anchor or str(p)).free
    except OSError:
        return None


def _looks_complete(path) -> bool:
    """无 sha1/size 时：拒绝空文件和 HTML 错误页，jar/zip 必须是 PK。"""
    p = Path(path)
    try:
        size = p.stat().st_size
    except OSError:
        return False
    if size < 16:
        return False
    try:
        with open(p, "rb") as f:
            head = f.read(32)
    except OSError:
        return False
    if p.suffix.lower() in (".jar", ".zip"):
        return head.startswith(b"PK")
    stripped = head.lstrip().lower()
    return not stripped.startswith((b"<html", b"<!doctype", b"error"))


def _is_redirect(resp) -> bool:
    return bool(getattr(resp, "is_redirect", False)) or resp.status_code in (301, 302, 303, 307, 308)


def _transport_dead(err) -> bool:
    """TLS/代理/握手挂了，换源比在同一条死链上重试有用。"""
    msg = str(err).lower()
    return any(token in msg for token in (
        "sslcertverificationerror",
        "certificate_verify_failed",
        "unable to get local issuer",
        "sslerror",
        "ssleoferror",
        "proxyerror",
        "newconnectionerror",
        "connecttimeouterror",
        "connection refused",
        "max retries exceeded",
    ))


def _should_switch_source(err) -> bool:
    """这类错误换下一个镜像，不要在死链上反复重试。"""
    msg = str(err)
    if "用户取消" in msg:
        return True
    if re.search(r"HTTP (403|404|408|409|410|429|5\d{2})\b", msg):
        return True
    return _transport_dead(err)


def _keep_part(err) -> bool:
    msg = str(err)
    return "用户取消" in msg or "下载不完整" in msg


def _enqueue_redirect(pending: deque, tried: set, location: str, depth: int):
    """Adoptium 等会 302 到 GitHub：这里展开国内镜像，避免直连 github.com 被本地代理证书拦死。"""
    nxt = expand_download_urls(location) if is_github_url(location) else [location]
    for item in reversed(nxt):
        if item and item not in tried:
            pending.appendleft((item, depth + 1))
    return nxt


# 全局“目标文件 -> 锁”注册表：任何 DownloadManager 下载同一文件时串行化，
# 防止并发任务写同一个 .part 文件互相打架（WinError 32 / 空壳文件）。
_PATH_LOCKS = {}
_PATH_LOCKS_GUARD = threading.Lock()


def _path_lock(dest) -> threading.Lock:
    key = os.path.abspath(str(dest))
    with _PATH_LOCKS_GUARD:
        lock = _PATH_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _PATH_LOCKS[key] = lock
        return lock


def _safe_unlink(path):
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        pass


def _safe_close(f):
    try:
        f.close()
    except OSError:
        pass


def _is_cancel(err) -> bool:
    if err is None:
        return False
    if err.__class__.__name__ == "TaskCancelled":
        return True
    return "用户取消" in str(err)


class DownloadManager:
    def __init__(self, threads: int = 8, on_progress=None, cancel=None, tracker=None):
        """
        threads:     并发下载线程数
        on_progress: 回调 (message, done, total)，从工作线程调用
        cancel:      回调 () -> bool，返回 True 时中止下载
        tracker:     DownloadTracker，供 GUI/CLI 读取速度与握手状态
        """
        self.threads = max(1, int(threads))
        self.on_progress = on_progress
        self.cancel = cancel or (lambda: False)
        self.tracker = tracker or DownloadTracker()
        self._lock = threading.Lock()
        self._done = 0
        self._last_notify = 0.0
        self._pace_lock = threading.Lock()
        self._paced = 0
        self._pace_t0 = time.monotonic()

        import requests
        from urllib3.util.retry import Retry
        self.session = requests.Session()
        from .net import apply_direct_to_session
        apply_direct_to_session(self.session)
        self.session.headers.update({
            "User-Agent": f"{APP_NAME}/{APP_VERSION} (python; +minecraft launcher)",
        })
        retry = Retry(
            total=1,
            connect=1,
            read=1,
            backoff_factor=0.2,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"],
            raise_on_status=False,
        )
        pool = max(16, self.threads)
        from .download_status import make_status_adapter
        adapter = make_status_adapter(
            self.tracker, max_retries=retry, pool_connections=pool, pool_maxsize=pool,
        )
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

    def _pace(self, n: int):
        try:
            from .config import CONFIG
            kbps = int(CONFIG.get("download_limit_kbps") or 0)
        except Exception:
            return
        if kbps <= 0 or n <= 0:
            return
        bps = kbps * 1024
        with self._pace_lock:
            self._paced += n
            elapsed = time.monotonic() - self._pace_t0
            expected = self._paced / bps
            delay = expected - elapsed
            if delay > 0.002:
                time.sleep(min(delay, 1.5))

    def _notify_progress(self, force=False):
        if not self.on_progress:
            return
        with self._lock:
            now = time.monotonic()
            if not force and now - self._last_notify < 0.15:
                return
            self._last_notify = now
        snap = self.tracker.snapshot()
        done = snap.bytes_done if snap.bytes_total else snap.files_done
        total = snap.bytes_total if snap.bytes_total else snap.files_total
        msg = snap.status_line or ""
        if snap.meta_line:
            msg = f"{msg}  |  {snap.meta_line}" if msg else snap.meta_line
        try:
            self.on_progress(msg, done, total)
        except Exception as e:
            if e.__class__.__name__ == "TaskCancelled":
                raise
            pass

    def _raise_if_cancel(self):
        if self.cancel():
            raise DownloadError("用户取消")

    # ------------------------------------------------------------ HTTP 基础

    def _extra_headers(self, url):
        """官方 CurseForge API 下载需要带 x-api-key。"""
        headers = {}
        if url and ("api.curseforge.com" in url or "/curseforge/v1/" in url):
            from .config import CONFIG
            key = CONFIG.get("curseforge_api_key")
            if key:
                headers["x-api-key"] = key
        return headers

    def _iter_urls(self, url, urls=None, expand=True):
        raw = []
        if url:
            raw.append(url)
        if urls:
            if isinstance(urls, (list, tuple)):
                raw.extend(urls)
            else:
                raw.append(urls)
        out, seen = [], set()
        for u in raw:
            extras = expand_download_urls(u) if expand else ([str(u)] if u else [])
            for e in extras:
                if e and e not in seen:
                    seen.add(e)
                    out.append(e)
        return out

    def _get_urls(self, url, expand=True):
        if expand:
            return self._iter_urls(url)
        return [str(url)] if url else []

    def fetch_json(self, url, timeout=(4, 15), expand=True, **kwargs):
        last_err = None
        for u in self._get_urls(url, expand=expand):
            self._raise_if_cancel()
            try:
                resp = self.session.get(u, timeout=_request_timeout(timeout), **kwargs)
                resp.raise_for_status()
                return resp.json()
            except Exception as e:
                last_err = e
                if _is_cancel(e):
                    raise DownloadError("用户取消") from e
                if u == url or "github" in (u or ""):
                    utils.log.warning("fetch_json 失败 %s: %s", u, e)
        if last_err:
            raise last_err
        raise DownloadError(f"fetch_json 失败: {url}")

    def fetch_text(self, url, timeout=(4, 15), expand=True, **kwargs):
        last_err = None
        for u in self._get_urls(url, expand=expand):
            self._raise_if_cancel()
            try:
                resp = self.session.get(u, timeout=_request_timeout(timeout), **kwargs)
                resp.raise_for_status()
                return resp.text
            except Exception as e:
                last_err = e
                if _is_cancel(e):
                    raise DownloadError("用户取消") from e
        if last_err:
            raise last_err
        raise DownloadError(f"fetch_text 失败: {url}")

    # ------------------------------------------------------------ 单文件下载

    def download(self, url, dest, sha1=None, size=None, force=False, timeout=300,
                 sha512=None, sha256=None, urls=None, expand=True) -> Path:
        """
        下载单个文件到 dest。url 可以是单个地址，urls 为额外候选（默认展开 GitHub 镜像）。
        expand=False 时不改写候选（陶瓦等已自带国内源）。
        403/404 会立刻换源。已有 .part 且带校验时按 Range 续传。
        """
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        key = str(dest)
        candidates = self._iter_urls(url, urls, expand=expand)
        if not candidates:
            raise DownloadError(f"没有可下载的地址: {dest.name}")
        if size:
            free = _free_bytes(dest)
            if free is not None and free < int(size) + (8 * 1024 * 1024):
                raise DownloadError(
                    f"磁盘空间不足：需要 {utils.format_size(size)}，剩余 {utils.format_size(free)}"
                )
        self.tracker.start_file(key, dest.name, candidates[0], size)
        self._notify_progress(force=True)
        try:
            with _path_lock(dest):
                result, skipped = self._download_locked(
                    candidates, dest, sha1, size, force, timeout, sha512, sha256, key,
                )
            self.tracker.finish_file(
                key, size or (result.stat().st_size if result.is_file() else None),
                skipped=skipped,
            )
            self._notify_progress(force=True)
            return result
        except Exception as e:
            self.tracker.fail_file(key, e)
            self._notify_progress(force=True)
            raise

    def _download_locked(self, urls, dest, sha1, size, force, timeout, sha512, sha256, key):
        if not force:
            if sha1 or sha256 or size is not None:
                if utils.file_matches(dest, sha1, size, sha256=sha256):
                    if not sha512 or utils.sha512_file(dest).lower() == str(sha512).lower():
                        return dest, True
            elif dest.is_file() and _looks_complete(dest):
                return dest, True

        part = dest.with_name(dest.name + ".part")
        can_resume = bool(sha1 or sha256 or sha512 or size is not None)
        req_timeout = _request_timeout(timeout)
        last_err = None
        pending = deque((u, 0) for u in urls if u)
        tried = set()
        while pending:
            self._raise_if_cancel()
            url, depth = pending.popleft()
            if url in tried:
                continue
            if depth > 8:
                last_err = DownloadError(f"重定向过多: {url}")
                continue
            tried.add(url)
            fatal = False
            redirected = False
            for attempt in range(2):
                self._raise_if_cancel()
                try:
                    self.tracker.reset_connect()
                    headers = self._extra_headers(url)
                    headers["Accept-Encoding"] = "identity"
                    have = 0
                    if can_resume and part.is_file():
                        try:
                            have = part.stat().st_size
                        except OSError:
                            have = 0
                    if have > 0:
                        headers["Range"] = f"bytes={have}-"
                    with self.session.get(
                        url, stream=True, timeout=req_timeout, headers=headers,
                        allow_redirects=False,
                    ) as resp:
                        if _is_redirect(resp):
                            loc = urljoin(url, resp.headers.get("Location") or "")
                            if not loc:
                                raise DownloadError(f"重定向无 Location: {url}")
                            nxt = _enqueue_redirect(pending, tried, loc, depth)
                            utils.log.info(
                                "下载重定向 %s -> %s（候选 %d）", url, loc, len(nxt),
                            )
                            redirected = True
                            break
                        if not self.tracker.did_connect():
                            self.tracker.reuse()
                        code = resp.status_code
                        if code == 416:
                            _safe_unlink(part)
                            raise DownloadError(f"HTTP 416: {url}")
                        if code in (403, 404, 408, 409, 410, 429) or code >= 500:
                            raise DownloadError(f"HTTP {code}: {url}")
                        resume = code == 206 and have > 0
                        if code == 200:
                            have = 0
                        elif code != 206:
                            resp.raise_for_status()
                        try:
                            cl_n = int(resp.headers.get("Content-Length") or 0)
                        except (TypeError, ValueError):
                            cl_n = 0
                        if resume:
                            expected = have + cl_n if cl_n else int(size or 0)
                        else:
                            expected = cl_n or int(size or 0)
                        self.tracker.http_ok(resp.status_code, expected, key)
                        self._notify_progress(force=True)

                        hasher_sha1 = hashlib.sha1() if sha1 else None
                        hasher_sha256 = hashlib.sha256() if sha256 else None
                        hasher_sha512 = hashlib.sha512() if sha512 else None
                        if resume and (hasher_sha1 or hasher_sha256 or hasher_sha512):
                            with open(part, "rb") as rf:
                                for buf in iter(lambda: rf.read(1024 * 1024), b""):
                                    if hasher_sha1:
                                        hasher_sha1.update(buf)
                                    if hasher_sha256:
                                        hasher_sha256.update(buf)
                                    if hasher_sha512:
                                        hasher_sha512.update(buf)

                        with open(part, "ab" if resume else "wb") as f:
                            got = have
                            for chunk in resp.iter_content(chunk_size=64 * 1024):
                                if self.cancel():
                                    _safe_close(f)
                                    raise DownloadError("用户取消")
                                if chunk:
                                    f.write(chunk)
                                    if hasher_sha1:
                                        hasher_sha1.update(chunk)
                                    if hasher_sha256:
                                        hasher_sha256.update(chunk)
                                    if hasher_sha512:
                                        hasher_sha512.update(chunk)
                                    got += len(chunk)
                                    self.tracker.transfer(key, got, expected)
                                    self._notify_progress()
                                    self._pace(len(chunk))
                        if expected and got != expected and not (sha1 or sha256 or sha512):
                            raise DownloadError(f"下载不完整 {url} ({got}/{expected})")
                    self.tracker.verify(dest.name)
                    self._notify_progress(force=True)
                    if hasher_sha1:
                        if hasher_sha1.hexdigest() != str(sha1).lower():
                            raise DownloadError(f"校验失败: {url} (期望 sha1={sha1}, size={size})")
                    elif sha1 or sha256 or size is not None:
                        if not utils.file_matches(part, sha1, size, sha256=sha256):
                            raise DownloadError(f"校验失败: {url} (期望 sha1={sha1}, size={size})")
                    if hasher_sha256:
                        if hasher_sha256.hexdigest() != str(sha256).lower():
                            raise DownloadError(f"sha256 校验失败: {url}")
                    elif sha256 and utils.sha256_file(part).lower() != str(sha256).lower():
                        raise DownloadError(f"sha256 校验失败: {url}")
                    elif not _looks_complete(part):
                        raise DownloadError(f"下载内容无效: {url}")
                    if hasher_sha512:
                        if hasher_sha512.hexdigest() != sha512.lower():
                            raise DownloadError(f"sha512 校验失败: {url}")
                    elif sha512 and utils.sha512_file(part) != sha512.lower():
                        raise DownloadError(f"sha512 校验失败: {url}")
                    os.replace(part, dest)
                    return dest, False
                except DownloadError as e:
                    last_err = e
                    if not _keep_part(e):
                        _safe_unlink(part)
                    if _is_cancel(e) or _should_switch_source(e):
                        fatal = True
                        break
                    time.sleep(1.0 * (attempt + 1))
                except Exception as e:
                    last_err = e
                    if _is_cancel(e):
                        raise DownloadError("用户取消") from e
                    if not can_resume:
                        _safe_unlink(part)
                    if _transport_dead(e):
                        fatal = True
                        break
                    time.sleep(1.0 * (attempt + 1))
            if redirected:
                continue
            if fatal and last_err and _is_cancel(last_err):
                raise last_err if isinstance(last_err, DownloadError) else DownloadError("用户取消")
            if last_err:
                utils.log.warning("下载源失败，换下一个: %s", last_err)
        raise DownloadError(f"下载失败 {dest.name}: {last_err}")

    # ------------------------------------------------------------ 批量下载

    def download_all(self, tasks, message="下载中"):
        """
        tasks: [(url, dest, sha1, size), ...] 或 5 元组带 sha512。
        url 可以是单个地址或候选列表。同一 dest 会合并镜像。
        全部完成后返回；用户取消抛出 DownloadError("用户取消")。
        """
        merged = {}
        order = []
        for raw in tasks:
            url, dest, sha1, size = raw[0], raw[1], raw[2], raw[3]
            sha512 = raw[4] if len(raw) > 4 else None
            dest = Path(dest)
            key = os.path.abspath(str(dest))
            urls = list(url) if isinstance(url, (list, tuple)) else [url]
            if key not in merged:
                merged[key] = [urls, dest, sha1, size, sha512]
                order.append(key)
            else:
                seen = set(merged[key][0])
                for u in urls:
                    if u and u not in seen:
                        merged[key][0].append(u)
                        seen.add(u)
                if merged[key][2] is None:
                    merged[key][2] = sha1
                if merged[key][3] is None:
                    merged[key][3] = size
                if merged[key][4] is None:
                    merged[key][4] = sha512
        tasks = [tuple(merged[k]) for k in order]
        total = len(tasks)
        errors = []
        self._done = 0
        total_bytes = sum(int(t[3]) for t in tasks if t[3])
        if total_bytes:
            free = _free_bytes(tasks[0][1]) if tasks else None
            if free is not None and free < total_bytes + MIN_FREE_PAD:
                raise DownloadError(
                    f"磁盘空间不足：需要 {utils.format_size(total_bytes)}，剩余 {utils.format_size(free)}"
                )
        self.tracker.begin_batch(message, total, total_bytes)
        self._notify_progress(force=True)
        cancelled = False
        try:
            with ThreadPoolExecutor(max_workers=self.threads) as pool:
                futures = {pool.submit(self._task_download, t): t for t in tasks}
                for fut in as_completed(futures):
                    dest = futures[fut][1]
                    try:
                        fut.result()
                    except Exception as e:
                        if _is_cancel(e):
                            cancelled = True
                        else:
                            errors.append(f"{Path(dest).name}: {e}")
                    if cancelled or self.cancel():
                        cancelled = True
                        for other in futures:
                            other.cancel()
                    with self._lock:
                        self._done += 1
                    self._notify_progress(force=True)
            if cancelled:
                self.tracker.end_batch(ok=False, message="已取消")
                raise DownloadError("用户取消")
            if errors:
                self.tracker.end_batch(ok=False, message=f"{message}失败 {len(errors)}/{total}")
                raise DownloadError(f"{message}失败（{len(errors)}/{total} 个文件）: {'; '.join(errors[:8])}")
            self.tracker.end_batch(ok=True, message=f"{message}完成")
            self._notify_progress(force=True)
            return True
        except DownloadError:
            raise
        except Exception:
            self.tracker.end_batch(ok=False)
            raise

    def _task_download(self, task):
        url, dest, sha1, size = task[0], task[1], task[2], task[3]
        sha512 = task[4] if len(task) > 4 else None
        if isinstance(url, (list, tuple)):
            first = url[0] if url else None
            return self.download(first, dest, sha1=sha1, size=size, sha512=sha512, urls=url)
        return self.download(url, dest, sha1=sha1, size=size, sha512=sha512)

    # ------------------------------------------------------------ 解压

    @staticmethod
    def extract_zip(zip_path, dest):
        utils.ensure_dir(dest)
        utils.safe_extract_zip(zip_path, dest)

    @staticmethod
    def extract_targz(path, dest):
        utils.ensure_dir(dest)
        utils.safe_extract_targz(path, dest)

    @staticmethod
    def extract_archive(path, dest):
        p = Path(path)
        name = p.name.lower()
        if name.endswith(".zip") or name.endswith(".jar"):
            DownloadManager.extract_zip(p, dest)
        elif name.endswith((".tar.gz", ".tgz")):
            DownloadManager.extract_targz(p, dest)
        else:
            raise DownloadError(f"不支持的压缩格式: {p.name}")

    @staticmethod
    def extract_jar_natives(jar_path, dest, exclude=None, skip_names=None):
        """把 natives jar 解压到目录，支持 extract.exclude 规则。

        skip_names: 文件名（小写）包含任一子串则跳过——HMCL「使用系统
        GLFW/OpenAL」靠不解压捆绑库、让 JVM 回落系统库实现。
        """
        import zipfile

        dest = utils.ensure_dir(dest)
        exclude = exclude or []
        skip_names = [s.lower() for s in (skip_names or [])]
        with zipfile.ZipFile(jar_path) as zf:
            for info in zf.infolist():
                name = info.filename
                if info.is_dir():
                    continue
                if any(name.startswith(prefix) for prefix in exclude):
                    continue
                base = os.path.basename(name).lower()
                if any(s in base for s in skip_names):
                    continue
                target = (dest / name).resolve()
                if not str(target).startswith(str(dest.resolve()) + os.sep):
                    raise DownloadError(f"压缩包包含非法路径: {name}")
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, open(target, "wb") as out:
                    while True:
                        buf = src.read(256 * 1024)
                        if not buf:
                            break
                        out.write(buf)
