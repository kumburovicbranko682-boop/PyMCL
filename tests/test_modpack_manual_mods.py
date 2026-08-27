from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mclauncher.downloader import DownloadError
from mclauncher.modpack import download_pack_mods_tolerant


class _FakeDM:
    """download_all 假实现：按 dest 文件名决定成功/失败。"""

    def __init__(self, fail_names=(), raise_message="下载整合包 Mod失败", cancel=False):
        self.fail_names = set(fail_names)
        self.raise_message = raise_message
        self.cancel_flag = cancel

    def download_all(self, tasks, message="下载中"):
        if self.cancel_flag:
            raise DownloadError("用户取消")
        failed = []
        for task in tasks:
            dest = Path(task[1])
            if dest.name in self.fail_names:
                failed.append(dest.name)
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"jar")
        if failed:
            raise DownloadError(f"{self.raise_message}（{len(failed)}/{len(tasks)} 个文件）")
        return True


def _task(mods_dir: Path, filename: str):
    return (["https://x/" + filename], mods_dir / filename, None, None)


def _raw(pid, fid):
    return {"projectID": pid, "fileID": fid}


class TolerantPackModsTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.mods = Path(self.td.name) / "mods"

    def test_all_ok_returns_empty(self):
        tasks = [_task(self.mods, "a.jar"), _task(self.mods, "b.jar")]
        raws = [_raw(1, 11), _raw(2, 22)]
        out = download_pack_mods_tolerant(_FakeDM(), tasks, raws, {})
        self.assertEqual(out, [])
        self.assertTrue((self.mods / "a.jar").is_file())

    def test_blocked_mod_becomes_manual_entry(self):
        tasks = [_task(self.mods, "ok.jar"), _task(self.mods, "blocked.jar")]
        raws = [_raw(1, 11), _raw(2, 22)]
        meta = {22: {"displayName": "Blocked Mod", "fileName": "blocked.jar"}}
        out = download_pack_mods_tolerant(
            _FakeDM(fail_names={"blocked.jar"}), tasks, raws, meta)
        self.assertEqual(len(out), 1)
        entry = out[0]
        self.assertEqual(entry["name"], "Blocked Mod")
        self.assertEqual(entry["filename"], "blocked.jar")
        self.assertEqual(entry["project_id"], 2)
        self.assertEqual(entry["url"], "https://www.curseforge.com/projects/2")
        self.assertEqual(entry["dest_dir"], str(self.mods))
        # 其余 Mod 已就位
        self.assertTrue((self.mods / "ok.jar").is_file())

    def test_total_failure_reraises(self):
        """全军覆没（>3 个）视为网络故障，如实抛错。"""
        names = [f"m{i}.jar" for i in range(5)]
        tasks = [_task(self.mods, n) for n in names]
        raws = [_raw(i, i * 10) for i in range(5)]
        with self.assertRaises(DownloadError):
            download_pack_mods_tolerant(
                _FakeDM(fail_names=set(names)), tasks, raws, {})

    def test_small_pack_all_blocked_still_tolerated(self):
        """小包（<=3 个）全部被禁分发也转手动清单。"""
        tasks = [_task(self.mods, "a.jar"), _task(self.mods, "b.jar")]
        raws = [_raw(1, 11), _raw(2, 22)]
        out = download_pack_mods_tolerant(
            _FakeDM(fail_names={"a.jar", "b.jar"}), tasks, raws, {})
        self.assertEqual(len(out), 2)

    def test_cancel_propagates(self):
        tasks = [_task(self.mods, "a.jar")]
        with self.assertRaises(DownloadError) as ctx:
            download_pack_mods_tolerant(
                _FakeDM(cancel=True), tasks, [_raw(1, 11)], {})
        self.assertIn("用户取消", str(ctx.exception))

    def test_error_without_missing_files_reraises(self):
        """报错但磁盘上文件都在：状态对不上，不吞错。"""
        class WeirdDM(_FakeDM):
            def download_all(self, tasks, message="下载中"):
                for task in tasks:
                    dest = Path(task[1])
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(b"jar")
                raise DownloadError("元数据校验失败")

        tasks = [_task(self.mods, "a.jar")]
        with self.assertRaises(DownloadError):
            download_pack_mods_tolerant(WeirdDM(), tasks, [_raw(1, 11)], {})


if __name__ == "__main__":
    unittest.main()
