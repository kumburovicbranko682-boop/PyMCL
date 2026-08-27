# -*- coding: utf-8 -*-
"""资源详情：对本地 mock 的 Modrinth / CurseForge API 验证统一结构。"""
from __future__ import annotations

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from mclauncher import catalog_detail
from mclauncher.downloader import DownloadManager

MR_PROJECT = {
    "id": "AANobbMI",
    "slug": "sodium",
    "project_type": "mod",
    "title": "Sodium",
    "description": "Modern rendering engine",
    "body": "# Sodium\n\nA **fast** renderer.",
    "icon_url": "https://cdn.modrinth.com/icon.png",
    "downloads": 12345678,
    "followers": 999,
    "categories": ["optimization"],
    "loaders": ["fabric", "quilt"],
    "game_versions": ["1.19.2", "1.20.1", "1.21"],
    "updated": "2026-08-01T10:00:00Z",
    "published": "2020-01-01T10:00:00Z",
    "license": {"id": "LGPL-3.0"},
    "client_side": "required",
    "server_side": "unsupported",
    "gallery": [{"url": "https://cdn.modrinth.com/g1.png", "title": "shot"}],
    "source_url": "https://github.com/x/sodium",
    "issues_url": "https://github.com/x/sodium/issues",
}

CF_MOD = {
    "data": {
        "id": 238222,
        "slug": "jei",
        "name": "Just Enough Items",
        "summary": "View items and recipes",
        "downloadCount": 987654321,
        "logo": {"url": "https://media.forgecdn.net/jei.png"},
        "categories": [{"name": "Map and Information"}],
        "authors": [{"name": "mezz"}],
        "dateModified": "2026-07-01T00:00:00Z",
        "dateCreated": "2014-01-01T00:00:00Z",
        "screenshots": [{"url": "https://media.forgecdn.net/s1.png", "title": "ui"}],
        "links": {
            "websiteUrl": "https://www.curseforge.com/minecraft/mc-mods/jei",
            "sourceUrl": "https://github.com/mezz/JustEnoughItems",
        },
        "latestFilesIndexes": [
            {"gameVersion": "1.21", "modLoader": 4},
            {"gameVersion": "1.20.1", "modLoader": 1},
        ],
    }
}


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path == "/mr/project/sodium":
            payload = MR_PROJECT
        elif self.path == "/cf/mods/238222":
            payload = CF_MOD
        elif self.path == "/cf/mods/238222/description":
            payload = {"data": "<h1>JEI</h1><p>hello</p>"}
        else:
            self.send_response(404)
            self.end_headers()
            return
        data = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class CatalogDetailTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        host, port = cls.server.server_address
        cls.base = f"http://{host}:{port}"
        cls.dm = DownloadManager(threads=1)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_modrinth_detail(self):
        with mock.patch("mclauncher.source.modrinth_api_bases",
                        return_value=[self.base + "/mr"]):
            d = catalog_detail.project_detail(self.dm, "modrinth", "sodium")
        self.assertEqual(d["name"], "Sodium")
        self.assertEqual(d["body_format"], "markdown")
        self.assertIn("**fast**", d["body"])
        self.assertEqual(d["downloads"], 12345678)
        self.assertEqual(d["license"], "LGPL-3.0")
        self.assertEqual(d["loaders"], ["fabric", "quilt"])
        # 最新版本在前
        self.assertEqual(d["game_versions"][0], "1.21")
        self.assertEqual(d["gallery"][0]["url"], "https://cdn.modrinth.com/g1.png")
        self.assertEqual(d["links"]["project"], "https://modrinth.com/mod/sodium")
        self.assertEqual(d["links"]["source"], "https://github.com/x/sodium")
        self.assertEqual(d["updated"], "2026-08-01")

    def test_curseforge_detail(self):
        with mock.patch("mclauncher.mods.cf_api_bases",
                        return_value=[self.base + "/cf"]):
            d = catalog_detail.project_detail(self.dm, "curseforge", 238222)
        self.assertEqual(d["name"], "Just Enough Items")
        self.assertEqual(d["body_format"], "html")
        self.assertIn("<h1>JEI</h1>", d["body"])
        self.assertEqual(d["downloads"], 987654321)
        self.assertEqual(d["author"], "mezz")
        self.assertEqual(sorted(d["loaders"]), ["fabric", "forge"])
        self.assertIn("1.21", d["game_versions"])
        self.assertEqual(d["links"]["project"],
                         "https://www.curseforge.com/minecraft/mc-mods/jei")
        self.assertEqual(d["gallery"][0]["title"], "ui")

    def test_missing_ident(self):
        with self.assertRaises(catalog_detail.DetailError):
            catalog_detail.project_detail(self.dm, "modrinth", "")

    def test_unreachable_source(self):
        with mock.patch("mclauncher.source.modrinth_api_bases",
                        return_value=["http://127.0.0.1:1/mr"]):
            with self.assertRaises(catalog_detail.DetailError):
                catalog_detail.project_detail(self.dm, "modrinth", "sodium")


if __name__ == "__main__":
    unittest.main()
