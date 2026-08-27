# -*- coding: utf-8 -*-
"""资源文件更新日志：HTML 转文本、Modrinth / CurseForge 按需拉取。"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mclauncher import catalog_files as cf
from mclauncher import mods as mods_mod


class HtmlToTextTests(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(cf.html_to_text(""), "")
        self.assertEqual(cf.html_to_text(None), "")

    def test_plain_text_passthrough(self):
        self.assertEqual(cf.html_to_text("Fixed a bug"), "Fixed a bug")

    def test_paragraphs_and_breaks(self):
        out = cf.html_to_text("<p>First</p><p>Second<br>Third</p>")
        self.assertEqual(out, "First\nSecond\nThird")

    def test_list_items_get_bullets(self):
        out = cf.html_to_text("<ul><li>one</li><li>two</li></ul>")
        self.assertEqual(out, "• one\n• two")

    def test_strips_script_and_style(self):
        out = cf.html_to_text(
            "<script>alert(1)</script><style>p{}</style><p>ok</p>")
        self.assertEqual(out, "ok")

    def test_unescapes_entities(self):
        self.assertEqual(cf.html_to_text("a &amp; b &lt;c&gt;"), "a & b <c>")

    def test_collapses_blank_lines(self):
        out = cf.html_to_text("<p>a</p><p></p><p></p><p>b</p>")
        self.assertNotIn("\n\n\n", out)
        self.assertTrue(out.startswith("a") and out.endswith("b"))


class _DM:
    def __init__(self, payload=None, err=None):
        self.payload = payload
        self.err = err
        self.urls = []

    def fetch_json(self, url, **kwargs):
        self.urls.append(url)
        if self.err:
            raise self.err
        return self.payload


class FetchChangelogModrinthTests(unittest.TestCase):
    def test_fetches_full_text(self):
        dm = _DM(payload={"changelog": "## 2.0\n- stuff"})
        out = cf.fetch_changelog(dm, {"source": "modrinth", "version_id": "abc"})
        self.assertEqual(out, "## 2.0\n- stuff")
        self.assertTrue(any("/version/abc" in u for u in dm.urls))

    def test_file_id_fallback_key(self):
        dm = _DM(payload={"changelog": "x"})
        out = cf.fetch_changelog(dm, {"source": "modrinth", "file_id": "abc"})
        self.assertEqual(out, "x")

    def test_missing_version_id_returns_empty(self):
        dm = _DM(payload={"changelog": "x"})
        self.assertEqual(cf.fetch_changelog(dm, {"source": "modrinth"}), "")
        self.assertEqual(dm.urls, [])

    def test_all_endpoints_down_raises(self):
        dm = _DM(err=RuntimeError("down"))
        with self.assertRaises(mods_mod.ModError):
            cf.fetch_changelog(dm, {"source": "modrinth", "version_id": "abc"})

    def test_empty_changelog_returns_empty(self):
        dm = _DM(payload={"changelog": ""})
        self.assertEqual(
            cf.fetch_changelog(dm, {"source": "modrinth", "version_id": "a"}), "")


class FetchChangelogCurseforgeTests(unittest.TestCase):
    def test_fetches_and_converts_html(self):
        with patch.object(mods_mod, "_cf_fetch",
                          return_value={"data": "<p>Fixed <b>bug</b></p>"}) as get:
            out = cf.fetch_changelog(_DM(), {"source": "curseforge",
                                             "id": 42, "file_id": 100})
        self.assertEqual(out, "Fixed bug")
        path = get.call_args.args[1]
        self.assertEqual(path, "/mods/42/files/100/changelog")

    def test_resolves_addon_by_slug(self):
        with patch.object(mods_mod, "cf_by_slug", return_value={"id": 77}) as by_slug, \
             patch.object(mods_mod, "_cf_fetch", return_value={"data": "ok"}):
            out = cf.fetch_changelog(_DM(), {"source": "curseforge",
                                             "slug": "jei", "kind": "mod",
                                             "file_id": 5})
        self.assertEqual(out, "ok")
        by_slug.assert_called_once()

    def test_missing_ids_returns_empty(self):
        with patch.object(mods_mod, "_cf_fetch") as get:
            self.assertEqual(
                cf.fetch_changelog(_DM(), {"source": "curseforge", "id": 42}), "")
            self.assertEqual(
                cf.fetch_changelog(_DM(), {"source": "curseforge", "file_id": 5}), "")
        get.assert_not_called()

    def test_api_error_propagates(self):
        with patch.object(mods_mod, "_cf_fetch",
                          side_effect=mods_mod.ModError("api down")):
            with self.assertRaises(mods_mod.ModError):
                cf.fetch_changelog(_DM(), {"source": "curseforge",
                                           "id": 42, "file_id": 100})


class RowChangelogFieldTests(unittest.TestCase):
    def test_row_truncates_to_400(self):
        row = cf._row(changelog="x" * 1000)
        self.assertEqual(len(row["changelog"]), 400)

    def test_row_defaults_empty(self):
        self.assertEqual(cf._row()["changelog"], "")


if __name__ == "__main__":
    unittest.main()
