"""Generate PWA PNG icons matching web/icon.svg (amber star on deep ink)."""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path("web")


def star_points(cx: float, cy: float, r_out: float, r_in: float, n: int = 4) -> list[tuple[float, float]]:
    """n-pointed star (4 points -> 8 spikes), like the SVG path."""
    pts: list[tuple[float, float]] = []
    for i in range(n * 2):
        r = r_out if i % 2 == 0 else r_in
        a = math.pi * i / n - math.pi / 2
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


def make(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # rounded square background
    radius = int(size * 0.22)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=(11, 14, 20, 255))
    d.rounded_rectangle(
        [size // 50, size // 50, size - size // 50, size - size // 50],
        radius=radius - size // 90, outline=(35, 42, 58, 255), width=max(2, size // 128),
    )
    # star (outward spikes long, inner short) + hollow center
    cx = cy = size / 2
    pts = star_points(cx, cy, size * 0.36, size * 0.075, n=4)
    d.polygon(pts, fill=(232, 179, 75, 255))
    r_center = size * 0.065
    d.ellipse([cx - r_center, cy - r_center, cx + r_center, cy + r_center], fill=(11, 14, 20, 255))
    r_core = size * 0.035
    d.ellipse([cx - r_core, cy - r_core, cx + r_core, cy + r_core], fill=(245, 207, 138, 255))
    return img


for px in (192, 512):
    make(px).save(OUT / f"icon-{px}.png")
    print(f"web/icon-{px}.png")
print("done")
