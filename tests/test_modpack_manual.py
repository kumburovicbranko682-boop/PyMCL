# -*- coding: utf-8 -*-
"""整合包被禁 Mod（CurseForge 403）手动下载引导测试。

作者禁止第三方分发时 API 无 downloadUrl、CDN 403。部分失败要生成
手动下载清单继续安装（对标 PCL2 / HMCL）；用户取消或全部失败仍要
原样报错，不能装出一个空 mods 的实例。
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mclauncher import modpack  # noqa: E402
from mclauncher.downloader import DownloadError  # noqa: E402


class _Inst:
    def __init__(self, root):
        self.path = Path(root)
        self.name = "test"


def _infos(root, present=(), absent=()):
    infos = []
    mods = Path(root) / "mods"
    mods.mkdir(parents=True, exist_ok=True)
    for i, name in enumerate(present):
        p = mods / name
        p.write_bytes(b"jar")
        infos.append({"pid": 100 + i, "fid": 200 + i, "dest": p, "name": name})
    for i, name in enumerate(absent):
        infos.append({"pid": 300 + i, "fid": 400 + i,
                      "dest": mods / name, "name": name})
    return infos


class HandleFailureTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.inst = _Inst(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_partial_failure_returns_manual_list(self):
        infos = _infos(self.tmp.name, present=["ok.jar"], absent=["blocked.jar"])
        with mock.patch("mclauncher.mods.cf_mods_by_ids", return_value={
                300: {"id": 300, "name": "Blocked Mod",
                      "links": {"websiteUrl": "https://www.curseforge.com/minecraft/mc-mods/blocked"}}}):
            manual = modpack._handle_mod_download_failure(
                None, DownloadError("下载整合包 Mod失败（1/2 个文件）"),
                infos, self.inst, None, None)
        self.assertEqual(len(manual), 1)
        self.assertEqual(manual[0]["project"], "Blocked Mod")
        self.assertEqual(manual[0]["filename"], "blocked.jar")
        self.assertEqual(
            manual[0]["url"],
            "https://www.curseforge.com/minecraft/mc-mods/blocked/files/400")
        # 清单落盘到 mods 目录
        txt = self.inst.path / "mods" / "需要手动下载的Mod.txt"
        self.assertTrue(txt.is_file())
        body = txt.read_text(encoding="utf-8")
        self.assertIn("blocked.jar", body)
        self.assertIn("/files/400", body)

    def test_meta_lookup_failure_falls_back_to_project_url(self):
        infos = _infos(self.tmp.name, present=["ok.jar"], absent=["blocked.jar"])
        with mock.patch("mclauncher.mods.cf_mods_by_ids",
                        side_effect=RuntimeError("api down")):
            manual = modpack._handle_mod_download_failure(
                None, DownloadError("x"), infos, self.inst, None, None)
        self.assertEqual(
            manual[0]["url"], "https://www.curseforge.com/projects/300/files/400")

    def test_cancel_reraises(self):
        infos = _infos(self.tmp.name, present=["ok.jar"], absent=["blocked.jar"])
        with self.assertRaises(DownloadError):
            modpack._handle_mod_download_failure(
                None, DownloadError("用户取消"), infos, self.inst, None, None)
        with self.assertRaises(DownloadError):
            modpack._handle_mod_download_failure(
                None, DownloadError("x"), infos, self.inst, lambda: True, None)

    def test_total_failure_reraises(self):
        infos = _infos(self.tmp.name, absent=["a.jar", "b.jar"])
        with self.assertRaises(DownloadError):
            modpack._handle_mod_download_failure(
                None, DownloadError("boom"), infos, self.inst, None, None)

    def test_nothing_missing_reraises(self):
        # 文件其实都在（例如校验误报），不该吞掉原始错误
        infos = _infos(self.tmp.name, present=["a.jar", "b.jar"])
        with self.assertRaises(DownloadError):
            modpack._handle_mod_download_failure(
                None, DownloadError("boom"), infos, self.inst, None, None)


class CfModsByIdsTest(unittest.TestCase):
    def test_batches_and_maps_by_id(self):
        from mclauncher import mods as mods_mod
        calls = []

        def fake_post(dm, path, body, api_key=None, timeout=60):
            calls.append((path, list(body["modIds"])))
            return {"data": [{"id": i, "name": f"mod{i}"} for i in body["modIds"]]}

        with mock.patch.object(mods_mod, "_cf_post", side_effect=fake_post):
            out = mods_mod.cf_mods_by_ids(None, list(range(1, 61)))
        self.assertEqual(len(out), 60)
        self.assertEqual(out[7]["name"], "mod7")
        self.assertEqual(len(calls), 2)  # 50 + 10 分批
        self.assertTrue(all(p == "/mods" for p, _ in calls))

    def test_bad_ids_skipped(self):
        from mclauncher import mods as mods_mod
        with mock.patch.object(mods_mod, "_cf_post") as post:
            out = mods_mod.cf_mods_by_ids(None, ["abc", None])
        self.assertEqual(out, {})
        post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
