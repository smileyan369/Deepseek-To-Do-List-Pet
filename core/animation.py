from __future__ import annotations

from enum import Enum
import random
import time

class PetState(str, Enum):
    IDLE = "idle"; SLEEPING = "sleeping"; DRAGGING = "dragging"; WAKING = "waking"

class AnimationStateMachine:
    """Single state owner: interaction always wins over idle sleep."""
    def __init__(self, rng: random.Random | None = None):
        self.rng = rng or random.Random()
        self.state = PetState.IDLE
        # Start the idle countdown at construction time. A zero origin would
        # look like hours of inactivity because monotonic clocks are absolute.
        self.last_interaction_ms = time.monotonic_ns() // 1_000_000
        self.idle_after_ms = self.rng.randint(120_000, 300_000)

    def interact(self, now_ms: int) -> None:
        self.last_interaction_ms = now_ms
        self.idle_after_ms = self.rng.randint(120_000, 300_000)
        if self.state == PetState.SLEEPING:
            self.state = PetState.WAKING
        elif self.state != PetState.DRAGGING:
            self.state = PetState.IDLE

    def drag(self, active: bool, now_ms: int) -> None:
        self.state = PetState.DRAGGING if active else PetState.IDLE
        self.interact(now_ms)

    def tick(self, now_ms: int) -> PetState:
        if self.state == PetState.WAKING:
            self.state = PetState.IDLE
        elif self.state == PetState.IDLE and now_ms - self.last_interaction_ms >= self.idle_after_ms:
            self.state = PetState.SLEEPING
        return self.state
