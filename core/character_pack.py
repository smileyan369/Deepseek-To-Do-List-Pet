from __future__ import annotations

"""Portable character-pack metadata and safe discovery for the workshop."""

from dataclasses import dataclass
import json
import re
from pathlib import Path


STATE_NAMES = (
    "idle", "running_right", "running_left", "waving", "jumping",
    "failed", "waiting", "working", "review", "sleeping", "waking",
)
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


@dataclass(frozen=True)
class StateSpec:
    name: str
    durations_ms: tuple[int, ...]
    loop: bool
    sheet: Path | None
    row: int
    frames: tuple[Path, ...]
    frames_per_strip: int

    @property
    def frame_count(self) -> int:
        if self.frames:
            return len(self.frames) * self.frames_per_strip
        return len(self.durations_ms)


@dataclass(frozen=True)
class CharacterPack:
    root: Path
    id: str
    name: str
    size: tuple[int, int]
    cell_size: tuple[int, int]
    anchor: tuple[int, int]
    hit_area: tuple[int, int, int, int]
    theme: dict
    states: dict[str, StateSpec]
    random_action_seconds: tuple[int, int]
    random_actions: tuple[str, ...]

    @classmethod
    def from_file(cls, config_path: Path) -> "CharacterPack":
        config_path = Path(config_path).resolve()
        raw = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("角色配置必须是 JSON 对象")
        root = config_path.parent
        pack_id = raw.get("id")
        name = raw.get("name", pack_id)
        if not isinstance(pack_id, str) or not _ID_RE.fullmatch(pack_id):
            raise ValueError("角色 id 只能包含字母、数字、点、下划线和短横线")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("角色 name 不能为空")

        size = _pair(raw.get("size"), "size")
        cell_size = _pair(raw.get("cell_size", [192, 208]), "cell_size")
        anchor = _pair(raw.get("anchor", [size[0] // 2, size[1] - 5]), "anchor")
        hit_area = _quad(raw.get("hit_area", [0, 0, size[0], size[1]]), "hit_area")
        if size[0] > 1000 or size[1] > 1200 or cell_size[0] > 2000 or cell_size[1] > 2000:
            raise ValueError("角色尺寸过大")
        if not isinstance(raw.get("states"), dict):
            raise ValueError("角色必须提供 states")

        states: dict[str, StateSpec] = {}
        for state_name, state_raw in raw["states"].items():
            if state_name not in STATE_NAMES or not isinstance(state_raw, dict):
                continue
            frames_raw = state_raw.get("frames", [])
            if frames_raw and (not isinstance(frames_raw, list) or not all(isinstance(x, str) for x in frames_raw)):
                raise ValueError(f"状态 {state_name} 的 frames 必须是路径数组")
            frames = tuple(_safe_asset(root, value) for value in frames_raw)
            sheet_value = state_raw.get("sheet")
            sheet = _safe_asset(root, sheet_value) if sheet_value else None
            if not frames and sheet is None:
                raise ValueError(f"状态 {state_name} 缺少 sheet 或 frames")
            frames_per_strip = int(state_raw.get("frames_per_strip", 4))
            if frames and not 1 <= frames_per_strip <= 32:
                raise ValueError(f"状态 {state_name} 的 frames_per_strip 无效")
            row = int(state_raw.get("row", 0))
            durations_raw = state_raw.get("durations_ms")
            if durations_raw is None:
                count = len(frames) * frames_per_strip if frames else 1
                durations = tuple(150 for _ in range(count))
            elif isinstance(durations_raw, list) and durations_raw and all(isinstance(x, (int, float)) for x in durations_raw):
                durations = tuple(max(16, int(x)) for x in durations_raw)
            else:
                raise ValueError(f"状态 {state_name} 的 durations_ms 无效")
            if frames and len(durations) > len(frames) * frames_per_strip:
                raise ValueError(f"状态 {state_name} 的时长数量超过帧数")
            states[state_name] = StateSpec(state_name, durations, bool(state_raw.get("loop", True)), sheet, row, frames, frames_per_strip)

        if "idle" not in states:
            raise ValueError("角色必须提供 idle 状态")
        for required in ("running_left", "running_right"):
            if required not in states:
                states[required] = states["idle"]
        random_raw = raw.get("random_action_seconds", [20, 45])
        random_range = _pair(random_raw, "random_action_seconds")
        if random_range[0] < 1 or random_range[1] < random_range[0]:
            raise ValueError("random_action_seconds 无效")
        actions_raw = raw.get("random_actions", ["idle"])
        if not isinstance(actions_raw, list) or not actions_raw:
            raise ValueError("random_actions 必须是非空数组")
        random_actions = tuple(value for value in actions_raw if isinstance(value, str) and value in states)
        if not random_actions:
            random_actions = ("idle",)
        return cls(root, pack_id, name.strip(), size, cell_size, anchor, hit_area,
                   raw.get("theme", {}), states, random_range, random_actions)


def discover_character_packs(roots: list[Path]) -> list[CharacterPack]:
    """Discover direct child folders containing character.json; bad packs are skipped."""
    found: list[CharacterPack] = []
    seen: set[str] = set()
    for root in roots:
        root = Path(root)
        if not root.is_dir():
            continue
        candidates = [root] if (root / "character.json").is_file() else sorted(root.iterdir())
        for candidate in candidates:
            config = candidate / "character.json"
            if not config.is_file():
                continue
            try:
                pack = CharacterPack.from_file(config)
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            if pack.id not in seen:
                seen.add(pack.id)
                found.append(pack)
    return found


def write_workshop_template(folder: Path) -> None:
    """Create non-destructive starter files in the user-facing workshop folder."""
    folder = Path(folder); folder.mkdir(parents=True, exist_ok=True)
    readme = folder / "创意工坊角色包说明.txt"
    example = folder / "character.example.json"
    if not readme.exists():
        readme.write_text(
            "每个角色使用一个独立文件夹，文件夹中放 character.json 和 frames 文件夹。\n"
            "默认格式：每帧 256x352，4 帧横向排列为 1024x352 PNG，程序显示为 128x176。\n"
            "把 character.example.json 复制到角色文件夹并改名为 character.json，再按配置中的文件名放入帧条。\n"
            "必需状态：idle、running_left、running_right；其他状态缺失时会使用待机或对应替代动作。\n"
            "角色 id 必须唯一，只能使用英文、数字、点、下划线和短横线。\n",
            encoding="utf-8",
        )
    if not example.exists():
        example.write_text(json.dumps(_gpt_strip_template(), ensure_ascii=False, indent=2), encoding="utf-8")


def _gpt_strip_template() -> dict:
    def state(filename: str, durations: list[int], loop: bool = True) -> dict:
        return {"frames": [f"frames/{filename}"], "frames_per_strip": 4,
                "durations_ms": durations, "loop": loop}
    return {
        "id": "my-deepsea-pet",
        "name": "我的深海桌宠",
        "size": [128, 176],
        "cell_size": [256, 352],
        "anchor": [64, 170],
        "hit_area": [12, 8, 104, 165],
        "theme": {"navy": "#132a56", "light_blue": "#9fd9f3", "accent": "#35c9d0", "white": "#f7fbff"},
        "states": {
            "idle": state("idle-01.png", [320, 140, 140, 520]),
            "running_right": state("running-right-01.png", [120, 120, 120, 120]),
            "running_left": state("running-left-01.png", [120, 120, 120, 120]),
            "waving": state("wave-01.png", [140, 140, 140, 260], False),
            "jumping": state("success-01.png", [140, 140, 140, 260], False),
            "failed": state("failed-01.png", [160, 160, 160, 300], False),
            "waiting": state("waiting-01.png", [180, 180, 180, 300]),
            "review": state("review-01.png", [170, 170, 170, 300]),
            "sleeping": state("sleeping-01.png", [320, 320, 320, 520]),
            "waking": state("waking-01.png", [160, 160, 160, 280], False),
        },
        "random_action_seconds": [20, 45],
        "random_actions": ["idle", "waving", "waiting"],
    }


def _safe_asset(root: Path, value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("角色素材路径必须位于角色包目录内")
    resolved = (root / path).resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError("角色素材路径越界")
    if not resolved.is_file():
        raise ValueError(f"角色素材不存在: {value}")
    return resolved


def _pair(value, label: str) -> tuple[int, int]:
    if not isinstance(value, list) or len(value) != 2 or not all(isinstance(x, (int, float)) for x in value):
        raise ValueError(f"{label} 必须是两个数字")
    result = (int(value[0]), int(value[1]))
    if min(result) <= 0:
        raise ValueError(f"{label} 必须为正数")
    return result


def _quad(value, label: str) -> tuple[int, int, int, int]:
    if not isinstance(value, list) or len(value) != 4 or not all(isinstance(x, (int, float)) for x in value):
        raise ValueError(f"{label} 必须是四个数字")
    result = tuple(int(x) for x in value)
    if result[2] <= 0 or result[3] <= 0:
        raise ValueError(f"{label} 尺寸必须为正数")
    return result
