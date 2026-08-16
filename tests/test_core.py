import tempfile
import unittest
import time
import json
import sys
from uuid import uuid4
from unittest.mock import patch
from datetime import datetime
from pathlib import Path
from core.animation import AnimationStateMachine, PetState
from core.character_pack import CharacterPack, discover_character_packs, write_workshop_template
from core.chat import (ChatConfig, ChatConfigStore, ChatMemory, OpenAICompatibleClient,
                       SYSTEM_PROMPT, add_web_search_context, needs_web_search)
from core.interaction import classify_press
from core.models import Task, current_minute, sorted_tasks
from core.positioning import Rect, fit_overlay_position, restore_position
from core.single_instance import SingleInstanceGuard
from core.store import DataStore

def task(name, due, key): return Task(str(key), name, due.isoformat() if due else None, "2026-01-01T00:00:00", key)

class CoreTests(unittest.TestCase):
    def test_sort_due_name_length_and_stable_key(self):
        early = datetime(2026, 1, 1, 8); late = datetime(2026, 1, 2, 8)
        items = [task("none", None, 2), task("long-name", early, 8), task("a", early, 9), task("b", early, 1), task("late", late, 1)]
        self.assertEqual([x.name for x in sorted_tasks(items)], ["b", "a", "long-name", "late", "none"])

    def test_default_minute_and_valid_ranges(self):
        value = current_minute(datetime(2026, 4, 5, 23, 59, 34)); self.assertEqual((value.hour, value.minute, value.second), (23, 59, 0))
        self.assertEqual(list(range(24))[-1], 23); self.assertEqual(list(range(60))[-1], 59)

    def test_store_restart_backup_and_corruption(self):
        with tempfile.TemporaryDirectory() as d:
            store = DataStore(Path(d)); t = task("A", None, 7); store.save([t], {"hidden": True}); tasks, opts = store.load()
            self.assertEqual(tasks[0].name, "A"); self.assertTrue(opts["hidden"])
            store.path.write_text("broken", encoding="utf-8"); store.save([t], {"hidden": False}); store.path.write_text("broken", encoding="utf-8")
            self.assertEqual(store.load()[0][0].name, "A")

    def test_press_thresholds(self):
        self.assertEqual(classify_press(199, 5).kind, "click"); self.assertEqual(classify_press(200, 0).kind, "drag")
        self.assertEqual(classify_press(201, 0).kind, "drag"); self.assertEqual(classify_press(20, 6).kind, "drag")

    def test_restore_screen_boundary(self):
        screens = [Rect(0, 0, 1000, 700), Rect(1000, 0, 1000, 700)]
        self.assertEqual(restore_position([4000, 2], screens, (190, 250)), (40, 80))
        self.assertEqual(restore_position([900, 600], screens, (190, 250)), (810, 450))

    def test_overlay_position_stays_inside_work_area(self):
        screen = Rect(1000, -200, 800, 600)
        self.assertEqual(fit_overlay_position((1700, 350), (300, 120), screen), (1492, 272))
        self.assertEqual(fit_overlay_position((900, -300), (300, 120), screen), (1008, -192))

    @unittest.skipUnless(sys.platform == "win32", "Windows mutex only")
    def test_single_instance_guard_rejects_second_owner(self):
        name = "Local\\DeepSeaTodoPet.Test." + str(uuid4())
        first, second = SingleInstanceGuard(name), SingleInstanceGuard(name)
        try:
            self.assertTrue(first.acquire())
            self.assertFalse(second.acquire())
        finally:
            second.release(); first.release()
        third = SingleInstanceGuard(name)
        try:
            self.assertTrue(third.acquire())
        finally:
            third.release()

    def test_animation_states_do_not_conflict(self):
        m = AnimationStateMachine(); m.last_interaction_ms = 0; m.idle_after_ms = 10; self.assertEqual(m.tick(10), PetState.SLEEPING)
        m.interact(11); self.assertEqual(m.state, PetState.WAKING); self.assertEqual(m.tick(12), PetState.IDLE)
        m.drag(True, 13); self.assertEqual(m.state, PetState.DRAGGING); self.assertEqual(m.tick(999999), PetState.DRAGGING)

    def test_animation_starts_idle_instead_of_sleeping(self):
        m = AnimationStateMachine(); now = time.monotonic_ns() // 1_000_000
        self.assertEqual(m.state, PetState.IDLE)
        self.assertEqual(m.tick(now + 1), PetState.IDLE)

    def test_workshop_pack_metadata_and_safe_discovery(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "deepseek-demo"; (root / "frames").mkdir(parents=True)
            (root / "frames" / "idle-01.png").write_bytes(b"placeholder")
            (root / "frames" / "run.png").write_bytes(b"placeholder")
            config = {
                "id": "deepseek-demo", "name": "DeepSeek 娘化版", "size": [128, 176],
                "cell_size": [256, 352], "states": {
                    "idle": {"frames": ["frames/idle-01.png"], "frames_per_strip": 4, "durations_ms": [120, 120, 120, 120]},
                    "running_left": {"sheet": "frames/run.png", "row": 0, "durations_ms": [120]},
                    "running_right": {"sheet": "frames/run.png", "row": 0, "durations_ms": [120]},
                },
            }
            (root / "character.json").write_text(json.dumps(config), encoding="utf-8")
            pack = CharacterPack.from_file(root / "character.json")
            self.assertEqual(pack.size, (128, 176)); self.assertEqual(pack.states["idle"].frames_per_strip, 4)
            self.assertEqual(discover_character_packs([Path(d)])[0].id, "deepseek-demo")
            config["states"]["idle"]["frames"] = ["../outside.png"]
            (root / "character.json").write_text(json.dumps(config), encoding="utf-8")
            with self.assertRaises(ValueError): CharacterPack.from_file(root / "character.json")

    def test_workshop_template_is_non_destructive(self):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d) / "characters"; write_workshop_template(folder)
            self.assertTrue((folder / "character.example.json").is_file())
            first = (folder / "character.example.json").read_text(encoding="utf-8")
            write_workshop_template(folder)
            self.assertEqual(first, (folder / "character.example.json").read_text(encoding="utf-8"))

    def test_chat_config_and_memory_persist_locally(self):
        with tempfile.TemporaryDirectory() as folder:
            config_store = ChatConfigStore(Path(folder))
            config = ChatConfig("https://example.test/v1", "chat-model", "secret-key", False)
            config_store.save(config)
            restored = config_store.load()
            self.assertEqual(restored, config)
            self.assertNotIn("secret-key", config_store.path.read_text(encoding="utf-8"))
            self.assertFalse(restored.web_search)

    @unittest.skipUnless(sys.platform == "win32", "Windows DPAPI only")
    def test_chat_config_uses_machine_scoped_dpapi(self):
        with tempfile.TemporaryDirectory() as folder:
            store = ChatConfigStore(Path(folder))
            config = ChatConfig("https://example.test/v1", "model", "machine-secret", True)
            store.save(config)
            raw = store.path.read_text(encoding="utf-8")
            self.assertIn("dpapi-machine:", raw)
            self.assertNotIn("machine-secret", raw)
            self.assertEqual(ChatConfigStore(Path(folder)).load().api_key, "machine-secret")

            memory = ChatMemory(Path(folder))
            for index in range(22):
                memory.append("user" if index % 2 == 0 else "assistant", f"普通对话 {index}")
            memory.append("user", "我喜欢海边散步")
            memory.append("assistant", "海风确实很舒服")
            for index in range(22, 44):
                memory.append("user" if index % 2 == 0 else "assistant", f"最近消息 {index}")
            messages = memory.messages_for("还记得我喜欢去海边吗")
            self.assertEqual(messages[0]["content"], SYSTEM_PROMPT)
            self.assertIn("海边散步", messages[1]["content"])
            self.assertEqual(messages[-1], {"role": "user", "content": "还记得我喜欢去海边吗"})
            self.assertTrue(memory.document_path.is_file())
            self.assertIn("海风确实很舒服", memory.document_path.read_text(encoding="utf-8"))

    def test_openai_compatible_stream_parsing(self):
        class Response:
            headers = {"Content-Type": "text/event-stream; charset=utf-8"}
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def __iter__(self):
                return iter([
                    b'data: {"choices":[{"delta":{"content":"\xe4\xbd\xa0\xe5\xa5\xbd"}}]}\n',
                    b'data: {"choices":[{"delta":{"content":"\xef\xbc\x81"}}]}\n',
                    b'data: [DONE]\n',
                ])
        with patch("core.chat.urlopen", return_value=Response()) as open_url:
            result = "".join(OpenAICompatibleClient().stream(
                ChatConfig("https://example.test/v1", "model", "key"),
                [{"role": "user", "content": "你好"}],
            ))
        self.assertEqual(result, "你好！")
        request = open_url.call_args.args[0]
        self.assertEqual(request.full_url, "https://example.test/v1/chat/completions")
        self.assertIn(b'"stream": true', request.data)

    def test_web_search_intent_and_context_are_bounded(self):
        self.assertTrue(needs_web_search("帮我搜索一下今天的新闻"))
        self.assertFalse(needs_web_search("我今天心情不错"))

        class Search:
            def search(self, query):
                self.query = query
                return [{"title": "示例资料", "url": "https://example.test/source", "summary": "最新摘要"}]

        search = Search(); messages = [{"role": "system", "content": "规则"}, {"role": "user", "content": "查资料"}]
        enriched = add_web_search_context(messages, "查资料", search)
        self.assertEqual(search.query, "查资料")
        self.assertEqual(enriched[-1], messages[-1])
        self.assertIn("https://example.test/source", enriched[-2]["content"])
        self.assertIn("禁止说自己不能联网", enriched[-2]["content"])

    def test_chinese_lpl_search_is_disambiguated(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self): return b'<div class="result"><a class="result__a" href="https://example.test">LPL</a><a class="result__snippet">summary</a></div>'
        from core.chat import WebSearchClient
        with patch("core.chat.urlopen", return_value=Response()) as open_url:
            results = WebSearchClient().search("搜一下今天 LPL 比赛结果")
        self.assertEqual(results[0]["title"], "LPL")
        self.assertIn("%E8%8B%B1%E9%9B%84%E8%81%94%E7%9B%9F", open_url.call_args.args[0].full_url)

    def test_tavily_payload_and_fallback(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self): return b'{"results":[{"title":"T","url":"https://t.test","content":"C"}]}'
        with patch("core.chat.urlopen", return_value=Response()) as open_url:
            from core.chat import TavilySearchClient
            result = TavilySearchClient("tv-key").search("最新消息")
        self.assertEqual(result[0]["title"], "T")
        self.assertIn(b'"api_key": "tv-key"', open_url.call_args.args[0].data)

    def test_bing_html_parser_returns_results_without_key(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self):
                return b'<li class="b_algo"><h2><a href="https://example.test">Example title</a></h2><div class="b_caption"><p class="b_lineclamp2">Example summary</p></div></li>'
        from core.chat import BingSearchClient
        with patch("core.chat.urlopen", return_value=Response()):
            result = BingSearchClient().search("latest example")
        self.assertEqual(result, [{"title": "Example title", "url": "https://example.test", "summary": "Example summary"}])

    def test_search_failure_is_explicit(self):
        from core.chat import ChatApiError, WebSearchClient
        with patch("core.chat.urlopen", side_effect=OSError("offline")):
            with self.assertRaises(ChatApiError):
                WebSearchClient().search("搜索最新消息")
