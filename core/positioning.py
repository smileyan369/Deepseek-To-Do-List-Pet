from __future__ import annotations

from dataclasses import dataclass

@dataclass(frozen=True)
class Rect:
    x: int; y: int; width: int; height: int
    def contains(self, x: int, y: int) -> bool:
        return self.x <= x < self.x + self.width and self.y <= y < self.y + self.height

def restore_position(saved: tuple[int, int] | list[int] | None, screens: list[Rect], size: tuple[int, int]) -> tuple[int, int]:
    """Keeps a restored pet visible, returning a primary-screen placement if needed."""
    primary = screens[0] if screens else Rect(0, 0, 1920, 1080)
    x, y = saved if saved and len(saved) == 2 else (primary.x + 40, primary.y + 80)
    # The stored point is the window's top-left. It can be near an edge while
    # the centre lies outside the work area, so identify its screen before clamping.
    if not any(s.contains(x, y) for s in screens):
        x, y = primary.x + 40, primary.y + 80
    screen = next((s for s in screens if s.contains(x, y)), primary)
    return (max(screen.x, min(x, screen.x + screen.width - size[0])),
            max(screen.y, min(y, screen.y + screen.height - size[1])))


def fit_overlay_position(position: tuple[int, int], size: tuple[int, int], screen: Rect,
                         margin: int = 8) -> tuple[int, int]:
    """Clamp a floating panel to one monitor's available work area."""
    min_x, min_y = screen.x + margin, screen.y + margin
    max_x = max(min_x, screen.x + screen.width - size[0] - margin)
    max_y = max(min_y, screen.y + screen.height - size[1] - margin)
    return (max(min_x, min(position[0], max_x)),
            max(min_y, min(position[1], max_y)))
