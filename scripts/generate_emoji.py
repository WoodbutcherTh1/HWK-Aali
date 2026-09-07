"""Generate custom emojis FROM SCRATCH with Pillow (no AI model needed).

Aali's generate_emoji tool backend: draws a designed emoji face on a
transparent canvas - emotion shapes, skin/color themes, accessories -
and saves a PNG (also used as sticker/asset). Runs in seconds on CPU,
so it works even while the GPU is busy training Aali.

Called by file_agent.file_tools.generate_emoji in a subprocess.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from PIL import Image, ImageDraw

EMOTION_SHAPES = {
    "happy": {"mouth": "smile", "eyes": "open"},
    "joy": {"mouth": "laugh", "eyes": "happy"},
    "love": {"mouth": "smile", "eyes": "heart"},
    "sad": {"mouth": "frown", "eyes": "tear"},
    "angry": {"mouth": "grit", "eyes": "angry"},
    "surprised": {"mouth": "o", "eyes": "wide"},
    "wink": {"mouth": "smile", "eyes": "wink"},
    "cool": {"mouth": "smile", "eyes": "shades"},
    "sleepy": {"mouth": "small", "eyes": "closed"},
    "laughing": {"mouth": "laugh", "eyes": "closed"},
}
DEFAULT_PALETTE = {
    "yellow": (255, 205, 66), "orange": (255, 161, 66), "red": (240, 98, 90),
    "green": (120, 200, 110), "blue": (95, 168, 240), "purple": (175, 122, 230),
    "pink": (245, 145, 180),
}


def _eye(draw: ImageDraw.ImageDraw, cx: int, cy: int, size: int,
         style: str, side: int, color=(60, 45, 30)) -> None:
    if style == "happy":            # ^ ^
        draw.arc([cx - size, cy - size, cx + size, cy + size], 200, 340,
                 fill=color, width=max(3, size // 3))
    elif style == "closed":         # -- --
        draw.line([cx - size, cy, cx + size, cy], fill=color, width=max(3, size // 3))
    elif style == "wink" and side < 0:
        draw.line([cx - size, cy, cx + size, cy], fill=color, width=max(3, size // 3))
    elif style == "heart" and side > 0:  # one heart eye
        r = int(size * 1.1)
        draw.ellipse([cx - r, cy - r, cx, cy + r - size // 3], fill=(235, 70, 90))
        draw.ellipse([cx, cy - r, cx + r, cy + r - size // 3], fill=(235, 70, 90))
        draw.polygon([(cx - r + 2, cy), (cx + r - 2, cy), (cx, cy + r + size // 2)],
                     fill=(235, 70, 90))
    elif style == "shades":
        w = int(size * 2.2)
        draw.rectangle([cx - w, cy - size // 2, cx + w, cy + size // 2 + 2], fill=(30, 30, 40))
        draw.line([cx - w, cy - size // 4, cx + w, cy - size // 4], fill=(30, 30, 40),
                  width=max(3, size // 2))
    elif style == "wide":
        r = int(size * 1.3)
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 255, 255),
                     outline=color, width=3)
        draw.ellipse([cx - r // 3, cy - r // 3, cx + r // 3, cy + r // 3], fill=color)
    elif style == "angry":
        draw.ellipse([cx - size, cy - size, cx + size, cy + size], fill=color)
        draw.line([cx - size, cy - int(size * 1.4), cx + size, cy - size // 2],
                  fill=color, width=max(4, size // 2))
    else:  # open
        draw.ellipse([cx - size, cy - size, cx + size, cy + size], fill=color)
        draw.ellipse([cx + size // 4 - 2, cy - size // 2, cx + size // 4 + 2,
                      cy - size // 2 + 4], fill=(255, 255, 255))


def _mouth(draw: ImageDraw.ImageDraw, cx: int, cy: int, size: int, style: str,
           color=(60, 45, 30)) -> None:
    if style == "smile":
        draw.arc([cx - size, cy - size, cx + size, cy + size], 20, 160,
                 fill=color, width=max(4, size // 3))
    elif style == "laugh":
        draw.pieslice([cx - size, cy - size // 2, cx + size, cy + size], 0, 180,
                      fill=color)
        draw.line([cx - size, cy + size // 4, cx + size, cy + size // 4],
                  fill=(255, 255, 255), width=max(3, size // 4))
    elif style == "frown":
        draw.arc([cx - size, cy, cx + size, cy + 2 * size], 200, 340,
                 fill=color, width=max(4, size // 3))
    elif style == "grit":
        draw.rectangle([cx - size, cy - size // 3, cx + size, cy + size // 2],
                       fill=color)
        for i in range(-2, 3):
            draw.line([cx + i * size // 3, cy - size // 3,
                       cx + i * size // 3, cy + size // 2],
                      fill=(255, 255, 255), width=2)
    elif style == "o":
        r = size // 2
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color)
    else:  # small
        draw.line([cx - size // 2, cy, cx + size // 2, cy], fill=color,
                  width=max(3, size // 3))


def render(out_path: Path, emotion: str = "happy", palette: str = "yellow",
           size: int = 256, accessory: str = "", text: str = "") -> dict:
    """Draw the emoji and save a transparent PNG. Returns a tool result dict."""
    emotion = emotion if emotion in EMOTION_SHAPES else "happy"
    shapes = EMOTION_SHAPES[emotion]
    base = DEFAULT_PALETTE.get(palette, DEFAULT_PALETTE["yellow"])
    scale = max(1, size // 256)
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    cx = cy = size // 2
    radius = int(size * 0.42)

    # face
    draw.ellipse([cx - radius, cy - radius, cx + radius, cy + radius],
                 fill=base + (255,), outline=tuple(max(0, c - 60) for c in base) + (255,),
                 width=4 * scale)
    if emotion == "angry":  # angry brows shadow
        draw.pieslice([cx - radius, cy - radius, cx + radius, cy + radius],
                      200, 250, fill=tuple(max(0, c - 30) for c in base) + (255,))
    # eyes
    eye_dx, eye_cy = int(radius * 0.45), int(cy - radius * 0.25)
    eye_size = int(radius * 0.16)
    _eye(draw, cx - eye_dx, eye_cy, eye_size, shapes["eyes"], side=-1)
    _eye(draw, cx + eye_dx, eye_cy, eye_size, shapes["eyes"], side=+1)
    # mouth
    _mouth(draw, cx, int(cy + radius * 0.35), int(radius * 0.4), shapes["mouth"])
    # tear for sad
    if shapes["eyes"] == "tear":
        tx, ty = cx - eye_dx, eye_cy + int(radius * 0.25)
        draw.pieslice([tx - 6 * scale, ty, tx + 6 * scale, ty + 18 * scale],
                      0, 180, fill=(110, 170, 240, 255))
    # accessories
    if accessory == "party":
        for i in range(7):
            angle = math.radians(i * 51)
            px = cx + int(math.cos(angle) * radius * 0.9)
            py = cy - radius + int(math.sin(angle) * radius * 0.9)
            colors = [(240, 80, 90), (90, 190, 120), (80, 140, 240), (240, 190, 60)]
            draw.ellipse([px - 5 * scale, py - 5 * scale, px + 5 * scale, py + 5 * scale],
                         fill=colors[i % 4] + (255,))
    elif accessory == "crown":
        points = [(cx - radius // 2, cy - radius), (cx - radius // 4, cy - radius - 18 * scale),
                  (cx, cy - radius), (cx + radius // 4, cy - radius - 18 * scale),
                  (cx + radius // 2, cy - radius)]
        draw.polygon(points, fill=(250, 200, 60, 255))
    elif accessory == "graduation":
        draw.polygon([(cx - radius, cy - radius * 0.7), (cx, cy - radius - 10 * scale),
                      (cx + radius, cy - radius * 0.7), (cx, cy - radius * 0.45)],
                     fill=(40, 40, 60, 255))
    if text:
        try:
            from PIL import ImageFont
            font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 22 * scale)
        except OSError:
            from PIL import ImageFont
            font = ImageFont.load_default()
        draw.text((cx, size - 16 * scale), text, font=font,
                  fill=(70, 60, 50, 255), anchor="mm")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(out_path, "PNG")
    return {"path": str(out_path), "emotion": emotion, "palette": palette,
            "size": f"{size}x{size}", "bytes": out_path.stat().st_size,
            "note": "emoji drawn from scratch (no AI model, works while GPU trains)"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Draw a custom emoji from scratch")
    parser.add_argument("--out", required=True)
    parser.add_argument("--emotion", default="happy",
                        choices=sorted(EMOTION_SHAPES) + list(EMOTION_SHAPES))
    parser.add_argument("--palette", default="yellow", choices=sorted(DEFAULT_PALETTE))
    parser.add_argument("--size", type=int, default=256)
    parser.add_argument("--accessory", default="", choices=("", "party", "crown", "graduation"))
    parser.add_argument("--text", default="")
    args = parser.parse_args()
    print(json.dumps(render(Path(args.out), args.emotion, args.palette,
                            args.size, args.accessory, args.text), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
