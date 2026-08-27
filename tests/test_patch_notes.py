# -*- coding: utf-8 -*-
"""官方版本更新说明（HMCL 版本公告同款）：索引解析、正文抓取、缓存兜底。"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mclauncher import news


_INDEX_PAYLOAD = {"version": 1, "entries": [
    {"title": "Minecraft 1.21.1", "version": "1.21.1", "type": "release",
     "date": "2024-08-08T12:00:00Z",
     "image": {"url": "/images/1.21.1.png", "title": "x"},
     "contentPath": "v2/1.21.1.json"},
    {"title": "Minecraft 24w33a", "version": "24w33a", "type": "snapshot",
     "contentPath": "v2/24w33a.json"},
    {"broken": True},
]}


class _FakeDM:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def fetch_json(self, url, timeout=15):
        self.calls.append(url)
        for key, value in self.responses.items():
            if key in url:
                if isinstance(value, Exception):
                    raise value
                return value
        raise RuntimeError(f"unexpected url {url}")


class IndexTests(unittest.TestCase):
    def test_parse_full_index(self):
        rows = news._index_rows(_INDEX_PAYLOAD)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["version"], "1.21.1")
        self.assertEqual(rows[0]["type"], "release")
        self.assertEqual(rows[0]["date"], "2024-08-08")
        self.assertEqual(rows[0]["contentPath"], "v2/1.21.1.json")
        self.assertEqual(rows[0]["image"], "/images/1.21.1.png")

    def test_index_caches(self):
        with tempfile.TemporaryDirectory() as td:
            cache = Path(td) / "idx.json"
            dm = _FakeDM({"javaPatchNotes.json": _INDEX_PAYLOAD})
            with patch.object(news, "PATCH_INDEX_CACHE", cache):
                rows = news.patch_note_index(dm, force=True)
                self.assertEqual(len(rows), 2)
                # 第二次直接吃缓存，不再联网
                dm2 = _FakeDM({})
                rows2 = news.patch_note_index(dm2)
                self.assertEqual(rows2, rows)
                self.assertEqual(dm2.calls, [])

    def test_index_network_down_uses_cache(self):
        with tempfile.TemporaryDirectory() as td:
            cache = Path(td) / "idx.json"
            from mclauncher import utils
            utils.write_json(cache, [{"version": "1.20", "contentPath": "x"}])
            dm = _FakeDM({"javaPatchNotes.json": RuntimeError("down")})
            with patch.object(news, "PATCH_INDEX_CACHE", cache):
                rows = news.patch_note_index(dm, force=True)
            self.assertEqual(rows[0]["version"], "1.20")


class NoteTests(unittest.TestCase):
    def _run(self, responses, version="1.21.1"):
        with tempfile.TemporaryDirectory() as td:
            with patch.object(news, "PATCH_INDEX_CACHE", Path(td) / "idx.json"), \
                    patch.object(news, "PATCH_DIR", Path(td) / "notes"):
                dm = _FakeDM(responses)
                return news.patch_note(version, dm), dm

    def test_fetch_and_convert_body(self):
        note, _dm = self._run({
            "javaPatchNotes.json": _INDEX_PAYLOAD,
            "1.21.1.json": {"title": "Minecraft 1.21.1",
                            "body": "<h1>Fixes</h1><p>Fixed <b>MC-1</b> bug</p>"},
        })
        self.assertEqual(note["version"], "1.21.1")
        self.assertEqual(note["title"], "Minecraft 1.21.1")
        self.assertIn("Fixed", note["body"])
        self.assertIn("MC-1", note["body"])
        self.assertNotIn("<p>", note["body"])
        self.assertEqual(note["type"], "release")

    def test_note_cached_after_fetch(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.object(news, "PATCH_INDEX_CACHE", Path(td) / "idx.json"), \
                    patch.object(news, "PATCH_DIR", Path(td) / "notes"):
                dm = _FakeDM({
                    "javaPatchNotes.json": _INDEX_PAYLOAD,
                    "1.21.1.json": {"title": "T", "body": "<p>hello</p>"},
                })
                first = news.patch_note("1.21.1", dm)
                self.assertIn("hello", first["body"])
                dm2 = _FakeDM({})
                second = news.patch_note("1.21.1", dm2)
                self.assertEqual(second["body"], first["body"])
                self.assertEqual(dm2.calls, [])

    def test_unknown_version_returns_empty_body(self):
        note, _dm = self._run({
            "javaPatchNotes.json": _INDEX_PAYLOAD,
        }, version="1.0.0")
        self.assertEqual(note["body"], "")
        self.assertEqual(note["version"], "1.0.0")

    def test_version_match_case_insensitive(self):
        note, _dm = self._run({
            "javaPatchNotes.json": _INDEX_PAYLOAD,
            "24w33a.json": {"title": "Snap", "body": "<p>snapshot stuff</p>"},
        }, version="24W33A")
        self.assertIn("snapshot stuff", note["body"])

    def test_empty_version(self):
        self.assertEqual(news.patch_note(""), {})

    def test_cache_filename_sanitized(self):
        p = news._note_cache_file("1.21 pre/../x")
        self.assertNotIn("/", p.name)
        self.assertNotIn("..", p.name.replace("._", ""))
        self.assertTrue(p.name.endswith(".json"))


class FacadeTests(unittest.TestCase):
    def test_both_facades(self):
        from app.backend import BackendAPI as QtBackend
        from bridge.api import BackendAPI as BridgeBackend
        for cls in (QtBackend, BridgeBackend):
            self.assertTrue(callable(getattr(cls, "game_patch_note")))


if __name__ == "__main__":
    unittest.main()
