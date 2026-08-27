# -*- coding: utf-8 -*-
"""Cleanroom 自动安装（HMCL 3.7「支持自动安装 Cleanroom」同款）：

1. GitHub Releases 版本列表解析（过滤无安装器的发行、prerelease 标注）；
2. 安装器内嵌 maven/ 构件解压（Cleanroom 主 jar 不提供外网下载地址）；
3. 离线安装全流程：假安装器 → 原版就位 → 版本 json 落盘 → 构件进 libraries；
4. loader_meta / game_install 接线。
"""
import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import PropertyMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mclauncher import cleanroom, utils  # noqa: E402
from mclauncher.config import CONFIG  # noqa: E402

TAG = "0.3.1-alpha"
VID = f"1.12.2-Cleanroom-{TAG}"

RELEASES = [
    {"tag_name": "0.3.1-alpha", "prerelease": False,
     "assets": [{"name": "cleanroom-0.3.1-alpha-installer.jar"},
                {"name": "cleanroom-0.3.1-alpha-universal.jar"}]},
    {"tag_name": "0.3.0-beta", "prerelease": True,
     "assets": [{"name": "cleanroom-0.3.0-beta-installer.jar"}]},
    # 没有安装器的发行不该出现在列表里
    {"tag_name": "0.2.0-alpha", "prerelease": False,
     "assets": [{"name": "cleanroom-0.2.0-alpha-sources.jar"}]},
    {"tag_name": "", "assets": []},
]


class _FakeDM:
    def __init__(self, releases=None, jar_bytes=b""):
        self.releases = RELEASES if releases is None else releases
        self.jar_bytes = jar_bytes
        self.downloaded = []

    def fetch_json(self, url, timeout=30, **kw):
        assert "CleanroomMC/Cleanroom/releases" in url
        return self.releases

    def download(self, url, dest, force=False, **kw):
        self.downloaded.append(url)
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self.jar_bytes)
        return dest


def _installer_jar_bytes(tag=TAG, with_maven=True, processors=None) -> bytes:
    """构造一个 Cleanroom 风格的现代 Forge 安装器 jar。"""
    vid = f"1.12.2-Cleanroom-{tag}"
    profile = {
        "spec": 0,
        "profile": "cleanroom",
        "version": vid,
        "minecraft": "1.12.2",
        "json": "/version.json",
        "path": f"com.cleanroommc:cleanroom:{tag}",
        "data": {},
        "processors": processors or [],
        "libraries": [{
            "name": f"com.cleanroommc:cleanroom:{tag}",
            "downloads": {"artifact": {
                "path": f"com/cleanroommc/cleanroom/{tag}/cleanroom-{tag}.jar",
                "url": ""}},
        }],
    }
    vjson = {
        "id": vid,
        "type": "release",
        "assets": "1.12",
        "javaVersion": {"majorVersion": 25},
        "mainClass": "top.outlands.foundation.boot.Foundation",
        "minecraftArguments": "--username ${auth_player_name}",
        "libraries": [{
            "name": f"com.cleanroommc:cleanroom:{tag}",
            "downloads": {"artifact": {
                "path": f"com/cleanroommc/cleanroom/{tag}/cleanroom-{tag}.jar",
                "url": ""}},
        }],
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("install_profile.json", json.dumps(profile))
        zf.writestr("version.json", json.dumps(vjson))
        if with_maven:
            zf.writestr(
                f"maven/com/cleanroommc/cleanroom/{tag}/cleanroom-{tag}.jar",
                b"PK-fake-cleanroom-jar")
    return buf.getvalue()


class ListVersionsTests(unittest.TestCase):
    def test_filters_and_flags(self):
        rows = cleanroom.list_versions(_FakeDM())
        self.assertEqual([r["id"] for r in rows], ["0.3.1-alpha", "0.3.0-beta"])
        self.assertTrue(rows[0]["stable"])
        self.assertFalse(rows[1]["stable"])  # prerelease → 测试版标注

    def test_empty_payload(self):
        self.assertEqual(cleanroom.list_versions(_FakeDM(releases=[])), [])


class ExtractMavenTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        self.addCleanup(self._td.cleanup)

    def _jar(self, entries: dict) -> Path:
        p = self.root / "inst.jar"
        with zipfile.ZipFile(p, "w") as zf:
            for name, data in entries.items():
                zf.writestr(name, data)
        return p

    def test_extracts_only_maven_entries(self):
        jar = self._jar({
            "maven/com/x/1.0/x-1.0.jar": b"lib",
            "maven/sub/": b"",
            "version.json": b"{}",
        })
        libs = self.root / "libraries"
        n = cleanroom.extract_embedded_maven(jar, libs)
        self.assertEqual(n, 1)
        self.assertEqual((libs / "com/x/1.0/x-1.0.jar").read_bytes(), b"lib")
        self.assertFalse((libs / "version.json").exists())

    def test_traversal_entries_skipped(self):
        jar = self._jar({"maven/../evil.jar": b"boom"})
        libs = self.root / "libraries"
        n = cleanroom.extract_embedded_maven(jar, libs)
        self.assertEqual(n, 0)
        self.assertFalse((self.root / "evil.jar").exists())

    def test_existing_files_kept_without_force(self):
        jar = self._jar({"maven/a/b.jar": b"new"})
        libs = self.root / "libraries"
        (libs / "a").mkdir(parents=True)
        (libs / "a/b.jar").write_bytes(b"old")
        cleanroom.extract_embedded_maven(jar, libs)
        self.assertEqual((libs / "a/b.jar").read_bytes(), b"old")
        cleanroom.extract_embedded_maven(jar, libs, force=True)
        self.assertEqual((libs / "a/b.jar").read_bytes(), b"new")


class InstallTests(unittest.TestCase):
    """真 Installer + 假安装器 jar：走完 _install_forge_modern 离线流程。"""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        self.addCleanup(self._td.cleanup)
        for p in (
            patch.object(utils, "ROOT", self.root),
            patch.object(type(CONFIG), "instances_dir",
                         new_callable=PropertyMock,
                         return_value=self.root / "instances"),
        ):
            p.start()
            self.addCleanup(p.stop)

        from mclauncher.instances import Instance
        (self.root / "instances" / "inst").mkdir(parents=True)
        self.inst = Instance("inst")

        from mclauncher.installer import Installer
        self.dm = _FakeDM(jar_bytes=_installer_jar_bytes())
        self.installer = Installer(self.inst, dm=self.dm)
        # 原版 1.12.2 由 install_version 负责；测试里直接落盘
        self._vanilla_installed = []

        def _fake_install_version(vid, force=False, java=None):
            self._vanilla_installed.append(vid)
            vdir = self.inst.versions_dir() / vid
            vdir.mkdir(parents=True, exist_ok=True)
            utils.write_json(vdir / f"{vid}.json", {"id": vid})
            (vdir / f"{vid}.jar").write_bytes(b"vanilla")
            return vid

        p = patch.object(Installer, "install_version",
                         side_effect=_fake_install_version)
        p.start()
        self.addCleanup(p.stop)

    def test_wrong_mc_version_rejected(self):
        from mclauncher.installer import InstallError
        with self.assertRaises(InstallError):
            self.installer.install_cleanroom("1.20.1", TAG)
        self.assertEqual(self.dm.downloaded, [])

    def test_empty_release_list_rejected(self):
        from mclauncher.installer import InstallError
        self.dm.releases = []
        with self.assertRaises(InstallError):
            self.installer.install_cleanroom("1.12.2")

    def test_full_offline_install(self):
        vid = self.installer.install_cleanroom("1.12.2", TAG)
        self.assertEqual(vid, VID)
        # 从 GitHub 地址下载安装器（DownloadManager 会自动加国内镜像候选）
        self.assertTrue(any("github.com/CleanroomMC/Cleanroom" in u
                            for u in self.dm.downloaded))
        # 原版先就位
        self.assertEqual(self._vanilla_installed, ["1.12.2"])
        # 版本 json 落盘且保留 javaVersion（启动时自动匹配新 Java）
        vjson = utils.read_json(
            self.inst.versions_dir() / VID / f"{VID}.json", {})
        self.assertEqual(vjson.get("id"), VID)
        self.assertEqual((vjson.get("javaVersion") or {}).get("majorVersion"), 25)
        # 内嵌主构件进了 libraries
        lib = (self.inst.libraries_dir()
               / f"com/cleanroommc/cleanroom/{TAG}/cleanroom-{TAG}.jar")
        self.assertEqual(lib.read_bytes(), b"PK-fake-cleanroom-jar")

    def test_latest_release_picked_when_version_omitted(self):
        vid = self.installer.install_cleanroom("1.12.2")
        self.assertEqual(vid, VID)


class WiringTests(unittest.TestCase):
    def test_loader_meta_rejects_other_mc(self):
        from mclauncher import loader_meta
        self.assertEqual(
            loader_meta.list_loader_versions(_FakeDM(), "1.20.1", "Cleanroom"), [])

    def test_loader_meta_lists_for_1_12_2(self):
        from mclauncher import loader_meta
        rows = loader_meta.list_loader_versions(_FakeDM(), "1.12.2", "Cleanroom")
        self.assertEqual(rows[0]["id"], "0.3.1-alpha")

    def test_game_install_dispatch(self):
        from mclauncher.game_install import install_game

        class _Stub:
            skip_assets = False

            def __init__(self, inst):
                self.instance = inst
                self.calls = []

            def _note(self, *a, **k):
                pass

            def install_cleanroom(self, mc, version=None, force=False):
                self.calls.append((mc, version))
                return f"1.12.2-Cleanroom-{version or 'latest'}"

        with tempfile.TemporaryDirectory() as td:
            with patch.object(type(CONFIG), "instances_dir",
                              new_callable=PropertyMock,
                              return_value=Path(td)):
                from mclauncher.instances import Instance
                (Path(td) / "inst").mkdir()
                stub = _Stub(Instance("inst"))
                vid = install_game(stub, "1.12.2", loader="Cleanroom",
                                   loader_version=TAG)
        self.assertEqual(stub.calls, [("1.12.2", TAG)])
        self.assertEqual(vid, VID)

    def test_detect_loader_maps_cleanroom_to_forge(self):
        from mclauncher import mods as mods_mod

        class _Inst:
            def installed_ids(self):
                return [VID]

        self.assertEqual(mods_mod.detect_loader(_Inst()), "forge")


class WizardGatingTests(unittest.TestCase):
    """安装向导：Cleanroom 只在 1.12.2 出现（HMCL 同款版本门槛）。"""

    @classmethod
    def setUpClass(cls):
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def _loaders(self, mc: str) -> list[str]:
        class _B:
            def call_async(self, fn, done=None, fail=None):
                pass

        from PySide6.QtWidgets import QWidget
        from app.pages.install_wizard import InstallWizardDialog
        host = QWidget()
        dlg = InstallWizardDialog(_B(), mc, "default", host)
        return [dlg.primary.itemText(i) for i in range(dlg.primary.count())]

    def test_1_12_2_offers_cleanroom(self):
        self.assertIn("Cleanroom", self._loaders("1.12.2"))

    def test_other_versions_do_not(self):
        self.assertNotIn("Cleanroom", self._loaders("1.20.1"))


if __name__ == "__main__":
    unittest.main()
