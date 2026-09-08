# -*- coding: utf-8 -*-
"""Render the Aali-CLI demo GIF (docs/assets/aali_cli_demo.gif).

Draws Claude Code-style terminal frames with PIL: window chrome (traffic
lights + title), a dark Linux-terminal body, truecolor text (Consolas for
the Latin/box content, Segoe UI + arabic_reshaper for the Arabic answer),
spinner animation, tool traces, and suggestion chips — matching the look
of scripts/aali_cli.py.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

import arabic_reshaper
from bidi.algorithm import get_display

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "assets"

W, H = 1120, 640
TITLEBAR = 38
BG = (23, 24, 26)          # terminal body
CHROME = (58, 58, 60)      # title bar
GOLD = (232, 179, 75)
GREEN = (108, 214, 132)
CYAN = (98, 197, 218)
GREY = (150, 152, 155)
WHITE = (240, 240, 238)
RED = (237, 106, 94)
YELLOW = (245, 191, 79)
BORDER = (80, 76, 60)

MONO = r"C:\Windows\Fonts\consola.ttf"
AR = r"C:\Windows\Fonts\segoeui.ttf"
ARB = r"C:\Windows\Fonts\segoeuib.ttf"

LH = 26
PAD = 26
TOP = TITLEBAR + 20


def fmono(sz: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(MONO, sz)


def far(sz: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(ARB if bold else AR, sz)


def ar_shape(s: str) -> str:
    return get_display(arabic_reshaper.reshape(s), base_dir="R")


def frame(rows: list[dict], cursor_row: int = -1) -> Image.Image:
    """rows: dicts {t: text, c: color, s: size, ar: bool, b: bold, x: indent}."""
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)
    # ---- window chrome
    d.rectangle([0, 0, W, TITLEBAR], fill=CHROME)
    for i, col in enumerate((RED, YELLOW, GREEN)):
        d.ellipse([16 + i * 26, TITLEBAR // 2 - 7, 30 + i * 26, TITLEBAR // 2 + 7], fill=col)
    d.text((W // 2, TITLEBAR // 2), "aali — 120 × 34", font=fmono(15),
           fill=(200, 200, 200), anchor="mm")
    # ---- rows
    y = TOP
    for row in rows:
        text = row["t"]
        col = row.get("c", WHITE)
        sz = row.get("s", 16)
        if row.get("ar"):
            d.text((PAD + row.get("x", 0), y), ar_shape(text), font=far(sz, row.get("b", False)), fill=col)
        else:
            d.text((PAD + row.get("x", 0), y), text, font=fmono(sz), fill=col)
        y += LH + (4 if sz >= 20 else 0)
    if cursor_row >= 0:
        yc = TOP + cursor_row * (LH + 4)
        d.rectangle([PAD + 2, yc + 2, PAD + 4, yc + 18], fill=WHITE)
    return im


def r(t: str, c: int = WHITE, s: int = 16, ar: bool = False, b: bool = False, x: int = 0) -> dict:
    return {"t": t, "c": c, "s": s, "ar": ar, "b": b, "x": x}


def boot(spinner: str) -> list[dict]:
    return [
        r("╭" + "─" * 40 + "╮", GOLD, 17),
        r("│  AALI · agent on your machine          │", GOLD, 17),
        r("│  local · private · your own model      │", GREY, 15),
        r("╰" + "─" * 40 + "╯", GOLD, 17),
        r("آلي — مساعدك المحلي، جاهز.", GOLD, 16, ar=True),
        r("  /help commands · Ctrl+C cancel · /exit quit", GREY, 15),
        r(""),
        r("❯ Build me a landing page and test it", WHITE, 17),
        r(""),
        r(spinner + " Aali is working…", GOLD, 16),
    ]


def work() -> list[dict]:
    return [
        r("❯ Build me a landing page and test it", WHITE, 17),
        r(""),
        r("● brain → aali_own (graduated)", GREEN, 16),
        r("● write_file(path=index.html, 84 lines)", WHITE, 16),
        r("  └─ wrote 4.2 KB ✓", GREY, 15, x=14),
        r("● run_command(cmd=python -m http.server)", WHITE, 16),
        r("  └─ serving on :8000 ✓", GREY, 15, x=14),
        r("● read_file(screenshot.png) → analyzed", WHITE, 16),
        r("  └─ layout looks good, 0 console errors", GREY, 15, x=14),
        r(""),
        r("● آلي", GOLD, 17, ar=True, b=True),
        r("تم! بنيت الصفحة وجرّبتها بنجاح — شغّالة الآن على المنفذ 8000.", WHITE, 17, ar=True),
    ]


def done() -> list[dict]:
    rows = work() + [
        r(""),
        r("  [1] Add a dark theme toggle", CYAN, 15),
        r("  [2] Deploy it with aali_deploy.bat", CYAN, 15),
        r(""),
        r("❯ _", GOLD, 17),
    ]
    return rows


def main() -> None:
    frames = [
        frame(boot("/"), cursor_row=7),
        frame(boot("-")),
        frame(boot("\\")),
        frame(boot("|")),
        frame(work()),
        frame(done(), cursor_row=len(done()) - 1),
    ]
    # cursor-off variant of the final frame for a blinking effect
    frames.append(frame(done(), cursor_row=-1))

    durations = [220, 220, 220, 220, 2600, 2400, 500]
    frames[0].save(
        OUT / "aali_cli_demo.gif", save_all=True, append_images=frames[1:],
        duration=durations, loop=0, optimize=True,
    )
    import os
    print("GIF:", (OUT / "aali_cli_demo.gif").stat().st_size // 1024, "KB")


if __name__ == "__main__":
    main()
