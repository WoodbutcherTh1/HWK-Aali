"""Generate the آلي Desktop app icon: HWK monogram on deep ink.

The owner asked for the three letters "HWK" on the icon — deep-ink rounded
square, gold border, bold gold HWK centered. Writes build-desktop/icon.ico
with every size Windows wants (256 -> 16).
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path("build-desktop/icon.ico")
BG = (37, 37, 35, 255)        # warm near-black (matches the app theme #252523)
GOLD = (232, 179, 75, 255)    # Aali gold #e8b34b
GOLD_SOFT = (245, 207, 138, 255)
SIZE = 256


def _font(size: int) -> ImageFont.FreeTypeFont:
    """Best available bold system font (Segoe UI Bold on Windows)."""
    candidates = [
        "C:/Windows/Fonts/segoeuib.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/tahoma.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def make() -> Image.Image:
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # rounded-square badge with a gold ring
    radius = int(SIZE * 0.22)
    d.rounded_rectangle([0, 0, SIZE - 1, SIZE - 1], radius=radius, fill=BG)
    d.rounded_rectangle(
        [2, 2, SIZE - 3, SIZE - 3], radius=radius - 1,
        outline=GOLD, width=4,
    )
    # the HWK monogram, optically centered
    font = _font(104)
    text = "HWK"
    left, top, right, bottom = d.textbbox((0, 0), text, font=font)
    tw, th = right - left, bottom - top
    x = (SIZE - tw) // 2 - left
    y = (SIZE - th) // 2 - top
    d.text((x, y), text, font=font, fill=GOLD)
    return img


if __name__ == "__main__":
    OUT.parent.mkdir(parents=True, exist_ok=True)
    icon = make()
    icon.save(
        OUT,
        sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)],
    )
    # PNG set too (docs / mac resources)
    for s in (512, 256, 128):
        icon.resize((s, s), Image.LANCZOS).save(f"build-desktop/icon-{s}.png")
    print(f"icon written: {OUT}")
