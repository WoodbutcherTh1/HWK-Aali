# -*- coding: utf-8 -*-
"""Build Aali-Guide-<date>.pdf — illustrated Arabic guide with real app screenshots.

Pages are composed as A4 bitmaps with PIL (raqm handles Arabic shaping) and
wrapped in a minimal PDF that embeds each page as a JPEG (DCTDecode), so no
third-party PDF library is required.

Usage:  .venv/Scripts/python.exe scripts/make_aali_guide.py [output.pdf]
"""
from __future__ import annotations

import io
import sys
from datetime import date
from pathlib import Path
from typing import Optional  # noqa: F401

from PIL import Image, ImageDraw, ImageFont

import arabic_reshaper
from bidi.algorithm import get_display

_reshaper = arabic_reshaper.ArabicReshaper(configuration={"delete_harakat": False, "support_ligatures": True})


def _sh(s: str) -> str:
    """Shape Arabic then apply the bidi algorithm so PIL draws it correctly."""
    return get_display(_reshaper.reshape(s), base_dir="R") if any("\u0600" <= c <= "\u06FF" for c in s) else s


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "docs" / "assets"
AALI_BRAND = (37, 37, 35)      # warm near-black #252523
AALI_PANEL = (52, 48, 45)      # warm panel
GOLD = (232, 179, 75)          # HWK gold
BEIGE = (198, 193, 188)        # warm text
WHITE = (247, 245, 242)

AR_FONT = r"C:\Windows\Fonts\segoeui.ttf"
AR_BOLD = r"C:\Windows\Fonts\segoeuib.ttf"

W, H = 1240, 1754              # A4 @ 150 dpi
M = 90                         # margin

_fonts: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}


def F(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    key = ("b" if bold else "r", size)
    if key not in _fonts:
        _fonts[key] = ImageFont.truetype(AR_BOLD if bold else AR_FONT, size)
    return _fonts[key]


def txt(d: ImageDraw.ImageDraw, xy: tuple[int, int], s: str, size: int = 30,
        bold: bool = False, fill: tuple[int, int, int] = BEIGE,
        anchor: str = "ra") -> None:
    d.text(xy, _sh(s), font=F(size, bold), fill=fill, anchor=anchor)


def para(d: ImageDraw.ImageDraw, right_x: int, y: int, s: str, size: int = 30,
         fill: tuple[int, int, int] = BEIGE, width: int = W - 2 * M,
         leading: int = 46) -> int:
    """Right-aligned wrapped Arabic paragraph. Returns new y."""
    words, line = s.split(), ""
    for w in words:
        trial = f"{line} {w}".strip()
        if d.textlength(_sh(trial), font=F(size)) > width:
            txt(d, (right_x, y), line, size, fill=fill)
            y += leading
            line = w
        else:
            line = trial
    if line:
        txt(d, (right_x, y), line, size, fill=fill)
        y += leading
    return y


def bullet(d: ImageDraw.ImageDraw, right_x: int, y: int, head: str, body: str) -> int:
    d.ellipse([right_x + 14, y + 14, right_x + 26, y + 26], fill=GOLD)
    txt(d, (right_x - 14, y), head, 32, bold=True, fill=WHITE)
    return para(d, right_x, y + 50, body, 29)


def shot(path: Path, box_w: int) -> Image.Image:
    im = Image.open(path).convert("RGB")
    return im.resize((box_w, round(im.height * box_w / im.width)), Image.LANCZOS)


def footer(d: ImageDraw.ImageDraw, page_no: int) -> None:
    d.line([M, H - 70, W - M, H - 70], fill=AALI_PANEL, width=2)
    txt(d, (W - M, H - 58), "آلي — دليل الاستخدام", 22, fill=(150, 145, 140))
    txt(d, (M, H - 58), str(page_no), 22, fill=(150, 145, 140), anchor="la")


def page_cover() -> Image.Image:
    im = Image.new("RGB", (W, H), AALI_BRAND)
    d = ImageDraw.Draw(im)
    d.rectangle([M, M, W - M, H - M], outline=GOLD, width=3)
    y = 190
    txt(d, (W - M - 60, y), "آلي", 150, bold=True, fill=GOLD); y += 210
    txt(d, (W - M - 60, y), "مساعد ذكاء اصطناعي يُبنى من الصفر", 52, bold=True, fill=WHITE); y += 100
    y = para(d, W - M - 60, y, "نموذج لغوي خاص + وكيل كامل بأدوات حقيقية: ملفات، أوامر آمنة، "
             "ذاكرة دائمة، وسائط، وأمان مُدرَّب. عربي أولاً، ويعمل بالكامل على خادمك.",
             34, leading=56)
    tour = shot(ASSETS / "home.png", W - 2 * M - 120)
    im.paste(tour, (M + 60, y + 30))
    d.rectangle([M + 60, y + 30, M + 60 + tour.width, y + 30 + tour.height],
                outline=AALI_PANEL, width=2)
    txt(d, (W - M - 60, H - 200), "إصدار 1.1  •  أيلول 2026", 28, fill=BEIGE)
    txt(d, (W - M - 60, H - 150), "مالك المشروع: حمام كعدنه (Hmam Kaadna)", 28, bold=True, fill=GOLD)
    footer(d, 1)
    return im


def page_whats_new() -> Image.Image:
    im = Image.new("RGB", (W, H), AALI_BRAND)
    d = ImageDraw.Draw(im)
    txt(d, (W - M, 100), "ما الجديد في هذا الإصدار", 56, bold=True, fill=GOLD)
    d.line([W - M, 180, W - M - 420, 180], fill=GOLD, width=4)
    y = 230
    y = bullet(d, W - M, y, "واجهة جديدة كلياً",
               "خلفية سوداء دافئة ولمسات ذهبية مع أيقونة HWK الجديدة في التطبيق وسطح المكتب.")
    y = bullet(d, W - M, y, "شجرة العقل — للمالك فقط",
               "صفحة /brain تعرض حياً كيف يعمل آلي من الداخل: تسجيل الدخول، المحادثة، الأدوات، "
               "الذاكرة، والمفاتيح — مع تصدير كخزائن Obsidian وتغذية أحداث مباشرة.")
    y = bullet(d, W - M, y, "لوحة تحكم المدير",
               "إحصاءات حية، إدارة المفاتيح، وزر يفتح شجرة العقل مباشرة بمصادقة آمنة.")
    y = bullet(d, W - M, y, "زر النشر الخاص بك",
               "scripts\\aali_deploy.bat ينشر آلي أينما تريد: محلياً، GitHub، Hugging Face، "
               "أو Ollama — لا شيء يُرفع دون موافقتك.")
    y = bullet(d, W - M, y, "تطبيق سطح المكتب 1.0.1",
               "مثبّت جاهز بأيقونة جديدة، نفق مشاركة بضغطة واحدة، وفحص تحديثات تلقائي.")
    y = bullet(d, W - M, y, "روابط محادثات قابلة للمشاركة",
               "أي محادثة تُفتح مباشرة عبر رابط يحوي معرّف الجلسة (?sid=...).")
    y = bullet(d, W - M, y, "آلي في الترمينال",
               "عميل طرفية جديد حيّ: ملوّن ويعرض أدوات آلي لحظة بلحظة، "
               "ويأتي داخل مثبّت سطح المكتب بقائمة ابدأ «آلي — Terminal». "
               "للتشغيل المباشر: scripts\\aali_cli.bat")
    y = bullet(d, W - M, y, "طريق التخرج للنموذج",
               "خط تدريب آلي: امتحان أساس ← ضبط دقيق ← امتحان نهائي ← ترقية تلقائية، "
               "بجانب تمديد سياق النموذج إلى 4096 توكن.")

    cw = (W - 2 * M - 40) // 2
    a, b = shot(ASSETS / "home.png", cw), shot(ASSETS / "chat.png", cw)
    top = y + 30
    im.paste(a, (W - M - cw, top))
    im.paste(b, (M, top))
    for x, lab in ((W - M - cw, "الصفحة الرئيسية"), (M, "محادثة حية")):
        txt(d, (x + cw, top + a.height + 16), lab, 26, bold=True, fill=WHITE, anchor="ra")
    footer(d, 2)
    return im


def page_brain() -> Image.Image:
    im = Image.new("RGB", (W, H), AALI_BRAND)
    d = ImageDraw.Draw(im)
    txt(d, (W - M, 100), "شجرة العقل — نظرة المالك الحية", 56, bold=True, fill=GOLD)
    d.line([W - M, 180, W - M - 460, 180], fill=GOLD, width=4)
    y = para(d, W - M, 220,
             "افتح  127.0.0.1:5055/brain  أو اضغط زر «شجرة العقل» في لوحة تحكم المدير. "
             "سترى كل تدفق داخلي: ماذا يحدث عند تسجيل الدخول، أثناء المحادثة، عند توليد "
             "مفتاح، ومن أين يحصل آلي على معلوماته — كل خطوة تُشير للملف البرمجي المسؤول.",
             32, leading=56)
    y = para(d, W - M, y + 10,
             "في الوضع متعدد المستخدمين تُحمى الصفحة بمفتاح المدير، ولا تظهر لأي مستخدم. "
             "الإحصاءات الحية تعرض أرقاماً وطوابع زمن فقط — لا محتوى خاص بالمستخدمين أبداً.",
             30, leading=52, fill=(170, 165, 160))
    cw = (W - 2 * M - 40) // 2
    a, b = shot(ASSETS / "brain.png", cw), shot(ASSETS / "admin.png", cw)
    im.paste(a, (W - M - cw, y + 20))
    im.paste(b, (M, y + 20))
    for x, lab in ((W - M - cw, "شجرة العقل"), (M, "لوحة تحكم المدير")):
        txt(d, (x + cw, y + 40 + a.height), lab, 26, bold=True, fill=WHITE, anchor="ra")
    y2 = y + 60 + a.height
    txt(d, (W - M, y2 + 30), "خزائن Obsidian", 34, bold=True, fill=WHITE)
    para(d, W - M, y2 + 80,
         "شجرة كاملة بروابط [[ويكي]] تُصدَّر إلى  D:/hwk-data/aali-brain-vault  — افتحها في "
         "Obsidian وشاهدها كخريطة علاقات. لإعادة التصدير:  python scripts/build_brain_vault.py",
         29)
    footer(d, 3)
    return im


def page_deploy() -> Image.Image:
    im = Image.new("RGB", (W, H), AALI_BRAND)
    d = ImageDraw.Draw(im)
    txt(d, (W - M, 100), "النشر والتحديثات — بيدك أنت", 56, bold=True, fill=GOLD)
    d.line([W - M, 180, W - M - 430, 180], fill=GOLD, width=4)
    y = para(d, W - M, 230,
             "شغّل  scripts\\aali_deploy.bat  بنقرة مزدوجة وستظهر قائمة: النشر المحلي، "
             "GitHub، Hugging Face، Ollama، أو كلها معاً. الأداة تفحص كل شيء أولاً وتعرض "
             "خطة واضحة — ولا يُرفع أي شيء إلا بعد موافقتك بحرف y.",
             32, leading=56)
    y = bullet(d, W - M, y + 20, "بطاقة نموذج باسمك",
               "أي نشر على Hugging Face يحمل بطاقة نموذج تُنسب إلى حمام كعدنه (Hmam Kaadna) "
               "بترخيص مفتوح تحدده أنت.")
    y = bullet(d, W - M, y, "تحديث تطبيق سطح المكتب",
               "التطبيق يفحص /api/desktop-version تلقائياً ويعرض شريط تحديث عند توفر إصدار "
               "أحدث — التحديث دائماً بقرار المستخدم.")
    y = bullet(d, W - M, y, "قناة الطريق الكاملة",
               "التفاصيل خطوة بخطوة في docs/publish_aali.md داخل المستودع.")
    y = bullet(d, W - M, y, "بصراحة",
               "ميزة إرفاق الصور والملفات في المحادثة قيد التطوير ولم تُصدَّر بعد؛ كل ما "
               "في هذا الدليل يعمل فعلياً اليوم.")

    box = Image.new("RGB", (W - 2 * M, 320), AALI_PANEL)
    bd = ImageDraw.Draw(box)
    txt(bd, (box.width - 40, 40), "الدعم", 34, bold=True, fill=GOLD)
    para(bd, box.width - 40, 100, "المالك: حمام كعدنه (Hmam Kaadna) — كل الحقوق محفوظة "
         "لأسمائه وعلامته. هذا المشروع شخصي مفتوح المصدر وفق الترخيص في المستودع.",
         28, fill=WHITE, width=box.width - 80)
    im.paste(box, (M, y + 30))
    footer(d, 4)
    return im


# ---------------------------------------------------------------- minimal PDF
def save_pdf(pages: list[Image.Image], out: Path) -> None:
    pages = [p.resize((595, 842)) if p.size != (595, 842) else p for p in pages]
    jpegs = []
    for p in pages:
        buf = io.BytesIO()
        p.save(buf, "JPEG", quality=85)
        jpegs.append(buf.getvalue())

    objs: list[bytes] = []
    n = len(jpegs)
    kids = " ".join(f"{4 + 2 * i} 0 R" for i in range(n))

    def add(data: bytes) -> int:
        objs.append(data)
        return len(objs)

    add(b"<< /Type /Catalog /Pages 2 0 R >>")
    add(f"<< /Type /Pages /Kids [{kids}] /Count {n} >>".encode())
    for i, jp in enumerate(jpegs):
        w_px, h_px = pages[i].size
        add(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            f"/Resources << /XObject << /Im0 {5 + 2 * i} 0 R >> >> "
            f"/Contents {4 + 2 * i} 0 R >>".encode())
        content = f"q {595} 0 0 {842} 0 0 cm /Im0 Do Q".encode()
        add(b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream")
        add(f"<< /Type /XObject /Subtype /Image /Width {w_px} /Height {h_px} "
            f"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode "
            f"/Length {len(jp)} >>\nstream\n".encode() + jp + b"\nendstream")

    out_bytes = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for i, data in enumerate(objs, 1):
        offsets.append(len(out_bytes))
        out_bytes += f"{i} 0 obj\n".encode() + data + b"\nendobj\n"
    xref_pos = len(out_bytes)
    out_bytes += f"xref\n0 {len(objs) + 1}\n".encode()
    out_bytes += b"0000000000 65535 f \n"
    for off in offsets[1:]:
        out_bytes += f"{off:010d} 00000 n \n".encode()
    out_bytes += (f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\n"
                  f"startxref\n{xref_pos}\n%%EOF\n").encode()
    out.write_bytes(out_bytes)


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else (
        Path.home() / "OneDrive" / "Desktop" / f"Aali-Guide-{date.today().isoformat()}.pdf")
    pages = [page_cover(), page_whats_new(), page_brain(), page_deploy()]
    save_pdf(pages, out)
    print(f"PDF written: {out}  ({out.stat().st_size // 1024} KB, {len(pages)} pages)")


if __name__ == "__main__":
    main()
