from __future__ import annotations

from dataclasses import dataclass

@dataclass(frozen=True)
class PressResult:
    kind: str  # click, drag, ignored

def classify_press(duration_ms: int, distance_px: float, long_started: bool = False) -> PressResult:
    """A click is under 200ms and stays within 5px; either threshold starts dragging."""
    if long_started or duration_ms >= 200 or distance_px >= 6:
        return PressResult("drag")
    if duration_ms < 200 and distance_px < 6:
        return PressResult("click")
    return PressResult("ignored")
