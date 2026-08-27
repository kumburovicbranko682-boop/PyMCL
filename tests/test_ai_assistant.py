# -*- coding: utf-8 -*-
"""AI 助手核心逻辑测试：接入解析、精确匹配、记忆、知识检索、提示词 i18n。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mclauncher.ai import knowledge, memory, store
from mclauncher.ai.agent import _round_kwargs
from mclauncher.ai.client import AIClientError, resolve_endpoint
from mclauncher.ai.defaults import DIAGNOSE_MAX_TOKENS, DIAGNOSE_MODEL
from mclauncher.ai.tools import exact_match_hit, normalize_ask_args


class ResolveEndpointTests(unittest.TestCase):
    def test_public_without_gateway_raises_with_guidance(self):
        with mock.patch("mclauncher.ai.client.DEFAULT_GATEWAY_URL", ""):
            with self.assertRaises(AIClientError) as ctx:
                resolve_endpoint({"ai_mode": "public"})
        self.assertIn("网关", str(ctx.exception))

    def test_public_https_gateway_ok_and_no_token_header(self):
        ep = resolve_endpoint({
            "ai_mode": "public",
            "ai_gateway_url": "https://ai.example.com/",
        })
        self.assertEqual(ep["url"], "https://ai.example.com/pymcl/chat")
        self.assertTrue(ep["public"])
        # 客户端绝不携带上游令牌
        self.assertNotIn("Authorization", ep["headers"])
        self.assertIn("X-PyMCL-Client", ep["headers"])

    def test_public_plain_http_on_public_host_rejected(self):
        with self.assertRaises(AIClientError):
            resolve_endpoint({
                "ai_mode": "public",
                "ai_gateway_url": "http://ai.example.com",
            })

    def test_public_plain_http_on_private_host_allowed(self):
        for host in ("http://127.0.0.1:8787", "http://192.168.1.5:8787",
                     "http://localhost:8787"):
            ep = resolve_endpoint({"ai_mode": "public", "ai_gateway_url": host})
            self.assertTrue(ep["url"].endswith("/pymcl/chat"))

    def test_public_model_follows_settings(self):
        ep = resolve_endpoint({
            "ai_mode": "public",
            "ai_gateway_url": "https://ai.example.com",
            "ai_model": "deepseek-v3.2",
        })
        self.assertEqual(ep["model"], "deepseek-v3.2")

    def test_custom_mode_requires_base_and_key(self):
        with self.assertRaises(AIClientError):
            resolve_endpoint({"ai_mode": "custom", "ai_api_key": "k"})
        with self.assertRaises(AIClientError):
            resolve_endpoint({"ai_mode": "custom", "ai_base_url": "https://x/v1"})
        ep = resolve_endpoint({
            "ai_mode": "custom",
            "ai_base_url": "https://x.example.com",
            "ai_api_key": "k",
            "ai_model": "m",
        })
        self.assertEqual(ep["url"], "https://x.example.com/v1/chat/completions")
        self.assertEqual(ep["model"], "m")


class ExactMatchTests(unittest.TestCase):
    def test_single_row_is_a_hit(self):
        rows = [{"name": "Sodium", "slug": "sodium"}]
        self.assertIs(exact_match_hit(rows, "钠"), rows[0])

    def test_exact_name_or_slug_among_many(self):
        rows = [
            {"name": "Sodium", "slug": "sodium"},
            {"name": "Sodium Extra", "slug": "sodium-extra"},
        ]
        self.assertEqual(exact_match_hit(rows, "SODIUM")["slug"], "sodium")
        self.assertEqual(exact_match_hit(rows, "sodium-extra")["name"], "Sodium Extra")

    def test_ambiguous_or_missing_returns_none(self):
        rows = [{"name": "A"}, {"name": "B"}]
        self.assertIsNone(exact_match_hit(rows, "c"))
        self.assertIsNone(exact_match_hit(rows, ""))
        self.assertIsNone(exact_match_hit([], "a"))

    def test_duplicate_exact_names_not_a_hit(self):
        rows = [
            {"name": "JEI", "slug": "jei", "source": "Modrinth"},
            {"name": "JEI", "slug": "jei-cf", "source": "CurseForge"},
        ]
        self.assertIsNone(exact_match_hit(rows, "jei"))


class GatewayModelWhitelistTests(unittest.TestCase):
    def test_pick_model_honors_whitelist_only(self):
        import ai_gateway.server as gw
        with mock.patch.object(gw, "NEWAPI_MODEL", "default-m"), \
                mock.patch.object(gw, "DEGRADE_MODEL", "cheap-m"), \
                mock.patch.object(gw, "ALLOWED_MODELS", ["deep-m"]):
            self.assertEqual(gw.pick_model(""), "default-m")
            self.assertEqual(gw.pick_model("deep-m"), "deep-m")
            self.assertEqual(gw.pick_model("evil-m"), "default-m")
            self.assertEqual(gw.pick_model("default-m"), "default-m")
            self.assertEqual(gw.pick_model("deep-m", degrade=True), "cheap-m")


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self._patch = mock.patch.object(
            memory, "STORE_FILE", Path(self._td.name) / "ai_memory.json")
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._td.cleanup()

    def test_record_event_updates_prefs_and_recent(self):
        memory.record_event("install_game", version="1.20.1",
                            loader="Fabric", instance="主实例")
        memory.record_event("install_mod", name="sodium", instance="主实例")
        data = memory.load()
        self.assertEqual(data["prefs"]["preferred_loader"], "Fabric")
        self.assertEqual(data["prefs"]["last_mc_version"], "1.20.1")
        self.assertEqual(data["prefs"]["last_instance"], "主实例")
        self.assertEqual(len(data["recent"]), 2)
        note = memory.system_note()
        self.assertIn("Fabric", note)
        self.assertIn("sodium", note)

    def test_vanilla_loader_not_remembered_as_preference(self):
        memory.record_event("install_game", version="1.20.1", loader="无")
        self.assertNotIn("preferred_loader", memory.load()["prefs"])

    def test_recent_rolls_over(self):
        for i in range(memory.MAX_RECENT + 5):
            memory.record_event("install_mod", name=f"m{i}")
        self.assertEqual(len(memory.load()["recent"]), memory.MAX_RECENT)

    def test_remember_fact_skips_unchanged_write(self):
        memory.remember_fact("机器内存MB", 16384)
        with mock.patch.object(memory, "save") as fake_save:
            memory.remember_fact("机器内存MB", 16384)
            fake_save.assert_not_called()
            memory.remember_fact("机器内存MB", 32768)
            fake_save.assert_called_once()

    def test_empty_memory_gives_empty_note(self):
        self.assertEqual(memory.system_note(), "")


class ChatNotesTests(unittest.TestCase):
    def test_notes_roll_and_survive_load(self):
        data = store._empty()
        cid = data["active_id"]
        with mock.patch.object(store, "save"):
            store.append_notes(data, cid, [f"n{i}" for i in range(store.MAX_NOTES + 9)])
        chat = store.get_chat(data, cid)
        self.assertEqual(len(chat["notes"]), store.MAX_NOTES)
        self.assertEqual(chat["notes"][-1], f"n{store.MAX_NOTES + 8}")

    def test_load_filters_bad_notes(self):
        raw = {
            "active_id": "c1",
            "chats": [{
                "id": "c1", "title": "t", "updated": 1,
                "messages": [],
                "notes": ["ok", "", "  ", "第二条"],
            }],
        }
        with mock.patch.object(store.utils, "read_json", return_value=raw), \
                mock.patch.object(store, "save"):
            data = store.load()
        self.assertEqual(data["chats"][0]["notes"], ["ok", "第二条"])


class KnowledgeTests(unittest.TestCase):
    def test_search_help_hits_relevant_articles(self):
        ids = [a["id"] for a in knowledge.search_help("启动 闪退")]
        self.assertIn("launch-fail", ids)
        ids = [a["id"] for a in knowledge.search_help("java 版本")]
        self.assertEqual(ids[0], "java")

    def test_search_help_empty_query_lists_all(self):
        rows = knowledge.search_help("")
        self.assertGreaterEqual(len(rows), 4)

    def test_wiki_lookup_rejects_empty(self):
        self.assertIsInstance(knowledge.wiki_lookup(""), str)

    def test_wiki_lookup_network_failure_is_readable(self):
        with mock.patch.object(knowledge.requests, "get",
                               side_effect=OSError("no net")):
            out = knowledge.wiki_lookup("下界合金")
        self.assertIsInstance(out, str)
        self.assertIn("失败", out)


class PromptI18nTests(unittest.TestCase):
    def test_prompt_follows_ui_language(self):
        from mclauncher import i18n
        from mclauncher.ai.prompt import system_prompt
        old = i18n.current_language()
        try:
            i18n.set_language("zh_CN")
            self.assertIn("用简体中文", system_prompt())
            i18n.set_language("en")
            text = system_prompt()
            self.assertIn("Reply in English", text)
            self.assertNotIn("用简体中文", text)
        finally:
            i18n.set_language(old)


class RoundKwargsTests(unittest.TestCase):
    def test_normal_round_uses_defaults(self):
        self.assertEqual(_round_kwargs({"ai_mode": "public"}, deep=False), {})

    def test_diagnose_round_escalates_public(self):
        out = _round_kwargs({"ai_mode": "public"}, deep=True)
        self.assertEqual(out["max_tokens"], DIAGNOSE_MAX_TOKENS)
        self.assertEqual(out["model"], DIAGNOSE_MODEL)

    def test_diagnose_round_keeps_custom_model(self):
        out = _round_kwargs({"ai_mode": "custom"}, deep=True)
        self.assertEqual(out["max_tokens"], DIAGNOSE_MAX_TOKENS)
        self.assertNotIn("model", out)


class AskArgsRegressionTests(unittest.TestCase):
    def test_single_question_normalized_with_other(self):
        qs = normalize_ask_args({
            "prompt": "选一个",
            "options": ["A", {"id": "b", "label": "B"}],
        })
        self.assertEqual(len(qs), 1)
        labels = [o["label"] for o in qs[0]["options"]]
        self.assertEqual(labels[:2], ["A", "B"])
        self.assertEqual(qs[0]["options"][-1]["id"], "other")


if __name__ == "__main__":
    unittest.main()
