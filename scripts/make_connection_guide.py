"""Simple picture guides for connecting the phone and the Mac to Aali.

The owner asked for "explain like I'm 5" pictures: big shapes, few words,
numbers in order. Two PNGs are rendered to the Desktop:

  1. Aali-Guide-Home.png      — inside the house (Wi-Fi)
  2. Aali-Guide-Anywhere.png  — outside the house (Tailscale) + fix table

Arabic is rendered with the repo's own shaping helpers (scripts/aali_cli.py
shape_arabic + reorder_visual) because Pillow has no raqm/libraqm on this
box — same pipeline the CLI uses for conhost. ASCII-safe module: all
Arabic lives in string literals only.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
for extra in (REPO / "scripts",):
    if extra.is_dir() and str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402
from aali_cli import reorder_visual, shape_arabic  # noqa: E402

W, H = 1680, 1080
INK = (11, 14, 20)
INK2 = (22, 26, 38)
INK3 = (32, 37, 54)
LINE = (45, 52, 74)
TEXT = (232, 230, 227)
MUT = (139, 147, 167)
GOLD = (232, 179, 75)
GREEN = (127, 176, 105)
BLUE = (111, 158, 201)
RED = (204, 107, 107)

FONT_PATHS = [
    "C:/Windows/Fonts/segoeui.ttf",
    "C:/Windows/Fonts/tahoma.ttf",
]
_fonts: dict[int, ImageFont.FreeTypeFont] = {}


def font(size: int) -> ImageFont.FreeTypeFont:
    if size not in _fonts:
        for path in FONT_PATHS:
            if Path(path).is_file():
                _fonts[size] = ImageFont.truetype(path, size)
                break
        else:
            _fonts[size] = ImageFont.load_default()
    return _fonts[size]


def ar(text: str) -> str:
    """Arabic -> shaped visual order so PIL draws it correctly."""
    return reorder_visual(shape_arabic(text))


def text_w(draw: ImageDraw.ImageDraw, s: str, size: int) -> int:
    return int(draw.textlength(s, font=font(size)))


def draw_ar(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str,
            size: int, fill=TEXT, anchor: str = "ra") -> None:
    """Right-anchored Arabic by default (RTL reading start)."""
    draw.text(xy, ar(text), font=font(size), fill=fill, anchor=anchor)


def card(draw: ImageDraw.ImageDraw, box, fill=INK2, outline=LINE) -> None:
    draw.rounded_rectangle(box, radius=18, fill=fill, outline=outline, width=2)


def num_badge(draw: ImageDraw.ImageDraw, xy, n: int, color=GOLD) -> None:
    x, y = xy
    draw.ellipse((x, y, x + 56, y + 56), fill=color)
    draw.text((x + 28, y + 27), str(n), font=font(30), fill=(20, 18, 12),
              anchor="mm")


def arrow(draw: ImageDraw.ImageDraw, p1, p2, color=GOLD, width=5) -> None:
    draw.line([p1, p2], fill=color, width=width)
    import math
    ang = math.atan2(p2[1] - p1[1], p2[0] - p1[0])
    for da in (2.6, -2.6):
        draw.line([p2, (p2[0] + 18 * math.cos(ang + da),
                        p2[1] + 18 * math.sin(ang + da))],
                  fill=color, width=width)


def pc_icon(draw: ImageDraw.ImageDraw, x: int, y: int, s=1.0) -> None:
    w, h = int(150 * s), int(92 * s)
    draw.rounded_rectangle((x, y, x + w, y + h), radius=10, fill=INK3,
                           outline=GOLD, width=3)
    draw.rounded_rectangle((x + w // 2 - 30, y + h, x + w // 2 + 30, y + h + 26),
                           radius=6, fill=INK3, outline=GOLD, width=3)
    draw.rounded_rectangle((x + 14, y + 12, x + w - 14, y + h - 12),
                           radius=4, fill=(24, 28, 40))


def phone_icon(draw: ImageDraw.ImageDraw, x: int, y: int, s=1.0) -> None:
    w, h = int(64 * s), int(112 * s)
    draw.rounded_rectangle((x, y, x + w, y + h), radius=14, fill=INK3,
                           outline=BLUE, width=3)
    draw.line((x + 18, y + 12, x + w - 18, y + 12), fill=BLUE, width=2)
    draw.ellipse((x + w / 2 - 5, y + h - 16, x + w / 2 + 5, y + h - 6),
                 fill=BLUE)


def mac_icon(draw: ImageDraw.ImageDraw, x: int, y: int, s=1.0) -> None:
    w, h = int(130 * s), int(80 * s)
    draw.rounded_rectangle((x, y, x + w, y + h), radius=8, fill=INK3,
                           outline=GREEN, width=3)
    draw.polygon([(x - 18, y + h + 22), (x + w + 18, y + h + 22),
                  (x + w - 8, y + h + 6), (x + 8, y + h + 6)],
                 fill=INK3, outline=GREEN)


def wifi_icon(draw: ImageDraw.ImageDraw, x: int, y: int, color=MUT) -> None:
    import math
    for i, r in enumerate((18, 34, 50)):
        draw.arc((x - r, y - r, x + r, y + r), start=215, end=325,
                 fill=color, width=4)
    draw.ellipse((x - 5, y - 5, x + 5, y + 5), fill=color)


def cloud_icon(draw: ImageDraw.ImageDraw, x: int, y: int, color=BLUE) -> None:
    for dx, dy, r in ((-34, 6, 24), (0, -8, 32), (32, 6, 26)):
        draw.ellipse((x + dx - r, y + dy - r, x + dx + r, y + dy + r),
                     fill=color)
    draw.rounded_rectangle((x - 58, y + 4, x + 58, y + 34), radius=15,
                           fill=color)


def base() -> Image.Image:
    img = Image.new("RGB", (W, H), INK)
    d = ImageDraw.Draw(img)
    for i in range(H):
        c = int(11 + 6 * (1 - i / H))
        d.line((0, i, W, i), fill=(c, c + 3, c + 7))
    return img


def header(d: ImageDraw.ImageDraw, title: str, subtitle: str) -> None:
    d.rounded_rectangle((0, 0, W, 118), radius=0, fill=INK2)
    d.line((0, 118, W, 118), fill=LINE, width=2)
    d.text((W - 40, 58), "★", font=font(44), fill=GOLD, anchor="rm")
    draw_ar(d, (W - 104, 60), title, 44, fill=GOLD, anchor="rm")
    draw_ar(d, (40, 60), subtitle, 20, fill=MUT, anchor="lm")


# ————————————————————————————————————————————— picture 1: home ——————————————
def guide_home(out: Path) -> None:
    img = base()
    d = ImageDraw.Draw(img)
    header(d, "كيف تتكلم مع آلي في البيت", "٣ خطوات فقط — اتبع الأرقام")

    # the map: PC —(Wi-Fi)→ iPhone + Mac
    card(d, (70, 190, 620, 560))
    pc_icon(d, 120, 260)
    draw_ar(d, (470, 300), "حاسوبك", 30, fill=TEXT)
    draw_ar(d, (470, 350), "آلي يعيش هنا", 22, fill=GOLD)
    draw_ar(d, (470, 396), "البرنامج: :5055", 20, fill=MUT)

    wifi_icon(d, 345, 620, GREEN)
    draw_ar(d, (470, 640), "شبكة البيت (الواي فاي)", 20, fill=GREEN,
            anchor="rm")

    arrow(d, (260, 470), (300, 585), GREEN)
    arrow(d, (430, 585), (470, 470), GREEN)

    card(d, (700, 190, 1060, 560))
    phone_icon(d, 830, 250, 1.4)
    draw_ar(d, (1000, 300), "الآيفون", 30, fill=TEXT)
    draw_ar(d, (1000, 350), "تطبيق آلي", 22, fill=BLUE)

    card(d, (1120, 190, 1560, 560))
    mac_icon(d, 1190, 250)
    draw_ar(d, (1500, 300), "الماك بوك", 30, fill=TEXT)
    draw_ar(d, (1500, 350), "برنامج ستوديو", 22, fill=GREEN)

    d.line((1060, 375, 1120, 375), fill=LINE, width=2)

    # step cards
    steps = [
        ("شغّل آلي على الحاسوب",
         "افتح الملف: scripts\\start_app.bat",
         "انتظر حتى يكتب: Aali chat UI"),
        ("في الآيفون: الإعدادات ⚙",
         "العنوان: http://192.168.1.13:5055",
         "والمفتاح: من ملف aali_master_key"),
        ("اضغط اختبار الاتصال",
         "يجب أن يكتب: ✓ تم الاتصال بنجاح",
         "بعدها اسأل آلي أي شيء!"),
    ]
    x = 70
    for i, (t, l1, l2) in enumerate(steps, 1):
        card(d, (x, 620, x + 480, 930))
        num_badge(d, (x + 24, 644), i)
        draw_ar(d, (x + 456, 672), t, 26, fill=GOLD)
        draw_ar(d, (x + 456, 740), l1, 21, fill=TEXT)
        draw_ar(d, (x + 456, 790), l2, 21, fill=TEXT)
        draw_ar(d, (x + 456, 860), "الترتيب مهم: الحاسوب أولاً!", 17,
                fill=MUT)
        x += 500

    draw_ar(d, (W // 2, 990),
            "مفتاح آلي على الحاسوب في هذا الملف: D:\\hwk-data\\aali_master_key.txt",
            20, fill=MUT)
    img.save(out)


# —————————————————————————————————————— picture 2: anywhere + fixes —————————
def guide_anywhere(out: Path) -> None:
    img = base()
    d = ImageDraw.Draw(img)
    header(d, "خارج البيت + حل المشاكل", "Tailscale = شبكة سحرية خاصة بك")

    # left: the tailscale path
    card(d, (70, 170, 780, 560))
    pc_icon(d, 110, 220, 0.9)
    draw_ar(d, (340, 260), "حاسوبك في البيت", 24, fill=TEXT)
    draw_ar(d, (340, 300), "آلي شغّال", 20, fill=GOLD)

    cloud_icon(d, 430, 420)
    draw_ar(d, (600, 400), "Tailscale", 22, fill=BLUE)
    draw_ar(d, (600, 436), "يربطكم وانتوا برا", 17, fill=MUT)

    phone_icon(d, 640, 230, 1.0)
    draw_ar(d, (760, 260), "الآيفون", 22, fill=TEXT)
    arrow(d, (240, 300), (390, 405), BLUE)
    arrow(d, (480, 395), (655, 305), BLUE)

    card(d, (70, 590, 780, 940))
    num_badge(d, (100, 615), 1)
    draw_ar(d, (740, 645), "حمّل تطبيق Tailscale من المتجر", 24, fill=GOLD)
    num_badge(d, (100, 690), 2)
    draw_ar(d, (740, 720), "سجّل بنفس الحساب المسجّل عندك", 24, fill=GOLD)
    num_badge(d, (100, 765), 3)
    draw_ar(d, (740, 795), "افتح Tailscale (زر ON) في أي مكان", 24, fill=GOLD)
    num_badge(d, (100, 840), 4)
    draw_ar(d, (740, 870), "في تطبيق آلي استعمل العنوان السحري:", 24, fill=GOLD)
    d.text((740, 905), "http://100.94.100.57:5055", font=font(24),
           fill=BLUE, anchor="rm")

    # right: when it says OFFLINE
    card(d, (830, 170, 1610, 940))
    draw_ar(d, (1570, 210), "آلي يقول: غير متصل؟ اطلب هذه", 28, fill=RED)
    rows = [
        ("الحاسوب شغّال؟", "شغّل scripts\\start_app.bat"),
        ("نفس الواي فاي؟ (في البيت)", "الجوال والحاسوب على شبكة واحدة"),
        ("Tailscale مفتوح؟ (برا البيت)", "زر ON داخل التطبيق"),
        ("العنوان صحيح؟", "http://192.168.1.13:5055 في البيت"),
        ("المفتاح تلصقته؟", "انسخه من aali_master_key.txt"),
        ("كل شيء صحيح وما زال؟", "أعد تشغيل الحاسوب ثم كرر الخطوات"),
    ]
    y = 280
    for i, (q, a) in enumerate(rows, 1):
        d.ellipse((870, y + 8, 902, y + 40), outline=GOLD, width=2)
        d.text((886, y + 24), str(i), font=font(20), fill=GOLD, anchor="mm")
        draw_ar(d, (1570, y + 26), q, 23, fill=TEXT)
        draw_ar(d, (1570, y + 66), a, 20, fill=MUT)
        y += 108

    draw_ar(d, (W // 2, 1005),
            "قاعدة ذهبية: الحاسوب هو صاحب العقل — من دون تشغيله لن يرد آلي أبداً",
            22, fill=GOLD)
    img.save(out)


def main() -> int:
    desktop = Path.home() / "OneDrive" / "Desktop"
    if not desktop.is_dir():
        desktop = Path.home() / "Desktop"
    out1 = desktop / "Aali-Guide-Home.png"
    out2 = desktop / "Aali-Guide-Anywhere.png"
    guide_home(out1)
    guide_anywhere(out2)
    print(f"wrote {out1}")
    print(f"wrote {out2}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
