from __future__ import annotations

"""Animated character widget with built-in and workshop pack support."""

import random
import sys
from pathlib import Path

from PySide6.QtCore import QElapsedTimer, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QWidget

from core.character_pack import CharacterPack, StateSpec


def asset_path(relative: str) -> Path:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    return root / relative


def default_character_pack() -> CharacterPack:
    return CharacterPack.from_file(asset_path("assets/character.json"))


class CharacterWidget(QWidget):
    hovered = Signal(bool)

    # Kept as class defaults for callers and tests; an instance adopts its pack.
    SIZE = (112, 154)
    CELL_SIZE = (256, 352)
    IDLE_REST_RANGE_MS = (6_000, 10_000)

    def __init__(self, parent=None, pack: CharacterPack | None = None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._advance)
        self.clock = QElapsedTimer()
        self.pack = pack or default_character_pack()
        self._load_pack_assets(self.pack)
        self.state = "idle"
        self.frame = 0
        self.transient = False
        self.idle_resting = True
        self.idle_rest_until = random.SystemRandom().randint(*self.idle_rest_range_ms)
        self.clock.start()
        self.next_frame_at = self.idle_rest_until
        self._set_timer_interval()
        self.timer.start()

    def load_pack(self, pack: CharacterPack) -> None:
        """Replace visuals in place while leaving the todo and window objects alive."""
        self._load_pack_assets(pack)
        self.state = "idle"
        self.frame = 0
        self.transient = False
        self.idle_resting = True
        self.idle_rest_until = random.SystemRandom().randint(*self.idle_rest_range_ms)
        self.clock.restart()
        self.next_frame_at = self.idle_rest_until
        self._set_timer_interval()
        self.update()

    def _set_timer_interval(self) -> None:
        """Use a slower refresh while the character is visibly resting."""
        resting = self.state == "idle" and self.idle_resting and not self.transient
        self.timer.setInterval(80 if resting else 33)

    def _load_pack_assets(self, pack: CharacterPack) -> None:
        images: dict[str, list[QImage]] = {}
        for name, spec in pack.states.items():
            sources: list[QImage] = []
            if spec.frames:
                for path in spec.frames:
                    image = QImage(str(path))
                    if image.isNull() or image.width() < pack.cell_size[0] * spec.frames_per_strip or image.height() < pack.cell_size[1]:
                        raise ValueError(f"角色 {pack.name} 的状态 {name} 帧条尺寸不足")
                    sources.append(image)
            elif spec.sheet:
                image = QImage(str(spec.sheet))
                frame_count = len(spec.durations_ms)
                if image.isNull() or image.width() < pack.cell_size[0] * frame_count or image.height() < pack.cell_size[1] * (spec.row + 1):
                    raise ValueError(f"角色 {pack.name} 的状态 {name} 精灵图尺寸不足")
                sources.append(image)
            images[name] = sources
        self.pack = pack
        self._state_specs: dict[str, StateSpec] = dict(pack.states)
        self._state_specs.setdefault("sleeping", self._state_specs.get("waiting", self._state_specs["idle"]))
        self._state_specs.setdefault("waking", self._state_specs.get("waving", self._state_specs["idle"]))
        images.setdefault("sleeping", images.get("waiting", images["idle"]))
        images.setdefault("waking", images.get("waving", images["idle"]))
        self._images = images
        self.SIZE = pack.size
        self.CELL_SIZE = pack.cell_size
        self.IDLE_REST_RANGE_MS = tuple(value * 1000 for value in pack.random_action_seconds)
        self.idle_rest_range_ms = self.IDLE_REST_RANGE_MS
        self.random_actions = pack.random_actions
        self.STATES = {name: (spec.row, list(spec.durations_ms)) for name, spec in self._state_specs.items()}
        self.sheet = self._images["idle"][0]
        self.setFixedSize(*self.SIZE)

    def set_state(self, state: str) -> None:
        if self.transient and state in {"idle", "waiting", "review", "sleeping"}:
            return
        self._start_state(state, False)

    def force_state(self, state: str) -> None:
        self.transient = False
        self._start_state(state, False, force=True)

    def play_once(self, state: str) -> None:
        self._start_state(state, True)

    def _start_state(self, state: str, transient: bool, force: bool = False) -> None:
        state = state if state in self._state_specs else "idle"
        if not force and state == self.state and transient == self.transient:
            return
        self.state = state
        self.transient = transient
        self.frame = 0
        self.clock.restart()
        self.idle_resting = state == "idle" and not transient
        if self.idle_resting:
            self.idle_rest_until = random.SystemRandom().randint(*self.idle_rest_range_ms)
            self.next_frame_at = self.idle_rest_until
        else:
            self.next_frame_at = self._state_specs[state].durations_ms[0]
        self._set_timer_interval()
        self.update()

    def _advance(self) -> None:
        elapsed = self.clock.elapsed()
        durations = self._state_specs[self.state].durations_ms
        spec = self._state_specs[self.state]
        if self.state == "idle" and self.idle_resting:
            if elapsed < self.idle_rest_until:
                return
            action = random.SystemRandom().choice(self.random_actions)
            if action != "idle":
                self._start_state(action, True)
                return
            self.idle_resting = False
            self.frame = 0
            self.clock.restart()
            self.next_frame_at = durations[0]
            self._set_timer_interval()
            return
        changed = False
        while elapsed >= self.next_frame_at:
            next_frame = self.frame + 1
            if next_frame >= len(durations):
                if self.transient:
                    self.transient = False
                    self._start_state("idle", False)
                    return
                if self.state == "idle":
                    self.frame = 0
                    self.idle_resting = True
                    self.clock.restart()
                    self.idle_rest_until = random.SystemRandom().randint(*self.idle_rest_range_ms)
                    self.next_frame_at = self.idle_rest_until
                    self._set_timer_interval()
                    changed = True
                    break
                if not spec.loop:
                    self.frame = len(durations) - 1
                    self.next_frame_at = 2**31
                    changed = True
                    break
                self.frame = 0
                self.clock.restart()
                self.next_frame_at = durations[0]
                changed = True
                break
            self.frame = next_frame
            self.next_frame_at += durations[self.frame]
            changed = True
        if changed:
            self.update()

    def enterEvent(self, event):
        self.hovered.emit(True)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.hovered.emit(False)
        super().leaveEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        spec = self._state_specs[self.state]
        if spec.frames:
            strip_index, frame_index = divmod(self.frame, spec.frames_per_strip)
            image = self._images[self.state][min(strip_index, len(self._images[self.state]) - 1)]
            source = QRectF(frame_index * self.CELL_SIZE[0], 0, self.CELL_SIZE[0], self.CELL_SIZE[1])
        else:
            image = self._images[self.state][0]
            source = QRectF(self.frame * self.CELL_SIZE[0], spec.row * self.CELL_SIZE[1], self.CELL_SIZE[0], self.CELL_SIZE[1])
        painter.drawImage(QRectF(0, 0, self.width(), self.height()), image, source)
