from __future__ import annotations

"""Prepare locally generated character strips for the desktop pet."""

from collections import deque
from pathlib import Path
import shutil

from PIL import Image, ImageOps


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "\u4eba\u7269\u8bbe\u8ba1\u56fe\u7247"
OUTPUT = ROOT / "assets" / "deepseek"
CELL_W, CELL_H = 256, 352
CONTENT_W, CONTENT_H = 238, 338


def remove_green(image: Image.Image) -> Image.Image:
    """Turn the green-screen background transparent with a feathered edge."""
    rgba = image.convert("RGBA")
    pixels = rgba.load()
    for y in range(rgba.height):
        for x in range(rgba.width):
            red, green, blue, alpha = pixels[x, y]
            dominance = green - max(red, blue)
            if green >= 20 and dominance > 6:
                edge_alpha = max(0, min(255, 255 - (dominance - 6) * 7))
                # Neutralize chroma spill before lowering alpha so scaled edges
                # do not acquire a bright green outline.
                green = min(green, max(red, blue) + 2)
                pixels[x, y] = (red, green, blue, min(alpha, edge_alpha))
    return rgba


def remove_small_components(image: Image.Image, min_area: int = 1200) -> Image.Image:
    """Remove detached decorations such as generated motion marks."""
    alpha = image.getchannel("A")
    width, height = image.size
    active = bytearray(1 if value > 24 else 0 for value in alpha.getdata())
    seen = bytearray(width * height)
    alpha_data = bytearray(alpha.tobytes())

    for start, value in enumerate(active):
        if not value or seen[start]:
            continue
        queue = deque([start])
        seen[start] = 1
        component: list[int] = []
        while queue:
            current = queue.popleft()
            component.append(current)
            x, y = current % width, current // width
            for nx, ny in (
                (x - 1, y - 1), (x, y - 1), (x + 1, y - 1),
                (x - 1, y), (x + 1, y),
                (x - 1, y + 1), (x, y + 1), (x + 1, y + 1),
            ):
                if 0 <= nx < width and 0 <= ny < height:
                    neighbor = ny * width + nx
                    if active[neighbor] and not seen[neighbor]:
                        seen[neighbor] = 1
                        queue.append(neighbor)
        if len(component) < min_area:
            for index in component:
                alpha_data[index] = 0

    image.putalpha(Image.frombytes("L", image.size, bytes(alpha_data)))
    return image


def split_source(path: Path, *, clean_decorations: bool = False) -> list[Image.Image]:
    image = remove_green(Image.open(path))
    cell_width = image.width / 4
    frames = [
        image.crop((round(index * cell_width), 0, round((index + 1) * cell_width), image.height))
        for index in range(4)
    ]
    if clean_decorations:
        frames = [remove_small_components(frame) for frame in frames]
    return frames


def frame_bbox(frame: Image.Image) -> tuple[int, int, int, int]:
    bbox = frame.getchannel("A").point(lambda value: 255 if value > 8 else 0).getbbox()
    if bbox is None:
        raise ValueError("Character frame has no visible content")
    return bbox


def render_strip(frames: list[Image.Image], output: Path) -> None:
    boxes = [frame_bbox(frame) for frame in frames]
    max_width = max(box[2] - box[0] for box in boxes)
    max_height = max(box[3] - box[1] for box in boxes)
    scale = min(CONTENT_W / max_width, CONTENT_H / max_height)

    result = Image.new("RGBA", (CELL_W * 4, CELL_H), (0, 0, 0, 0))
    for index, (frame, box) in enumerate(zip(frames, boxes)):
        trimmed = frame.crop(box)
        size = (max(1, round(trimmed.width * scale)), max(1, round(trimmed.height * scale)))
        trimmed = trimmed.resize(size, Image.Resampling.LANCZOS)
        x = index * CELL_W + (CELL_W - trimmed.width) // 2
        y = CELL_H - 8 - trimmed.height
        result.alpha_composite(trimmed, (x, y))
    output.parent.mkdir(parents=True, exist_ok=True)
    result.save(output)


def mirror_strip(path: Path, output: Path) -> None:
    strip = Image.open(path).convert("RGBA")
    mirrored = Image.new("RGBA", strip.size, (0, 0, 0, 0))
    for index in range(4):
        frame = strip.crop((index * CELL_W, 0, (index + 1) * CELL_W, CELL_H))
        mirrored.alpha_composite(ImageOps.mirror(frame), (index * CELL_W, 0))
    mirrored.save(output)


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    sources = {
        "idle": "idle.png",
        "running_right": "running-right.png",
        "sleeping": "sleeping.png",
        "waking": "waking.png",
        "waving": "waving.png",
        "waiting": "waiting.png",
        "review": "review.png",
        "jumping": "jumping.png",
    }

    render_strip(
        split_source(SOURCE / sources["idle"], clean_decorations=True),
        OUTPUT / "idle.png",
    )
    render_strip(
        split_source(SOURCE / sources["running_right"], clean_decorations=True),
        OUTPUT / "running_right.png",
    )
    mirror_strip(OUTPUT / "running_right.png", OUTPUT / "running_left.png")
    render_strip(
        split_source(SOURCE / sources["waving"], clean_decorations=True),
        OUTPUT / "waving.png",
    )
    render_strip(
        split_source(SOURCE / sources["waiting"], clean_decorations=True),
        OUTPUT / "waiting.png",
    )
    render_strip(
        split_source(SOURCE / sources["review"], clean_decorations=True),
        OUTPUT / "review.png",
    )
    render_strip(
        split_source(SOURCE / sources["jumping"], clean_decorations=True),
        OUTPUT / "jumping.png",
    )

    render_strip(
        split_source(SOURCE / sources["sleeping"], clean_decorations=True),
        OUTPUT / "sleeping.png",
    )
    render_strip(
        split_source(SOURCE / sources["waking"], clean_decorations=True),
        OUTPUT / "waking.png",
    )

    for state in ("failed", "working"):
        shutil.copyfile(OUTPUT / "idle.png", OUTPUT / f"{state}.png")


if __name__ == "__main__":
    main()
