# -*- coding: utf-8 -*-
"""Minecraft 官方版本更新日志测试（对标 HMCL 下载页 patch notes）。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mclauncher import patch_notes  # noqa: E402

INDEX = {
    "version": 1,
    "entries": [
        {"title": "Minecraft 1.21.1", "version": "1.21.1", "type": "release",
         "image": {"url": "/images/1.21.1.png", "title": "1.21.1"},
         "contentPath": "javaPatchNotes/1.21.1.json"},
        {"title": "Minecraft 24w14a", "version": "24w14a", "type": "snapshot",
         "image": {"url": "https://cdn.example.com/s.png"},
         "contentPath": "javaPatchNotes/24w14a.json"},
    ],
}
CONTENT = {"title": "Minecraft 1.21.1", "body": "<h1>Fixed bugs</h1><p>MC-1</p>"}


class _FakeDM:
    def __init__(self):
        self.calls = []

    def fetch_json(self, url, **_kw):
        self.calls.append(url)
        if url.endswith("javaPatchNotes.json"):
            return INDEX
        if "1.21.1.json" in url:
            return CONTENT
        if "24w14a.json" in url:
            return {"title": "24w14a", "body": ""}
        raise RuntimeError(f"unexpected url {url}")


class PatchNoteTest(unittest.TestCase):
    def setUp(self):
        patch_notes._index_cache = None
        self.dm = _FakeDM()

    def tearDown(self):
        patch_notes._index_cache = None

    def test_release_note_fetched(self):
        note = patch_notes.patch_note(self.dm, "1.21.1")
        self.assertEqual(note["title"], "Minecraft 1.21.1")
        self.assertEqual(note["type"], "release")
        self.assertIn("Fixed bugs", note["body_html"])
        # 相对图片地址补全为绝对地址
        self.assertEqual(note["image"],
                         "https://launchercontent.mojang.com/images/1.21.1.png")

    def test_absolute_image_kept(self):
        note = patch_notes.patch_note(self.dm, "24w14a")
        self.assertEqual(note["image"], "https://cdn.example.com/s.png")

    def test_unknown_version_raises(self):
        with self.assertRaises(patch_notes.PatchNoteError):
            patch_notes.patch_note(self.dm, "b1.7.3")

    def test_empty_version_raises(self):
        with self.assertRaises(patch_notes.PatchNoteError):
            patch_notes.patch_note(self.dm, "")

    def test_index_cached_between_calls(self):
        patch_notes.patch_note(self.dm, "1.21.1")
        patch_notes.patch_note(self.dm, "24w14a")
        index_calls = [u for u in self.dm.calls if u.endswith("javaPatchNotes.json")]
        self.assertEqual(len(index_calls), 1)

    def test_index_network_error_wrapped(self):
        class _DeadDM:
            def fetch_json(self, url, **_kw):
                raise RuntimeError("offline")
        with self.assertRaises(patch_notes.PatchNoteError):
            patch_notes.patch_note(_DeadDM(), "1.21.1")


if __name__ == "__main__":
    unittest.main()
