# -*- coding: utf-8 -*-
"""通过版本 JSON 安装（HMCL「通过版本 JSON 安装」GP-5730 同款）：

1. .json 文件识别（import_files 分类）；
2. read_version_json 校验与可读报错；
3. 真 Installer + 假 DownloadManager 的离线安装全流程
   （落 json / 下客户端 jar / 下依赖库 / inheritsFrom 复用父 jar）；
4. bridge 门面的 import_local_file 分发。
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import PropertyMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mclauncher import import_files, manifest, utils, version_json_install as vji  # noqa: E402
from mclauncher.config import CONFIG  # noqa: E402
from mclauncher.installer import InstallError  # noqa: E402

VANILLA_LIKE = {
    "id": "1.20.1-custom",
    "mainClass": "net.minecraft.client.main.Main",
    "downloads": {"client": {"url": "https://example.com/client.jar"}},
    "libraries": [{
        "name": "com.example:lib:1.0",
        "downloads": {"artifact": {
            "path": "com/example/lib/1.0/lib-1.0.jar",
            "url": "https://example.com/lib-1.0.jar"}},
    }],
    "arguments": {"jvm": [], "game": []},
}

PATCH_LIKE = {
    "id": "fabric-custom",
    "inheritsFrom": "1.20.1",
    "mainClass": "net.fabricmc.loader.impl.launch.knot.KnotClient",
    "libraries": [],
}


class _FakeDM:
    """离线 DownloadManager：把每次下载记下来并写占位字节。"""

    def __init__(self):
        self.downloaded = []
        self.on_progress = None

    def cancel(self):
        return False

    def download(self, url, dest, sha1=None, size=None, force=False, **kw):
        self.downloaded.append(url)
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"fake-bytes")
        return dest

    def download_all(self, tasks, message=""):
        for url, dest, sha1, size in tasks:
            self.download(url, dest, sha1=sha1, size=size)

    def fetch_json(self, url, timeout=30, **kw):
        raise AssertionError(f"离线测试不应联网: {url}")


class LooksLikeTests(unittest.TestCase):
    def test_vanilla_and_patch_accepted(self):
        self.assertTrue(vji.looks_like_version_json(VANILLA_LIKE))
        self.assertTrue(vji.looks_like_version_json(PATCH_LIKE))

    def test_rejects_non_version_payloads(self):
        self.assertFalse(vji.looks_like_version_json(None))
        self.assertFalse(vji.looks_like_version_json([]))
        self.assertFalse(vji.looks_like_version_json({}))
        self.assertFalse(vji.looks_like_version_json({"id": "x"}))  # 没有 mainClass/inheritsFrom
        self.assertFalse(vji.looks_like_version_json({"mainClass": "M"}))  # 没有 id
        self.assertFalse(vji.looks_like_version_json({"foo": 1, "bar": 2}))


class ReadValidateTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        self.addCleanup(self._td.cleanup)

    def _write(self, name, payload) -> Path:
        p = self.root / name
        if isinstance(payload, (bytes, bytearray)):
            p.write_bytes(payload)
        else:
            p.write_text(json.dumps(payload), encoding="utf-8")
        return p

    def test_reads_valid_json(self):
        p = self._write("v.json", VANILLA_LIKE)
        self.assertEqual(vji.read_version_json(p)["id"], "1.20.1-custom")

    def test_reads_utf8_bom(self):
        p = self.root / "bom.json"
        p.write_bytes(b"\xef\xbb\xbf" + json.dumps(PATCH_LIKE).encode("utf-8"))
        self.assertEqual(vji.read_version_json(p)["id"], "fabric-custom")

    def test_missing_file(self):
        with self.assertRaises(InstallError):
            vji.read_version_json(self.root / "nope.json")

    def test_broken_json(self):
        p = self._write("broken.json", b"{not json")
        with self.assertRaises(InstallError):
            vji.read_version_json(p)

    def test_non_dict_top_level(self):
        p = self._write("arr.json", [1, 2, 3])
        with self.assertRaises(InstallError):
            vji.read_version_json(p)

    def test_plain_config_rejected(self):
        p = self._write("cfg.json", {"theme": "dark", "volume": 3})
        with self.assertRaises(InstallError) as ctx:
            vji.read_version_json(p)
        self.assertIn("不是 Minecraft 版本 JSON", str(ctx.exception))

    def test_oversize_rejected(self):
        p = self.root / "big.json"
        p.write_bytes(b"0" * (vji.MAX_JSON_BYTES + 1))
        with self.assertRaises(InstallError):
            vji.read_version_json(p)

    def test_default_version_id(self):
        self.assertEqual(vji.default_version_id("a/b.json", {"id": "X"}), "X")
        self.assertEqual(vji.default_version_id("a/b.json", {}), "b")


class ClassifyTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        self.addCleanup(self._td.cleanup)

    def test_json_ext_supported_for_drag_drop(self):
        self.assertIn(".json", import_files.SUPPORTED_EXTS)
        self.assertIn("version_json", import_files.KIND_LABELS)

    def test_version_json_classified(self):
        p = self.root / "v.json"
        p.write_text(json.dumps(VANILLA_LIKE), encoding="utf-8")
        info = import_files.classify_file(p)
        self.assertEqual(info["kind"], "version_json")
        self.assertEqual(info["version_id"], "1.20.1-custom")

    def test_patch_json_classified(self):
        p = self.root / "fabric.json"
        p.write_text(json.dumps(PATCH_LIKE), encoding="utf-8")
        self.assertEqual(import_files.classify_file(p)["kind"], "version_json")

    def test_plain_config_json_unknown(self):
        p = self.root / "settings.json"
        p.write_text(json.dumps({"lang": "zh"}), encoding="utf-8")
        self.assertEqual(import_files.classify_file(p)["kind"], "unknown")

    def test_broken_json_unknown(self):
        p = self.root / "broken.json"
        p.write_text("{oops", encoding="utf-8")
        self.assertEqual(import_files.classify_file(p)["kind"], "unknown")


class InstallTests(unittest.TestCase):
    """真 Installer + 假 DM：验证 json 落盘、jar / 库下载、继承处理。"""

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
        self.dm = _FakeDM()
        self.installer = Installer(self.inst, dm=self.dm)

    def _json_file(self, payload, name="v.json") -> Path:
        p = self.root / name
        p.write_text(json.dumps(payload), encoding="utf-8")
        return p

    def _pre_install(self, vid, payload=None):
        vdir = self.inst.versions_dir() / vid
        vdir.mkdir(parents=True, exist_ok=True)
        utils.write_json(vdir / f"{vid}.json",
                         payload or {"id": vid, "mainClass": "M", "libraries": []})
        (vdir / f"{vid}.jar").write_bytes(b"vanilla-jar")

    def test_full_install_lands_json_jar_and_libs(self):
        target = vji.install_from_json(self.installer, self._json_file(VANILLA_LIKE))
        self.assertEqual(target, "1.20.1-custom")
        vdir = self.inst.versions_dir() / target
        data = utils.read_json(vdir / f"{target}.json", None)
        self.assertEqual(data["id"], target)
        self.assertTrue((vdir / f"{target}.jar").is_file())
        self.assertTrue((self.inst.libraries_dir()
                         / "com/example/lib/1.0/lib-1.0.jar").is_file())
        self.assertIn("https://example.com/client.jar", self.dm.downloaded)
        self.assertIn("https://example.com/lib-1.0.jar", self.dm.downloaded)

    def test_custom_name_renames_id(self):
        target = vji.install_from_json(
            self.installer, self._json_file(VANILLA_LIKE), name="我的自定义")
        self.assertEqual(target, "我的自定义")
        data = utils.read_json(
            self.inst.versions_dir() / target / f"{target}.json", None)
        self.assertEqual(data["id"], "我的自定义")
        self.assertFalse((self.inst.versions_dir() / "1.20.1-custom").exists())

    def test_illegal_chars_sanitized(self):
        payload = dict(VANILLA_LIKE, id='bad/na*me')
        target = vji.install_from_json(self.installer, self._json_file(payload))
        self.assertEqual(target, "bad-na-me")

    def test_inherits_reuses_local_parent_jar(self):
        self._pre_install("1.20.1")
        target = vji.install_from_json(self.installer, self._json_file(PATCH_LIKE))
        self.assertEqual(target, "fabric-custom")
        self.assertTrue(self.inst.has_version("fabric-custom"))
        # 父版本已就位：不应有任何联网下载
        self.assertEqual(self.dm.downloaded, [])

    def test_inherits_missing_parent_readable_error(self):
        def _boom(dm, vid, force=False):
            raise manifest.VersionNotFound(f"找不到版本 {vid}")

        with patch.object(manifest, "get_version_json", side_effect=_boom):
            with self.assertRaises(InstallError) as ctx:
                vji.install_from_json(self.installer, self._json_file(PATCH_LIKE))
        self.assertIn("1.20.1", str(ctx.exception))
        # 失败要发生在落盘之前，不留残缺版本目录
        self.assertFalse((self.inst.versions_dir() / "fabric-custom").exists())

    def test_inherits_self_rejected(self):
        payload = dict(PATCH_LIKE, id="loop", inheritsFrom="loop")
        with self.assertRaises(InstallError):
            vji.install_from_json(self.installer, self._json_file(payload))

    def test_existing_version_rejected_without_force(self):
        self._pre_install("1.20.1-custom")
        with self.assertRaises(InstallError) as ctx:
            vji.install_from_json(self.installer, self._json_file(VANILLA_LIKE))
        self.assertIn("已存在", str(ctx.exception))

    def test_no_client_download_info_rejected(self):
        payload = {"id": "no-jar", "mainClass": "M", "libraries": []}
        with self.assertRaises(InstallError):
            vji.install_from_json(self.installer, self._json_file(payload))


class BridgeDispatchTests(unittest.TestCase):
    """bridge 门面：.json 拖入 import_local_file 应走 install_version_json。"""

    def setUp(self):
        from mclauncher.config import DEFAULT_CONFIG

        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        self.addCleanup(self._td.cleanup)
        data = {k: (list(v) if isinstance(v, list) else dict(v) if isinstance(v, dict) else v)
                for k, v in DEFAULT_CONFIG.items()}
        for p in (patch.object(utils, "ROOT", self.root),
                  patch.object(CONFIG, "data", data),
                  patch.object(CONFIG, "save", lambda: None)):
            p.start()
            self.addCleanup(p.stop)

        from bridge.api import BackendAPI

        class _Bus:
            def emit(self, *a, **k):
                pass

        self.api = BackendAPI(_Bus())

    def test_version_json_routed(self):
        p = self.root / "v.json"
        p.write_text(json.dumps(VANILLA_LIKE), encoding="utf-8")
        with patch.object(type(self.api), "install_version_json",
                          return_value="task-1") as m:
            out = self.api.import_local_file(str(p))
        self.assertEqual(out, "task-1")
        m.assert_called_once()
        self.assertEqual(m.call_args.args[0], str(p))

    def test_plain_json_still_unrecognized(self):
        p = self.root / "cfg.json"
        p.write_text(json.dumps({"a": 1}), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.api.import_local_file(str(p))


if __name__ == "__main__":
    unittest.main()
