from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from PySide6.QtCore import QEvent, QPoint, QTimer, Qt
from PySide6.QtGui import QAction, QCursor, QIcon
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon, QWidget
from app.character import CharacterWidget, asset_path
from app.chat_views import ActionChooser, ApiConfigDialog, ChatInputPanel, ChatRunner, SpeechBubble
from app.global_hotkey import GlobalVisibilityHotkey
from app.task_views import TaskEditor, TaskList
from core.animation import AnimationStateMachine, PetState
from core.chat import ChatConfigStore, ChatMemory
from core.character_pack import CharacterPack, discover_character_packs, write_workshop_template
from core.interaction import classify_press
from core.models import Task, sorted_tasks
from core.positioning import Rect, fit_overlay_position, restore_position
from core.single_instance import SingleInstanceGuard
from core.store import DataStore


class PetWindow(QWidget):
    """Non-activating transparent shell; child overlays live in separate Tool windows."""
    def __init__(self, store: DataStore | None = None):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground); self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.store = store or DataStore(); self.tasks, self.settings = self.store.load()
        self.chat_config_store = ChatConfigStore(self.store.data_dir); self.chat_config = self.chat_config_store.load()
        self.chat_memory = ChatMemory(self.store.data_dir); self.chat_active = False; self.chat_runner = None; self.chat_received = False
        self.character_roots = self._character_roots()
        self.character_packs = self._discover_character_packs()
        selected_pack = self.character_packs.get(self.settings.get("character_id"), self._default_character_pack())
        self.machine = AnimationStateMachine(); self.character = CharacterWidget(self, selected_pack); self.setFixedSize(*self.character.SIZE); self.character.installEventFilter(self)
        self.character.hovered.connect(self._hovered); self.list = TaskList(); self.editor = TaskEditor()
        self.chooser = ActionChooser(); self.chat_input = ChatInputPanel(); self.bubble = SpeechBubble()
        self.list.edit_requested.connect(self.edit_task); self.list.delete_requested.connect(self.delete_task); self.list.complete_requested.connect(self.complete_task)
        self.editor.saved.connect(self.save_editor); self.editor.validation_failed.connect(lambda: self.character.play_once("failed")); self.list.activity.connect(self._schedule_hide); self.editor.activity.connect(self._schedule_hide)
        self.chooser.chat_selected.connect(self.open_chat); self.chooser.task_selected.connect(self.open_new); self.chooser.activity.connect(self._schedule_hide)
        self.chat_input.send_requested.connect(self.send_chat); self.chat_input.close_requested.connect(self.end_chat); self.chat_input.activity.connect(self._schedule_hide)
        self.press_at = 0; self.press_pos = QPoint(); self.long_started = False; self.drag_offset = QPoint()
        self.long_press_timer = QTimer(self); self.long_press_timer.setSingleShot(True); self.long_press_timer.timeout.connect(self._begin_long_press)
        self.hide_timer = QTimer(self); self.hide_timer.setSingleShot(True); self.hide_timer.timeout.connect(self._hide_overlays_if_outside)
        self.tick_timer = QTimer(self); self.tick_timer.timeout.connect(self._tick); self.tick_timer.start(250)
        self._restore_position(); self._create_tray(); self.refresh()
        if self.settings.get("autostart"):
            # Refresh the command after executable moves or product renames.
            self.settings["autostart"] = self._set_autostart(True)
            self.autostart_action.setChecked(self.settings["autostart"])
            self._save()
        QApplication.instance().installEventFilter(self)
        if self.settings.get("hidden"): self.hide_all()
        else: self.show()

    def _create_tray(self):
        self.app_icon = QIcon(str(asset_path("assets/app-icon.png")))
        self.setWindowIcon(self.app_icon)
        self.tray = QSystemTrayIcon(self.app_icon, self)
        self.tray.setToolTip("待办桌宠\nCtrl+Shift+Z 显示/隐藏")
        menu = QMenu()
        self.hide_action = QAction("隐藏桌宠", menu); self.hide_action.setCheckable(True); self.hide_action.triggered.connect(self.toggle_hidden)
        self.autostart_action = QAction("开机自启", menu); self.autostart_action.setCheckable(True); self.autostart_action.triggered.connect(self.toggle_autostart)
        api_action = QAction("配置聊天 API", menu); api_action.triggered.connect(self.configure_chat_api)
        self.character_menu = QMenu("创意工坊", menu)
        self._populate_character_menu()
        exit_action = QAction("退出", menu); exit_action.triggered.connect(self.quit)
        menu.addAction(self.hide_action); menu.addAction(api_action); menu.addSeparator(); menu.addAction(self.autostart_action); menu.addSeparator(); menu.addMenu(self.character_menu); menu.addSeparator(); menu.addAction(exit_action)
        self.tray.setContextMenu(menu); self.tray.activated.connect(lambda reason: self.toggle_hidden() if reason == QSystemTrayIcon.DoubleClick else None)
        self.hide_action.setChecked(bool(self.settings.get("hidden"))); self.autostart_action.setChecked(bool(self.settings.get("autostart"))); self.tray.show()

        self.pet_menu = QMenu()
        hide_pet = self.pet_menu.addAction("隐藏桌宠"); hide_pet.triggered.connect(self.hide_all)
        configure = self.pet_menu.addAction("配置聊天 API"); configure.triggered.connect(self.configure_chat_api)
        self.pet_menu.addSeparator()
        quit_pet = self.pet_menu.addAction("退出"); quit_pet.triggered.connect(self.quit)

    def _character_roots(self) -> list[Path]:
        app_root = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]
        return [self.store.data_dir / "characters", app_root / "characters"]

    def _default_character_pack(self) -> CharacterPack:
        return CharacterPack.from_file(Path(__file__).resolve().parents[1] / "assets" / "character.json")

    def _discover_character_packs(self) -> dict[str, CharacterPack]:
        packs = {self._default_character_pack().id: self._default_character_pack()}
        for pack in discover_character_packs(self.character_roots):
            packs.setdefault(pack.id, pack)
        return packs

    def _populate_character_menu(self):
        self.character_menu.clear()
        selected = self.character.pack.id if hasattr(self, "character") else self.settings.get("character_id")
        for pack_id, pack in self.character_packs.items():
            action = QAction(pack.name, self.character_menu); action.setCheckable(True); action.setChecked(pack_id == selected)
            action.triggered.connect(lambda checked=False, value=pack_id: self.switch_character(value))
            self.character_menu.addAction(action)
        open_folder = QAction("打开角色目录", self.character_menu); open_folder.triggered.connect(self.open_character_folder)
        self.character_menu.addSeparator(); self.character_menu.addAction(open_folder)

    def open_character_folder(self):
        folder = self.character_roots[0]; write_workshop_template(folder)
        if sys.platform == "win32":
            os.startfile(str(folder))

    def switch_character(self, pack_id: str):
        pack = self.character_packs.get(pack_id)
        if pack is None or pack.id == self.character.pack.id:
            return
        old_pos = self.pos()
        try:
            self.hide_overlays(); self.character.load_pack(pack); self.setFixedSize(*pack.size); self.move(old_pos)
        except (OSError, ValueError, RuntimeError) as exc:
            self.tray.showMessage("角色加载失败", str(exc), QSystemTrayIcon.Warning, 3500)
            return
        self.settings["character_id"] = pack.id; self._save(); self._populate_character_menu()

    def _restore_position(self):
        screens = [Rect(s.availableGeometry().x(), s.availableGeometry().y(), s.availableGeometry().width(), s.availableGeometry().height()) for s in QApplication.screens()]
        self.move(*restore_position(self.settings.get("pet_position"), screens, (self.width(), self.height())))

    def _screen_rect(self, point: QPoint | None = None) -> Rect:
        target = point or self.geometry().center()
        screen = QApplication.screenAt(target) or QApplication.primaryScreen()
        geometry = screen.availableGeometry()
        return Rect(geometry.x(), geometry.y(), geometry.width(), geometry.height())

    def _move_visible(self, widget: QWidget, x: int, y: int, screen: Rect | None = None):
        widget.move(*fit_overlay_position((x, y), (widget.width(), widget.height()), screen or self._screen_rect()))

    def _move_near_pet(self, widget: QWidget):
        screen = self._screen_rect()
        x = self.x() + self.width() // 2 - widget.width() // 2
        y = self.y() + self.height() - 5
        if y + widget.height() > screen.y + screen.height - 8:
            y = self.y() - widget.height() + 5
        self._move_visible(widget, x, y, screen)

    def _save(self): self.store.save(self.tasks, self.settings)
    def refresh(self): self.list.set_tasks(sorted_tasks(self.tasks))

    def eventFilter(self, watched, event):
        if self.chat_active and watched is self.character and event.type() in (QEvent.MouseButtonPress, QEvent.MouseButtonRelease):
            return True
        if watched is self.character:
            if event.type() == QEvent.MouseButtonPress and event.button() == Qt.RightButton:
                self.long_press_timer.stop(); self.press_at = 0; return True
            if event.type() == QEvent.MouseButtonRelease and event.button() == Qt.RightButton:
                self.hide_overlays(); self.pet_menu.exec(event.globalPosition().toPoint()); return True
            if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                self.press_at = time.monotonic_ns() // 1_000_000; self.press_pos = event.globalPosition().toPoint(); self.long_started = False; self.long_press_timer.start(200); self.hide_overlays(); self._interact(); return True
            if event.type() == QEvent.MouseMove and self.press_at:
                now = time.monotonic_ns() // 1_000_000
                distance = (event.globalPosition().toPoint() - self.press_pos).manhattanLength()
                if not self.long_started and (now - self.press_at >= 200 or distance >= 6): self._begin_long_press()
                if self.long_started:
                    target = event.globalPosition().toPoint() - self.drag_offset
                    if target.x() < self.x(): self.character.force_state("running_left")
                    elif target.x() > self.x(): self.character.force_state("running_right")
                    self.move(target); return True
            if event.type() == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
                now = time.monotonic_ns() // 1_000_000; distance = (event.globalPosition().toPoint() - self.press_pos).manhattanLength(); result = classify_press(now - self.press_at, distance, self.long_started)
                self.press_at = 0; self.long_press_timer.stop()
                if result.kind == "click": self.open_mode_chooser()
                if result.kind == "drag": self.machine.drag(False, now); self.character.force_state("idle"); self.settings["pet_position"] = [self.x(), self.y()]; self._save()
                return True
        return super().eventFilter(watched, event)

    def _begin_long_press(self):
        if not self.press_at or self.long_started: return
        self.long_started = True; self.drag_offset = QCursor.pos() - self.pos()
        self.machine.drag(True, time.monotonic_ns() // 1_000_000)
        # Directional running starts on the first actual move; the pressed pet
        # remains standing while the pointer has not moved yet.
        self.character.force_state("idle")

    def _hovered(self, inside: bool):
        self._interact()
        if inside and not any((self.editor.isVisible(), self.chooser.isVisible(), self.chat_active)): self.show_list()
        elif not inside: self._schedule_hide()

    def _interact(self):
        self.machine.interact(time.monotonic_ns() // 1_000_000)
        if self.machine.state == PetState.WAKING: self.character.play_once("waking")
        else: self.character.set_state("idle")
    def _tick(self):
        if self.character.transient: return
        now = time.monotonic_ns() // 1_000_000
        if any((self.editor.isVisible(), self.list.isVisible(), self.chooser.isVisible(), self.chat_active)):
            self.machine.interact(now)
            return
        state = self.machine.tick(now)
        self.character.set_state("sleeping" if state == PetState.SLEEPING else "idle")

    def show_list(self):
        if not self.tasks or self.editor.isVisible() or self.chooser.isVisible() or self.chat_active or self.settings.get("hidden"): return
        # First row center (6 + 5 + 17) aligns with the current pet's center.
        screen = self._screen_rect(); x = self.x() - self.list.width() + 8
        if x < screen.x + 8:
            x = self.x() + self.width() - 8
        self._move_visible(self.list, x, self.y() + self.height() // 2 - 28, screen)
        self.list.show(); self.character.set_state("review")

    def open_mode_chooser(self):
        self.hide_overlays(); self._interact()
        self._move_near_pet(self.chooser)
        self.chooser.show(); self.chooser.raise_(); self.character.play_once("waving")

    def open_new(self):
        self.chooser.hide(); self.list.hide(); self._interact(); self.editor.open_task(); self._move_near_pet(self.editor); self.character.set_state("waiting")

    def edit_task(self, task_id: str):
        task = next((t for t in self.tasks if t.id == task_id), None)
        if task: self.list.hide(); self.chooser.hide(); self.editor.open_task(task); self._move_near_pet(self.editor); self.character.set_state("review")

    def open_chat(self):
        self.chooser.hide()
        self.chat_config = self.chat_config_store.load()
        if not self.chat_config.configured:
            QMessageBox.information(self, "聊天 API 未配置", "当前没有保存有效的 API Key，请先完成聊天 API 配置。")
            if not self.configure_chat_api():
                self.character.force_state("idle")
                return
            self.chat_config = self.chat_config_store.load()
        self.list.hide(); self.editor.hide(); self.chat_active = True; self._interact()
        self._move_near_pet(self.chat_input)
        self.chat_input.set_busy(self.chat_runner is not None); self.chat_input.open_panel(); self.character.set_state("waiting")

    def send_chat(self, text: str):
        if self.chat_runner is not None:
            return
        self.chat_config = self.chat_config_store.load()
        if not self.chat_config.configured:
            self.chat_input.set_busy(False); self.end_chat()
            QMessageBox.information(self, "聊天 API 未配置", "请先右键桌宠，选择“配置聊天 API”。")
            return
        messages = self.chat_memory.messages_for(text)
        self.chat_memory.append("user", text)
        self.chat_received = False; self.chat_input.set_busy(True)
        self.bubble.begin(self._bubble_anchor()); self.bubble.show_error("想一想...")
        runner = ChatRunner(self.chat_config, messages); self.chat_runner = runner
        runner.chunk.connect(lambda chunk, current=runner: self._chat_chunk(current, chunk))
        runner.finished.connect(lambda answer, current=runner: self._chat_finished(current, answer))
        runner.failed.connect(lambda message, current=runner: self._chat_failed(current, message))
        runner.start()

    def _chat_chunk(self, runner: ChatRunner, chunk: str):
        if runner is not self.chat_runner or not self.chat_active:
            return
        if not self.chat_received:
            self.chat_received = True; self.bubble.begin(self._bubble_anchor())
        self.bubble.enqueue(chunk)

    def _chat_finished(self, runner: ChatRunner, answer: str):
        if runner is not self.chat_runner:
            return
        self.chat_memory.append("assistant", answer); self.chat_runner = None
        self.chat_input.set_busy(False)
        if self.chat_active and not self.chat_received:
            self.bubble.begin(self._bubble_anchor()); self.bubble.enqueue(answer)
        if self.chat_active:
            self.bubble.finalize()

    def _chat_failed(self, runner: ChatRunner, message: str):
        if runner is not self.chat_runner:
            return
        self.chat_runner = None; self.chat_input.set_busy(False)
        if self.chat_active:
            self.bubble.begin(self._bubble_anchor()); self.bubble.show_error(message)

    def _bubble_anchor(self) -> QPoint:
        return QPoint(self.x() + self.width() - 8, self.y() + 24)

    def _inside_chat_widget(self, watched) -> bool:
        return isinstance(watched, QWidget) and (
            watched in {self.chat_input, self.bubble}
            or self.chat_input.isAncestorOf(watched)
            or self.bubble.isAncestorOf(watched)
        )

    def end_chat(self):
        self.chat_active = False
        if self.chat_runner is not None:
            self.chat_runner.cancel(); self.chat_runner = None
        self.chat_input.hide(); self.chat_input.set_busy(False); self.bubble.hide(); self.bubble.reveal_timer.stop()
        self.character.force_state("idle")

    def configure_chat_api(self):
        dialog = ApiConfigDialog(self.chat_config_store.load(), self)
        cursor = QCursor.pos(); self._move_visible(dialog, cursor.x() - dialog.width() // 2, cursor.y() - 30, self._screen_rect(cursor))
        if dialog.exec() == ApiConfigDialog.Accepted:
            self.chat_config = dialog.value(); self.chat_config_store.save(self.chat_config)
            self.tray.showMessage("聊天 API", "配置已保存，可以开始聊天。", QSystemTrayIcon.Information, 2500)
            return True
        return False

    def save_editor(self, name, due, no_due):
        if self.editor.editing_id:
            task = next(t for t in self.tasks if t.id == self.editor.editing_id); task.name = name; task.due_at = None if no_due else due.isoformat()
        else: self.tasks.append(Task.create(name, None if no_due else due))
        self.editor.hide(); self.refresh(); self._save(); self.character.play_once("jumping")

    def delete_task(self, task_id: str):
        answer = QMessageBox.question(self, "删除任务", "确定删除这个任务吗？", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer == QMessageBox.Yes: self.tasks = [t for t in self.tasks if t.id != task_id]; self.refresh(); self._save()

    def complete_task(self, task_id: str):
        self.tasks = [t for t in self.tasks if t.id != task_id]; self.refresh(); self._save(); self.character.play_once("jumping")

    def hide_overlays(self):
        self.list.hide(); self.editor.hide(); self.chooser.hide()
        if self.chat_active: self.end_chat()
        else: self.character.force_state("idle")
    def _schedule_hide(self): self.hide_timer.start(250)
    def _hide_overlays_if_outside(self):
        if self.chat_active:
            return
        focus = QApplication.focusWidget()
        editor_has_focus = self.editor.isVisible() and (self.editor.isActiveWindow() or focus is self.editor or (focus is not None and self.editor.isAncestorOf(focus)))
        chat_has_focus = self.chat_active and (self.chat_input.isActiveWindow() or focus is self.chat_input or (focus is not None and self.chat_input.isAncestorOf(focus)))
        # Native IME candidate windows temporarily take the pointer outside the
        # editor. Keep the panel alive while any editor control owns input focus.
        if editor_has_focus or chat_has_focus:
            return
        if not any(w.underMouse() for w in (self.character, self.list, self.editor, self.chooser, self.chat_input)): self.hide_overlays()

    def hide_all(self): self.hide_overlays(); self.hide(); self.settings["hidden"] = True; self.hide_action.setChecked(True); self._save()
    def show_all(self): self.settings["hidden"] = False; self.hide_action.setChecked(False); self.show(); self._save()
    def toggle_hidden(self): self.show_all() if self.settings.get("hidden") else self.hide_all()

    def toggle_autostart(self):
        wanted = not self.settings.get("autostart", False); enabled = self._set_autostart(wanted)
        self.settings["autostart"] = enabled; self.autostart_action.setChecked(enabled); self._save()

    def _set_autostart(self, enabled: bool) -> bool:
        if sys.platform != "win32": return False
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\\Microsoft\\Windows\\CurrentVersion\\Run", 0, winreg.KEY_SET_VALUE)
            command = f'"{sys.executable}"' if getattr(sys, "frozen", False) else f'"{sys.executable}" "{Path(__file__).parent.parent / "main.py"}"'
            if enabled: winreg.SetValueEx(key, "DeepSeaTodoPet", 0, winreg.REG_SZ, command)
            else:
                try: winreg.DeleteValue(key, "DeepSeaTodoPet")
                except FileNotFoundError: pass
            winreg.CloseKey(key); return enabled
        except OSError: return False

    def quit(self):
        self._save(); self.tray.hide(); self.list.hide(); self.editor.hide(); self.chooser.hide(); self.chat_input.hide(); self.bubble.hide()
        QApplication.instance().removeEventFilter(self); QApplication.quit()
    def closeEvent(self, event): self.quit(); event.accept()


def run():
    instance = SingleInstanceGuard()
    if not instance.acquire():
        return
    app = QApplication(sys.argv); app.setQuitOnLastWindowClosed(False); app.setApplicationName("待办桌宠")
    app.setWindowIcon(QIcon(str(asset_path("assets/app-icon.png"))))
    window = PetWindow(); app.aboutToQuit.connect(window.tray.hide)
    visibility_hotkey = GlobalVisibilityHotkey(app, window.toggle_hidden)
    if not visibility_hotkey.register():
        window.tray.showMessage("快捷键不可用", "Ctrl+Shift+Z 已被其他程序占用，桌宠仍可通过托盘显示或隐藏。", QSystemTrayIcon.Warning, 4000)
    app.aboutToQuit.connect(visibility_hotkey.unregister)
    try:
        sys.exit(app.exec())
    finally:
        instance.release()
