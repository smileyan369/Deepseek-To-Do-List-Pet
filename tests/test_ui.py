import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import json
import unittest
import tempfile
from unittest.mock import patch
from datetime import datetime, timedelta
from pathlib import Path
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage, QInputMethodEvent, QTextCursor
from app.task_views import TaskEditor, TaskRow, TickCheckBox
from app.chat_views import ApiConfigDialog, ChatInputPanel, ChatRunner, SpeechBubble
from app.global_hotkey import GlobalVisibilityHotkey
from app.main_window import PetWindow
from app.character import CharacterWidget
from core.models import Task
from core.store import DataStore
from core.character_pack import CharacterPack
from core.chat import ChatConfig

app = QApplication.instance() or QApplication([])

class UiTests(unittest.TestCase):
    def test_global_visibility_hotkey_registers_dispatches_and_unregisters(self):
        import ctypes
        from ctypes import wintypes
        from PySide6.QtCore import QByteArray

        class NativeApi:
            def __init__(self): self.register_args = None; self.unregistered = False
            def RegisterHotKey(self, *args): self.register_args = args; return 1
            def UnregisterHotKey(self, *args): self.unregistered = True; return 1

        class FakeApp:
            def installNativeEventFilter(self, value): self.installed = value
            def removeNativeEventFilter(self, value): self.removed = value

        native = NativeApi(); fake_app = FakeApp(); triggered = []
        hotkey = GlobalVisibilityHotkey(fake_app, lambda: triggered.append(True), native)
        self.assertTrue(hotkey.register())
        self.assertEqual(native.register_args[1], hotkey.HOTKEY_ID)
        self.assertEqual(native.register_args[2], hotkey.MOD_CONTROL | hotkey.MOD_SHIFT | hotkey.MOD_NOREPEAT)
        self.assertEqual(native.register_args[3], hotkey.VK_Z)
        message = wintypes.MSG(); message.message = hotkey.WM_HOTKEY; message.wParam = hotkey.HOTKEY_ID
        handled, _ = hotkey.nativeEventFilter(QByteArray(b"windows_generic_MSG"), ctypes.addressof(message))
        self.assertTrue(handled); self.assertEqual(triggered, [True])
        hotkey.unregister(); self.assertTrue(native.unregistered); self.assertFalse(hotkey.registered)

    def test_global_visibility_hotkey_reports_registration_conflict(self):
        class NativeApi:
            def RegisterHotKey(self, *args): return 0
        class FakeApp: pass
        hotkey = GlobalVisibilityHotkey(FakeApp(), lambda: None, NativeApi())
        self.assertFalse(hotkey.register()); self.assertFalse(hotkey.registered)

    def _make_strip_pack(self, folder):
        pack_root = Path(folder) / "deepseek"
        (pack_root / "frames").mkdir(parents=True)
        image = QImage(64, 80, QImage.Format_RGBA8888); image.fill(0)
        image.save(str(pack_root / "frames" / "idle.png")); image.save(str(pack_root / "frames" / "run.png"))
        config = {
            "id": "deepseek", "name": "DeepSeek 娘化版", "size": [32, 40], "cell_size": [16, 20],
            "states": {
                "idle": {"frames": ["frames/idle.png"], "frames_per_strip": 4, "durations_ms": [40, 40, 40, 40]},
                "running_left": {"frames": ["frames/run.png"], "frames_per_strip": 4, "durations_ms": [40, 40, 40, 40]},
                "running_right": {"frames": ["frames/run.png"], "frames_per_strip": 4, "durations_ms": [40, 40, 40, 40]},
            },
        }
        (pack_root / "character.json").write_text(json.dumps(config), encoding="utf-8")
        return CharacterPack.from_file(pack_root / "character.json")

    def test_checked_box_renders_different_pixels(self):
        box = TickCheckBox("无截止时间"); box.resize(120, 28); box.show(); app.processEvents()
        before = box.grab().toImage(); box.setChecked(True); app.processEvents(); after = box.grab().toImage()
        changed = sum(before.pixel(x, y) != after.pixel(x, y) for y in range(20) for x in range(20))
        self.assertTrue(box.isChecked()); self.assertGreater(changed, 8)

    def test_editor_defaults_ranges_and_no_due(self):
        editor = TaskEditor(); editor.open_task()
        self.assertEqual(editor.hour.count(), 24); self.assertEqual(editor.minute.count(), 60)
        self.assertEqual(editor.hour.itemText(0), "00"); self.assertEqual(editor.minute.itemText(59), "59")
        editor.no_due.setChecked(True); self.assertFalse(editor.date.isEnabled()); self.assertFalse(editor.hour.isEnabled())

    def test_editor_accepts_real_keyboard_input(self):
        from PySide6.QtTest import QTest
        editor = TaskEditor(); editor.open_task(); QTest.qWait(30)
        editor.name.clear(); QTest.keyClicks(editor.name, "write todo")
        self.assertEqual(editor.name.text(), "write todo")

    def test_editor_enter_saves_task(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        editor = TaskEditor(); saved = []
        editor.saved.connect(lambda name, due, no_due: saved.append((name, due, no_due)))
        editor.open_task(); QTest.qWait(30); editor.name.setText("回车保存")
        QTest.keyClick(editor.name, Qt.Key_Return)
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0][0], "回车保存")

    def test_task_due_colors_cover_upcoming_overdue_and_no_due(self):
        now = datetime(2026, 8, 8, 12, 0)
        cases = (
            (now + timedelta(minutes=31), "normal"),
            (now + timedelta(minutes=30), "upcoming"),
            (now - timedelta(seconds=1), "overdue"),
            (None, "normal"),
        )
        for index, (due, expected) in enumerate(cases):
            task = Task(str(index), "时间颜色", due.isoformat() if due else None, now.isoformat(), index)
            row = TaskRow(task); row.update_due_style(now)
            self.assertEqual(row.due_status, expected)
            row.close()

    def test_long_input_keeps_caret_visible_and_supports_navigation(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        editor = TaskEditor(); editor.open_task(); QTest.qWait(30)
        value = "编写一个和C++相关的有用知识点并整理成文档"
        editor.name.setText(value); editor.name.setCursorPosition(len(value)); app.processEvents()
        end_rect = editor.name.inputMethodQuery(Qt.ImCursorRectangle)
        self.assertLessEqual(end_rect.right(), editor.name.contentsRect().right())
        QTest.keyClick(editor.name, Qt.Key_Left); QTest.keyClick(editor.name, Qt.Key_Left)
        self.assertEqual(editor.name.cursorPosition(), len(value) - 2)
        QTest.keyClick(editor.name, Qt.Key_Home); app.processEvents()
        self.assertEqual(editor.name.cursorPosition(), 0)
        self.assertGreaterEqual(editor.name.inputMethodQuery(Qt.ImCursorRectangle).left(), editor.name.contentsRect().left())
        QTest.keyClick(editor.name, Qt.Key_End); app.processEvents()
        self.assertEqual(editor.name.cursorPosition(), len(value))
        self.assertLessEqual(editor.name.inputMethodQuery(Qt.ImCursorRectangle).right(), editor.name.contentsRect().right())
        controls = (editor.name, editor.date, editor.hour, editor.minute, editor.no_due, editor.ok)
        for left, right in zip(controls, controls[1:]):
            self.assertLess(left.geometry().right(), right.geometry().left())

    def test_editor_accepts_chinese_ime_commit(self):
        editor = TaskEditor(); editor.open_task(); app.processEvents()
        editor.name.clear(); event = QInputMethodEvent(); event.setCommitString("中文待办")
        QApplication.sendEvent(editor.name, event)
        self.assertEqual(editor.name.text(), "中文待办")

    def test_empty_name_emits_validation_failure_and_stays_open(self):
        editor = TaskEditor(); failures = []
        editor.validation_failed.connect(lambda: failures.append(True))
        editor.open_task(); app.processEvents(); editor.name.clear(); editor._save()
        self.assertEqual(failures, [True])
        self.assertTrue(editor.isVisible())
        self.assertEqual(editor.name.placeholderText(), "任务名称不能为空")

    def test_edit_and_complete_signal(self):
        task = Task("one", "任务", datetime(2026, 2, 3, 4, 5).isoformat(), "2026-01-01T00:00:00", 4)
        editor = TaskEditor(); editor.open_task(task); self.assertEqual(editor.editing_id, "one"); self.assertEqual(editor.name.text(), "任务")
        edits = []
        row = TaskRow(task); received = []; row.complete_requested.connect(received.append); row.box.setChecked(True)
        from PySide6.QtTest import QTest
        QTest.qWait(450); self.assertEqual(received, ["one"])
        self.assertLessEqual(row.height(), 2)
        row.update_due_style(datetime(2026, 2, 3, 4, 4, 59))
        self.assertIn("#8490a2", row.name.styleSheet())

        editable = TaskRow(task); editable.edit_requested.connect(edits.append); editable.edit_button.click()
        self.assertEqual(edits, ["one"])

    def test_long_task_name_is_elided_with_full_tooltip(self):
        task = Task("long", "这是一个非常非常长的待办事项名称，用于验证稳定的省略显示", None, "2026-01-01T00:00:00", 1)
        row = TaskRow(task)
        self.assertTrue(row.name.text().endswith("...")); self.assertTrue(row.name.is_elided)
        with patch("app.task_views.QToolTip.showText") as show_text:
            row.name.show_full_name()
            show_text.assert_called_once()
            self.assertEqual(show_text.call_args.args[1], task.name)

    def test_main_window_add_hide_and_restore(self):
        with tempfile.TemporaryDirectory() as folder:
            pet = PetWindow(DataStore(folder)); pet.show_all()
            tray_labels = [action.text() for action in pet.tray.contextMenu().actions() if not action.isSeparator()]
            self.assertEqual(tray_labels, ["隐藏桌宠", "配置聊天 API", "开机自启", "创意工坊", "退出"])
            self.assertEqual([a.text() for a in pet.pet_menu.actions() if not a.isSeparator()], ["隐藏桌宠", "配置聊天 API", "退出"])
            pet.open_new(); app.processEvents(); pet.editor.name.setFocus(); app.processEvents()
            composing = QInputMethodEvent("zhongwen", [])
            QApplication.sendEvent(pet.editor.name, composing)
            pet._hide_overlays_if_outside()
            self.assertTrue(pet.editor.isVisible(), "中文输入法候选阶段不应触发自动收起")
            pet.editor.name.setText("保存的任务")
            pet.save_editor("保存的任务", datetime(2026, 8, 8, 9, 10), False)
            self.assertEqual(pet.tasks[0].name, "保存的任务")
            pet.hide_all(); self.assertTrue(pet.settings["hidden"]); self.assertFalse(pet.isVisible())
            pet.show_all(); self.assertFalse(pet.settings["hidden"]); pet.tray.hide(); pet.close()

    def test_workshop_strip_pack_can_replace_character(self):
        with tempfile.TemporaryDirectory() as folder:
            pack = self._make_strip_pack(folder)
            character = CharacterWidget(pack=pack); character.show(); app.processEvents()
            self.assertEqual(character.SIZE, (32, 40)); self.assertEqual(character.CELL_SIZE, (16, 20))
            character.set_state("running_right"); character.frame = 3; app.processEvents()
            self.assertEqual(character.state, "running_right")
            character.close()

    def test_workshop_selection_is_discovered_and_restored(self):
        with tempfile.TemporaryDirectory() as folder:
            store = DataStore(folder); self._make_strip_pack(Path(folder) / "characters")
            pet = PetWindow(store); self.assertIn("deepseek", pet.character_packs)
            pet.switch_character("deepseek")
            self.assertEqual(pet.character.pack.id, "deepseek"); self.assertEqual(pet.size().toTuple(), (32, 40))
            pet.tray.hide(); pet.close(); app.processEvents()
            restored = PetWindow(store)
            self.assertEqual(restored.character.pack.id, "deepseek")
            restored.tray.hide(); restored.close()

    def test_click_animation_plays_once_while_editor_stays_open(self):
        with tempfile.TemporaryDirectory() as folder:
            pet = PetWindow(DataStore(folder)); pet.show_all(); pet.open_new(); app.processEvents()
            self.assertEqual(pet.character.state, "waiting")
            self.assertFalse(pet.character.transient)
            pet.character.frame = len(pet.character.STATES["waiting"][1]) - 1
            pet.character.next_frame_at = 0
            pet.character._advance()
            self.assertEqual(pet.character.state, "waiting")
            self.assertFalse(pet.character.transient)
            pet._tick()
            self.assertTrue(pet.editor.isVisible())
            self.assertEqual(pet.character.state, "waiting")
            self.assertFalse(pet.character.transient)
            pet.hide_overlays(); self.assertEqual(pet.character.state, "idle")
            pet.tray.hide(); pet.close()

    def test_hover_animation_plays_once_while_task_list_stays_open(self):
        with tempfile.TemporaryDirectory() as folder:
            pet = PetWindow(DataStore(folder)); pet.show_all()
            pet.tasks = [Task.create("悬停测试", datetime(2026, 8, 8, 9, 10))]
            pet.refresh(); pet.show_list(); app.processEvents()
            self.assertTrue(pet.list.isVisible())
            self.assertEqual(pet.character.state, "review")
            self.assertFalse(pet.character.transient)
            pet.character.frame = len(pet.character.STATES["review"][1]) - 1
            pet.character.next_frame_at = 0
            pet.character._advance(); pet._tick()
            self.assertTrue(pet.list.isVisible())
            self.assertEqual(pet.character.state, "review")
            self.assertFalse(pet.character.transient)
            pet.hide_overlays(); self.assertEqual(pet.character.state, "idle")
            pet.tray.hide(); pet.close()

    def test_character_renders_transparent_standing_and_sleeping(self):
        character = CharacterWidget(); character.show(); app.processEvents()
        self.assertEqual(character.pack.id, "deepsea-maid")
        self.assertEqual(character.SIZE, (112, 154))
        self.assertEqual(character.CELL_SIZE, (256, 352))
        self.assertEqual((character.sheet.width(), character.sheet.height()), (1024, 352))
        expected_states = {
            "idle", "running_right", "running_left", "waving", "jumping",
            "failed", "waiting", "working", "review", "sleeping", "waking",
        }
        self.assertTrue(expected_states.issubset(character.STATES))
        self.assertTrue(all(character.STATES[name][0] == 0 for name in expected_states))
        self.assertTrue(character.idle_resting); self.assertEqual(character.frame, 0)
        character.idle_rest_until = 0
        with patch("app.character.random.SystemRandom.choice", return_value="idle"):
            character._advance()
        self.assertFalse(character.idle_resting)
        character.frame = len(character.STATES["idle"][1]) - 1; character.next_frame_at = 0
        character._advance(); self.assertTrue(character.idle_resting); self.assertEqual(character.frame, 0)
        standing = character.grab().toImage(); self.assertEqual(standing.pixelColor(0, 0).alpha(), 0)
        character.set_state("sleeping"); app.processEvents()
        sleeping = character.grab().toImage(); self.assertEqual(sleeping.pixelColor(0, 0).alpha(), 0)
        character.frame = len(character.STATES["sleeping"][1]) - 1
        character.next_frame_at = 0; character._advance()
        self.assertEqual(character.state, "sleeping")
        self.assertEqual(character.frame, 3)
        character.play_once("waking"); self.assertTrue(character.transient)
        character.force_state("running_right")
        self.assertEqual(character.state, "running_right"); self.assertFalse(character.transient)
        character.close()

    def test_random_idle_action_starts_after_short_rest(self):
        character = CharacterWidget(); character.idle_rest_until = 0
        with patch("app.character.random.SystemRandom.choice", return_value="waving"):
            character._advance()
        self.assertEqual(character.state, "waving"); self.assertTrue(character.transient)
        character.close()

    def test_default_character_strips_are_clean_and_fixed_size(self):
        character = CharacterWidget()
        self.assertEqual(character.pack.random_action_seconds, (6, 10))
        self.assertEqual(character.pack.random_actions, ("idle", "waving", "waiting"))
        self.assertFalse(character.pack.states["sleeping"].loop)
        self.assertFalse(character.pack.states["waking"].loop)
        for state_name, spec in character.pack.states.items():
            self.assertEqual(spec.frame_count, 4, state_name)
            self.assertEqual(len(spec.frames), 1, state_name)
            image = QImage(str(spec.frames[0]))
            self.assertFalse(image.isNull(), state_name)
            self.assertEqual((image.width(), image.height()), (1024, 352), state_name)
            for x, y in ((0, 0), (255, 0), (256, 0), (1023, 351)):
                self.assertEqual(image.pixelColor(x, y).alpha(), 0, state_name)

            sampled_green = 0
            for y in range(0, image.height(), 4):
                for x in range(0, image.width(), 4):
                    color = image.pixelColor(x, y)
                    if color.alpha() > 32 and color.green() > max(color.red(), color.blue()) + 20:
                        sampled_green += 1
            self.assertLess(sampled_green, 100, state_name)
        character.close()

    def test_click_chooser_and_missing_api_reminder(self):
        with tempfile.TemporaryDirectory() as folder:
            pet = PetWindow(DataStore(folder)); pet.show_all(); pet.open_mode_chooser(); app.processEvents()
            self.assertTrue(pet.chooser.isVisible())
            with patch("app.main_window.QMessageBox.information") as message, patch.object(pet, "configure_chat_api", return_value=False) as configure:
                pet.open_chat()
                message.assert_called_once()
                configure.assert_called_once()
            self.assertFalse(pet.chat_active)
            def save_config():
                pet.chat_config_store.save(ChatConfig("https://example.test/v1", "model", "key"))
                return True
            with patch("app.main_window.QMessageBox.information"), patch.object(pet, "configure_chat_api", side_effect=save_config):
                pet.open_chat()
            self.assertTrue(pet.chat_active)
            pet.end_chat()
            pet.open_mode_chooser(); pet.open_new(); app.processEvents()
            self.assertTrue(pet.editor.isVisible())
            pet.tray.hide(); pet.close()

    def test_chat_config_input_and_bottom_anchored_bubble(self):
        config = ChatConfig("https://example.test/v1", "model", "key", True, "tavily-key")
        dialog = ApiConfigDialog(config)
        self.assertEqual(dialog.value(), config)
        panel = ChatInputPanel(); panel.show(); app.processEvents()
        event = QInputMethodEvent(); event.setCommitString("中文聊天")
        QApplication.sendEvent(panel.input, event)
        self.assertEqual(panel.input.toPlainText(), "中文聊天")

        bubble = SpeechBubble(); anchor = bubble.pos() + bubble.rect().bottomLeft(); anchor.setX(500); anchor.setY(400)
        bubble.begin(anchor); initial_height = bubble.height(); initial_bottom = bubble.geometry().bottom()
        bubble.enqueue("这是一段会自动换行并让气泡向上增长的较长聊天回复。" * 4)
        from PySide6.QtTest import QTest
        QTest.qWait(1800); app.processEvents()
        self.assertGreater(bubble.height(), initial_height)
        self.assertEqual(bubble.geometry().bottom(), initial_bottom)
        panel.close(); bubble.close(); dialog.close()

    def test_chat_enter_sends_and_close_button_or_escape_ends_session(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest

        class PendingRunner:
            cancelled = False
            def cancel(self):
                self.cancelled = True

        with tempfile.TemporaryDirectory() as folder:
            pet = PetWindow(DataStore(folder)); pet.show_all()
            pet.chat_config_store.save(ChatConfig("https://example.test/v1", "model", "key"))
            pet.open_chat(); QTest.qWait(50)
            received = []
            pet.chat_input.send_requested.disconnect(pet.send_chat)
            pet.chat_input.send_requested.connect(received.append)
            pet.chat_input.input.setPlainText("第一段")
            pet.chat_input.input.moveCursor(QTextCursor.End)
            QTest.keyClick(pet.chat_input.input, Qt.Key_Return, Qt.ShiftModifier)
            QTest.keyClicks(pet.chat_input.input, "second")
            self.assertEqual(received, [])
            QTest.keyClick(pet.chat_input.input, Qt.Key_Return)
            self.assertEqual(received, ["第一段\nsecond"])
            self.assertIn("Enter 发送", pet.chat_input.input.placeholderText())
            self.assertIn("Shift+Enter 换行", pet.chat_input.input.placeholderText())
            self.assertIn("Esc 退出", pet.chat_input.input.placeholderText())
            self.assertEqual(pet.chat_input.send.width(), pet.chat_input.input.height())
            self.assertEqual(pet.chat_input.send.height(), pet.chat_input.input.height())

            runner = PendingRunner(); pet.chat_runner = runner; pet.chat_input.set_busy(True)
            pet.end_chat()
            self.assertTrue(runner.cancelled)
            self.assertIsNone(pet.chat_runner)
            self.assertTrue(pet.chat_input.send.isEnabled())
            pet.open_chat(); QTest.qWait(30); QTest.mouseClick(pet.chat_input.send, Qt.LeftButton)
            self.assertFalse(pet.chat_active)
            pet.open_chat(); QTest.qWait(30); QTest.keyClick(pet.chat_input.input, Qt.Key_Escape)
            self.assertFalse(pet.chat_active)
            pet.open_chat(); QTest.qWait(30); pet.chat_input.clearFocus(); pet._schedule_hide(); QTest.qWait(350)
            self.assertTrue(pet.chat_active)
            pet.end_chat()
            pet.tray.hide(); pet.close()

    def test_search_failure_falls_back_to_offline_chat(self):
        from core.chat import ChatApiError

        class Search:
            def search(self, query): raise ChatApiError("搜索服务不可用")

        class Client:
            def stream(self, config, messages):
                self.messages = messages
                yield "离线回答"

        client = Client(); completed = []; failures = []
        runner = ChatRunner(
            ChatConfig("https://example.test/v1", "model", "key", True),
            [{"role": "system", "content": "规则"}, {"role": "user", "content": "搜索今天的新闻"}],
            client=client,
            search_client=Search(),
        )
        runner.finished.connect(completed.append); runner.failed.connect(failures.append)
        runner._run()
        self.assertEqual(completed, ["离线回答"]); self.assertEqual(failures, [])
        fallback = client.messages[-2]["content"]
        self.assertIn("联网搜索失败", fallback)
        self.assertIn("不要编造搜索结果", fallback)

    def test_long_chat_bubble_uses_vertical_scrollbar(self):
        from PySide6.QtCore import QPoint
        bubble = SpeechBubble(); bubble.begin(QPoint(500, 400))
        bubble.show_error("这是一段很长的回复，用来验证气泡会显示垂直滚动条。" * 30); app.processEvents()
        scrollbar = bubble.text.verticalScrollBar()
        self.assertGreater(scrollbar.maximum(), 0)
        scrollbar.setValue(scrollbar.maximum())
        self.assertEqual(scrollbar.value(), scrollbar.maximum())
        bubble.close()

    def test_overlays_are_kept_inside_current_screen(self):
        with tempfile.TemporaryDirectory() as folder:
            pet = PetWindow(DataStore(folder)); available = app.primaryScreen().availableGeometry()
            pet.move(available.right() - pet.width() + 1, available.bottom() - pet.height() + 1)

            def assert_visible(widget):
                geometry = widget.geometry()
                self.assertGreaterEqual(geometry.left(), available.left() + 8)
                self.assertGreaterEqual(geometry.top(), available.top() + 8)
                self.assertLessEqual(geometry.right(), available.right() - 8)
                self.assertLessEqual(geometry.bottom(), available.bottom() - 8)

            pet.open_mode_chooser(); app.processEvents(); assert_visible(pet.chooser)
            pet.open_new(); app.processEvents(); assert_visible(pet.editor)
            pet.tasks = [Task.create("屏幕边界", datetime(2026, 8, 8, 9, 10))]
            pet.refresh(); pet.editor.hide(); pet.show_list(); app.processEvents(); assert_visible(pet.list)
            pet.chat_config_store.save(ChatConfig("https://example.test/v1", "model", "key"))
            pet.open_chat(); app.processEvents(); assert_visible(pet.chat_input)
            pet.bubble.begin(pet._bubble_anchor()); app.processEvents(); assert_visible(pet.bubble)
            pet.tray.hide(); pet.close()
