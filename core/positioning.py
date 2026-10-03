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


def _fits(position: tuple[int, int], size: tuple[int, int], screen: Rect, margin: int) -> bool:
    x, y = position
    return (x >= screen.x + margin and y >= screen.y + margin
            and x + size[0] <= screen.x + screen.width - margin
            and y + size[1] <= screen.y + screen.height - margin)


def _overlaps(position: tuple[int, int], size: tuple[int, int], blocked: list[Rect]) -> bool:
    left, top = position
    right, bottom = left + size[0], top + size[1]
    return any(left < area.x + area.width and area.x < right
               and top < area.y + area.height and area.y < bottom for area in blocked)


# The bubble tucks a few pixels over the pet's top corner and clears its side by
# the same amount, so it reads as the pet's own balloon rather than a floating panel.
TOUCH = 6
GAP = 6


def balance_bubble_position(pet: Rect, size: tuple[int, int], screen: Rect,
                            blocked: list[Rect] | None = None,
                            margin: int = 8) -> tuple[int, int, bool]:
    """Place the balance bubble at the pet's upper right, falling back to the pet's
    upper left, then to the pet's side when it sits against the top edge. It stays
    tucked against the pet on every path - never docked to a screen corner. Returns
    the position and whether it sits on the right, so the bubble can point its tail
    at the pet. `blocked` holds panels already using that side (the task list):
    those are avoided first, but overlapping one beats moving away from the pet."""
    areas = blocked or []
    above = pet.y + 20 - size[1]                      # bottom edge tucked into the pet's head
    beside = pet.y + (pet.height - size[1]) // 2      # level with the pet, for edge cases
    candidates = [((pet.x + pet.width - TOUCH, above), True, True),
                  ((pet.x - size[0] + TOUCH, above), False, True),
                  ((pet.x + pet.width + GAP, beside), True, False),
                  ((pet.x - size[0] - GAP, beside), False, False)]
    for position, on_right, may_touch in candidates:
        placed = fit_overlay_position(position, size, screen, margin)
        if not _fits(position, size, screen, margin) or _overlaps(placed, size, areas):
            continue
        if not may_touch and _overlaps(placed, size, [pet]):
            continue
        return placed[0], placed[1], on_right
    for position, on_right, may_touch in candidates:
        placed = fit_overlay_position(position, size, screen, margin)
        if not _fits(position, size, screen, margin):
            continue
        if not may_touch and _overlaps(placed, size, [pet]):
            continue
        return placed[0], placed[1], on_right
    # Very small work area: stay on screen beside the pet rather than covering it.
    for position, on_right in ((pet.x - size[0] - GAP, beside), False), ((pet.x + pet.width + GAP, beside), True):
        placed = fit_overlay_position(position, size, screen, margin)
        if not _overlaps(placed, size, [pet]):
            return placed[0], placed[1], on_right
    placed = fit_overlay_position((pet.x - size[0] - GAP, beside), size, screen, margin)
    return placed[0], placed[1], False

