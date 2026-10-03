import py_compile
import tempfile
import unittest
import time
import json
import sys
import zipfile
from decimal import Decimal
from uuid import uuid4
from unittest.mock import patch
from datetime import date, datetime
from pathlib import Path
from urllib.error import HTTPError
from core.animation import AnimationStateMachine, PetState
from core.character_pack import CharacterPack, discover_character_packs, write_workshop_template
from core.chat import (BingRssSearchClient, ChatApiError, ChatConfig, ChatConfigStore, ChatMemory,
                       OpenAICompatibleClient, SYSTEM_PROMPT, So360SearchClient, WebSearchClient,
                       add_web_search_context, needs_web_search)
from core import runtime_guard
from core.balance import (MAX_FONT_PX, MIN_FONT_PX, DeepSeekBalanceClient, balance_endpoint,
                          balance_font_px, parse_balance)
from core.positioning import Rect, balance_bubble_position
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

class RuntimeGuardTests(unittest.TestCase):
    """A %TEMP% cleanup must not break a running pet (see core/runtime_guard.py)."""

    def test_bootstrap_archive_module_names(self):
        with tempfile.TemporaryDirectory() as d:
            archive = Path(d) / "base_library.zip"
            with zipfile.ZipFile(archive, "w") as handle:
                handle.writestr("demo_pkg/__init__.pyc", b"")
                handle.writestr("demo_pkg/leaf.pyc", b"")
                handle.writestr("encodings/gbk.pyc", b"")
                handle.writestr("notes.txt", b"")
            self.assertEqual(runtime_guard.archive_module_names(archive),
                             ["demo_pkg", "demo_pkg.leaf", "encodings.gbk"])
            self.assertEqual(runtime_guard.archive_module_names(Path(d) / "missing.zip"), [])

    def test_preload_imports_zip_modules_and_tolerates_broken_entries(self):
        name = "runtime_probe_" + uuid4().hex
        with tempfile.TemporaryDirectory() as d:
            source = Path(d) / f"{name}.py"
            source.write_text("VALUE = 1\n", encoding="utf-8")
            compiled = Path(d) / f"{name}.pyc"
            py_compile.compile(str(source), cfile=str(compiled), doraise=True)
            archive = Path(d) / "base_library.zip"
            with zipfile.ZipFile(archive, "w") as handle:
                handle.write(compiled, f"{name}.pyc")
                handle.writestr("runtime_probe_broken.pyc", b"not a real pyc")
            sys.path.insert(0, str(archive))
            try:
                loaded = runtime_guard.preload_modules_from_archive(archive)
                self.assertIn(name, loaded)
                self.assertNotIn("runtime_probe_broken", loaded)
                self.assertIn(name, sys.modules)
            finally:
                sys.path.remove(str(archive))
                sys.modules.pop(name, None)

    def test_preload_is_inert_when_running_from_source(self):
        self.assertFalse(getattr(sys, "frozen", False))
        self.assertEqual(runtime_guard.preload_frozen_runtime(), 0)

    @unittest.skipUnless(sys.platform == "win32", "Windows extraction paths")
    def test_removed_runtime_is_recognised_and_explained(self):
        root = r"C:\Users\lenovo\AppData\Local\Temp\_MEI271722"
        with patch.object(sys, "_MEIPASS", root, create=True):
            self.assertTrue(runtime_guard.was_runtime_removed(
                FileNotFoundError(2, "No such file or directory", root + r"\base_library.zip")))
            self.assertTrue(runtime_guard.was_runtime_removed(
                FileNotFoundError(2, "No such file or directory", root + r"\_ssl.pyd")))
            self.assertTrue(runtime_guard.was_runtime_removed(OSError("cannot find base_library.zip")))
            self.assertFalse(runtime_guard.was_runtime_removed(FileNotFoundError(2, "No such file or directory", r"C:\other\file.txt")))
            self.assertFalse(runtime_guard.was_runtime_removed(OSError("connection refused")))
            self.assertIn("重新打开", runtime_guard.removed_runtime_hint())

    @unittest.skipUnless(sys.platform == "win32", "Windows extraction paths")
    def test_chat_reports_a_cleaned_runtime_instead_of_errno_2(self):
        root = r"C:\Users\lenovo\AppData\Local\Temp\_MEI271722"
        removed = FileNotFoundError(2, "No such file or directory", root + r"\base_library.zip")
        with patch.object(sys, "_MEIPASS", root, create=True), patch("core.chat.urlopen", side_effect=removed):
            with self.assertRaises(ChatApiError) as raised:
                list(OpenAICompatibleClient().stream(
                    ChatConfig("https://api.deepseek.com/v1", "deepseek-flash", "key"),
                    [{"role": "user", "content": "你好"}],
                ))
        self.assertIn("重新打开", str(raised.exception))
        self.assertNotIn("base_library.zip", str(raised.exception))

    def test_deepseek_is_the_default_endpoint(self):
        config = ChatConfig()
        self.assertEqual(config.base_url, "https://api.deepseek.com/v1")
        self.assertEqual(config.model, "deepseek-flash")

class BalanceTests(unittest.TestCase):
    """Official balance parsing, the pet-sized type scale, and where the balloon goes."""

    def test_balance_endpoint_drops_the_version_prefix(self):
        self.assertEqual(balance_endpoint("https://api.deepseek.com/v1"), "https://api.deepseek.com/user/balance")
        self.assertEqual(balance_endpoint("https://api.deepseek.com"), "https://api.deepseek.com/user/balance")
        self.assertEqual(balance_endpoint(""), "https://api.deepseek.com/user/balance")
        self.assertEqual(balance_endpoint("https://example.test:8443/v1"), "https://example.test:8443/user/balance")

    def test_parse_balance_reads_decimal_strings_and_prefers_cny(self):
        payload = {"is_available": True, "balance_infos": [
            {"currency": "USD", "total_balance": "1.00", "granted_balance": "0.00", "topped_up_balance": "1.00"},
            {"currency": "CNY", "total_balance": "144.38", "granted_balance": "0.00", "topped_up_balance": "144.38"}]}
        balance = parse_balance(payload)
        self.assertEqual(balance.currency, "CNY")
        self.assertEqual(balance.total, Decimal("144.38"))
        self.assertEqual(balance.format_total(), "¥144.38")
        self.assertTrue(balance.available)

    def test_parse_balance_survives_an_empty_payload(self):
        balance = parse_balance({})
        self.assertEqual(balance.total, Decimal("0"))
        self.assertEqual(balance.currency, "CNY")
        self.assertTrue(balance.available)

    def test_balance_type_scale_grows_but_stays_pet_sized(self):
        sizes = [balance_font_px(Decimal(value)) for value in ("0", "1", "100", "1000", "100000")]
        self.assertEqual(sizes, sorted(sizes))
        self.assertTrue(all(MIN_FONT_PX <= size <= MAX_FONT_PX for size in sizes))
        self.assertGreater(balance_font_px(Decimal("1000")), balance_font_px(Decimal("10")))
        self.assertEqual(balance_font_px(Decimal("100")), 31)

    def test_balance_client_explains_an_expired_key(self):
        error = HTTPError("https://api.deepseek.com/user/balance", 401, "Unauthorized", {}, None)
        with patch("core.balance.urlopen", side_effect=error):
            with self.assertRaises(ChatApiError) as raised:
                DeepSeekBalanceClient().fetch(ChatConfig("https://api.deepseek.com/v1", "deepseek-flash", "key"))
        self.assertIn("API Key", str(raised.exception))

    def test_balance_client_parses_a_live_shaped_response(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self): return b'{"is_available":true,"balance_infos":[{"currency":"CNY","total_balance":"144.38","granted_balance":"0.00","topped_up_balance":"144.38"}]}'
        with patch("core.balance.urlopen", return_value=Response()) as open_url:
            balance = DeepSeekBalanceClient().fetch(ChatConfig("https://api.deepseek.com/v1", "deepseek-flash", "key"))
        self.assertEqual(balance.total, Decimal("144.38"))
        request = open_url.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.deepseek.com/user/balance")
        self.assertEqual(request.get_header("Authorization"), "Bearer key")

    def test_balloon_tucks_against_the_pets_upper_right(self):
        pet = Rect(500, 300, 112, 154); screen = Rect(0, 0, 1440, 900)
        x, y, on_right = balance_bubble_position(pet, (150, 80), screen)
        self.assertTrue(on_right)
        self.assertEqual(x, 500 + 112 - 6)            # tucked in, not floating away
        self.assertEqual(y, 300 + 20 - 80)

    def test_balloon_uses_the_pets_upper_left_near_the_right_edge(self):
        pet = Rect(1300, 300, 112, 154); screen = Rect(0, 0, 1440, 900)
        x, y, on_right = balance_bubble_position(pet, (150, 80), screen)
        self.assertFalse(on_right)
        self.assertEqual(x, 1300 - 150 + 6)
        self.assertEqual(y, 300 + 20 - 80)

    def test_balloon_stays_beside_the_pet_when_both_sides_are_busy(self):
        """Live case: pet pinned right, task list on the left - no screen-corner jump."""
        pet = Rect(1199, 93, 112, 154); screen = Rect(0, 0, 1440, 900)
        task_list = Rect(865, 142, 342, 328)
        x, y, on_right = balance_bubble_position(pet, (150, 80), screen, [task_list])
        self.assertFalse(on_right)
        self.assertEqual(x, 1199 - 150 + 6)
        self.assertLess(x + 150, screen.x + screen.width)
        self.assertGreater(x, screen.x)                # nowhere near a corner
        self.assertLess(y, 300)                        # still up at the pet's head

    def test_balloon_avoids_the_task_list_when_it_can(self):
        pet = Rect(500, 300, 112, 154); screen = Rect(0, 0, 1440, 900)
        task_list = Rect(622, 300, 200, 240)           # the list sitting to the pet's right
        x, y, on_right = balance_bubble_position(pet, (150, 80), screen, [task_list])
        self.assertFalse(on_right)
        self.assertEqual(x, 500 - 150 + 6)

    def test_balloon_sits_beside_a_pet_pinned_to_the_top_edge(self):
        pet = Rect(1199, 0, 112, 154); screen = Rect(0, 0, 1440, 900)
        x, y, on_right = balance_bubble_position(pet, (150, 120), screen)
        self.assertFalse(on_right)
        self.assertLessEqual(x + 150, pet.x)           # clear of the pet
        self.assertGreaterEqual(y, 8)

    def test_balloon_stays_on_screen(self):
        pet = Rect(300, 300, 112, 154); screen = Rect(0, 0, 640, 480)
        x, y, on_right = balance_bubble_position(pet, (150, 80), screen)
        self.assertGreaterEqual(x, 8)
        self.assertLessEqual(x + 150, 640 - 8)
        self.assertGreaterEqual(y, 8)


RSS_FEED = (b'<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>'
            b'<item><title>DeepSeek News</title><link>https://example.test/a</link>'
            b'<description>&lt;p&gt;Latest &lt;b&gt;update&lt;/b&gt;&lt;/p&gt;</description></item>'
            b'<item><title></title><link></link><description>ignored</description></item>'
            b'</channel></rss>')

SO360_PAGE = ('<li class="res-list"><h3 class="res-title ">'
              '<a href="https://www.so.com/link?m=redirect" data-mdurl="https://finance.example.test/a.html" '
              'rel="noopener">东财新闻标题</a></h3>'
              '<p class="res-desc"><span class="gray g-c-gray">今天 - </span>DeepSeek <em>最新</em>消息</p></li>'
              '<li class="res-list"><h3 class="res-title ">'
              '<a href="https://www.so.com/link?m=other" data-mdurl="https://example.test/b">第二条</a></h3></li>')


class SearchSourceTests(unittest.TestCase):
    """The no-key search chain, after Bing could not answer Chinese queries."""

    class Response:
        def __init__(self, body): self.body = body
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self): return self.body

    def test_bing_rss_feed_is_parsed_and_empty_items_dropped(self):
        with patch("core.chat.urlopen", return_value=self.Response(RSS_FEED)) as open_url:
            results = BingRssSearchClient().search("最新消息")
        self.assertEqual(results, [{"title": "DeepSeek News", "url": "https://example.test/a",
                                    "summary": "Latest update"}])
        self.assertIn("format=rss", open_url.call_args.args[0].full_url)
        self.assertIn("%E6%9C%80%E6%96%B0", open_url.call_args.args[0].full_url)

    def test_bing_rss_reports_a_broken_feed(self):
        with patch("core.chat.urlopen", return_value=self.Response(b"<html>not a feed")):
            with self.assertRaises(ChatApiError):
                BingRssSearchClient().search("x")

    def test_so360_prefers_the_real_url_over_the_redirect(self):
        with patch("core.chat.urlopen", return_value=self.Response(SO360_PAGE.encode("utf-8"))):
            results = So360SearchClient().search("今天上海天气")
        self.assertEqual([item["url"] for item in results],
                         ["https://finance.example.test/a.html", "https://example.test/b"])
        self.assertEqual(results[0]["title"], "东财新闻标题")
        self.assertIn("DeepSeek", results[0]["summary"])
        self.assertIn("最新", results[0]["summary"])

    def test_so360_keeps_a_result_whose_snippet_never_closes(self):
        page = ('<h3 class="res-title"><a href="https://example.test/c" data-mdurl="https://example.test/c">'
                'only title</a></h3>').encode("utf-8")
        with patch("core.chat.urlopen", return_value=self.Response(page)):
            results = So360SearchClient().search("x")
        self.assertEqual(results, [{"title": "only title", "url": "https://example.test/c", "summary": ""}])

    def test_search_chain_tries_360_first_then_falls_through_to_bing_rss(self):
        empty_360 = self.Response(b'<div class="no-result"></div>')
        with patch("core.chat.urlopen", side_effect=[empty_360, self.Response(RSS_FEED)]) as open_url:
            results = WebSearchClient().search("今天上海天气")
        self.assertEqual(results[0]["url"], "https://example.test/a")
        urls = [call.args[0].full_url for call in open_url.call_args_list]
        self.assertIn("so.com", urls[0])
        self.assertIn("format=rss", urls[1])

    def test_search_chain_order_is_documented_by_sources(self):
        self.assertEqual([name for name, _ in WebSearchClient().sources()],
                         ["360 搜索", "Bing RSS", "Bing", "DuckDuckGo"])
        self.assertEqual([name for name, _ in WebSearchClient("tv").sources()][0], "Tavily")

    def test_time_sensitive_phrasings_trigger_a_search(self):
        for query in ("今天上海天气", "DeepSeek 最近发布了什么", "残雪目前赔率多少", "英伟达股价"):
            self.assertTrue(needs_web_search(query), query)
        for query in ("内联函数是什么", "再讲一遍", "你好"):
            self.assertFalse(needs_web_search(query), query)
