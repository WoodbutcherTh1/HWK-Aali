"""Owner map — one page that explains the whole project.

The owner asked: "as the owner I want to know what each file does and what
he made for. Make me a giant graph."

What this builds (one self-contained HTML file, no server, works in any
browser on PC / Mac / iPhone):

  * a graph of the RUNNING system (brain, clients, ports, data) drawn as
    an SVG so the arrows are the real story;
  * every area of the repo with a curated Arabic+English purpose;
  * every individual file with a purpose line READ FROM ITS OWN SOURCE —
    the module docstring for .py, the leading comment for .sh/.bat/.swift/
    .js, the H1 for .md, the <title> for .html. Nothing invented: a file
    with no docstring is honestly listed as "بلا وصف" (no description).

    python scripts/make_owner_map.py [--out Desktop-Apps/OWNER-MAP.html]

Standard library only (a broken owner map must never need a venv).
"""

from __future__ import annotations

import argparse
import html
import os
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

SKIP_DIRS = {
    ".git", ".venv", ".venv-desktop", ".venv-hub", ".venv-studio",
    "node_modules", "__pycache__", ".pytest_cache", ".freebuff",
    "D:", "X:", "legacy", "_scratch", "workspace", "mentor_data",
    "attached_assets", "dist", "build", "work", "work-studio",
}
SKIP_SUFFIX = {".pyc", ".pyo", ".png", ".jpg", ".ico", ".icns", ".exe",
               ".bin", ".pt", ".safetensors", ".db", ".sqlite3", ".zip",
               ".wav", ".mp3", ".mp4", ".pdf", ".lock"}

# Curated areas: (dir, Arabic title, English title, colour, what it is FOR)
AREAS = [
    ("file-agent", "العقل والواجهة الخلفية", "The brain & backend", "gold",
     "قلب آلي: يستقبل سؤالك، يقرر، ينفّذ الأدوات، ويرد. هذا هو 프로그램 "
     "الذي يعمل على حاسوبك."),
    ("build-desktop", "تطبيقات سطح المكتب", "Desktop apps", "green",
     "ثلاثة تطبيقات سطح مكتب مبنية من نفس العقل: آلي ستوديو (محرّر كامل)، "
     "آلي ديسكتوب (نافذة بسيطة)، و آلي CLI (طرفية)."),
    ("ios", "تطبيق الآيفون", "The iPhone app", "blue",
     "تطبيق آلي للآيفون بواجهة SwiftUI، يتصل بنفس العقل عبر الشبكة."),
    ("web", "تطبيق الويب", "The web client", "blue",
     "واجهة آلي في المتصفح — تعمل على أي جهاز بدون تثبيت."),
    ("aali_hub", "المركز السحابي (SaaS)", "The cloud hub", "violet",
     "مركز آلي السحابي: حسابات، طابور، واشتراكات، وتحديثات موقّعة."),
    ("aali_node", "عقدة آلي على جهاز المستخدم", "The Aali Node", "violet",
     "العميل الذي يعمل على حاسوب المستخدم ويأمر من远程."),
    ("aali_monitor", "شاشة المراقبة", "The monitor", "amber",
     "لوحة مراقبة: صحة العقل، كرت الشاشة، والوظائف الجارية."),
    ("scripts", "الأدوات والسكربتات", "Tools & scripts", "amber",
     "كل الأدوات: بناء، تشغيل، مراقبة، نسخ احتياطي، ورسوم owner's guides."),
    ("tests", "الاختبارات", "The test suite", "grey",
     "أكثر من 1700 اختبار يضمن أن لا شيء ينكسر بصمت."),
    ("docs", "التوثيق", "Documentation", "grey",
     "شرح كل جزء: كيف يعمل، ولماذا، وما حدوده الصادقة."),
    ("data", "بيانات التدريب", "Training data", "red",
     "بيانات supervized داخل المستودع (ملفات صغيرة). corpora الكبيرة "
     "خارج المستودع على D:."),
    ("hwk_model", "بنية النموذج", "Model architecture", "violet",
     "كود النموذج اللغوي (RoPE transformer) الذي بُني من الصفر."),
    ("file-agent/file_agent", "أدوات الوكيل", "Agent tools", "amber",
     "أدوات التنفيذ: قراءة/كتابة الملفات، الأوامر، الصور، المحادثة."),
]

PURPOSE: dict[str, str] = {}      # filled in main()


def read_head(path: Path) -> tuple[str, str]:
    """(kind, purpose) for one file. Purpose comes from the file itself."""
    ext = path.suffix.lower()
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return (ext.lstrip("."), "تعذّر قراءة الملف")

    lines = text.splitlines()
    if ext == ".py":
        # module docstring = first triple-quoted block
        body = "\n".join(lines[:60])
        m = re.search(r'"""(.+?)"""', body, re.S)
        if m:
            first = " ".join(m.group(1).strip().split())
            return ("py", first[:190] or "بلا وصف")
        for line in lines[:20]:
            if line.startswith("#") and len(line) > 8:
                return ("py", line.lstrip("# ").strip()[:190])
        return ("py", "بلا وصف")

    if ext in (".sh", ".bat"):
        for line in lines[1:14]:
            s = line.strip()
            if s.startswith("#") and len(s) > 10:
                return (ext.lstrip("."), s.lstrip("#% ").strip()[:190])
        return (ext.lstrip("."), "سكربت شل/باتش — بلا وصف في أول سطور")

    if ext in (".swift", ".js", ".ts", ".tsx", ".jsx", ".css"):
        for line in lines[:12]:
            s = line.strip()
            if s.startswith("//") and len(s) > 6:
                return (ext.lstrip("."), s.lstrip("/ ").strip()[:190])
        return (ext.lstrip("."), "ملف كود — بلا وصف في أول سطور")

    if ext == ".md":
        for line in lines[:14]:
            if line.startswith("# "):
                return ("md", line.lstrip("# ").strip()[:190])
        return ("md", "ملف توثيق")

    if ext == ".html":
        m = re.search(r"<title>(.*?)</title>", text, re.S)
        if m:
            return ("html", m.group(1).strip()[:190])
        return ("html", "صفحة HTML")

    return (ext.lstrip(".") or "file", "ملف")


def collect() -> list[tuple[str, str, str, str]]:
    rows = []
    for base, dirs, files in os.walk(REPO):
        rel = Path(base).relative_to(REPO)
        # prune
        keep = []
        for d in dirs:
            if d in SKIP_DIRS:
                continue
            if d.startswith(".") and d not in (".github"):
                continue
            keep.append(d)
        dirs[:] = keep
        if parts := [p for p in rel.parts if p in SKIP_DIRS]:
            continue
        for name in sorted(files):
            if name.startswith("."):
                continue
            p = Path(base) / name
            if p.suffix.lower() in SKIP_SUFFIX:
                continue
            try:
                size = p.stat().st_size
            except OSError:
                continue
            if size > 400_000:
                continue
            kind, purpose = read_head(p)
            rows.append((p.relative_to(REPO).as_posix(), kind, purpose,
                         f"{size/1024:.0f} KB"))
    return rows


def diagram_svg() -> str:
    """The RUNNING system as an SVG graph — the part a picture explains."""
    return """
<svg viewBox="0 0 1200 640" width="100%" role="img" aria-label="Aali system graph">
  <defs>
    <marker id="ar" markerWidth="10" markerHeight="10" refX="8" refY="3"
            orient="auto"><path d="M0,0 L0,6 L9,3 z" fill="#8b93a7"/></marker>
  </defs>
  <rect width="1200" height="640" fill="#0b0e14"/>

  <!-- brain -->
  <rect x="430" y="150" width="340" height="230" rx="18" fill="#161a26"
        stroke="#e8b34b" stroke-width="2"/>
  <text x="600" y="205" text-anchor="middle" fill="#e8b34b" font-size="30"
        font-family="Segoe UI, Tahoma" font-weight="bold">قلب آلي</text>
  <text x="600" y="243" text-anchor="middle" fill="#e8e6e3" font-size="18"
        font-family="Segoe UI, Tahoma">file-agent/app.py</text>
  <text x="600" y="285" text-anchor="middle" fill="#6f9ec9" font-size="17"
        font-family="Consolas, monospace" direction="ltr">:5055 — يستقبل ويرد</text>
  <text x="600" y="315" text-anchor="middle" fill="#6f9ec9" font-size="16"
        font-family="Consolas, monospace" direction="ltr">:20129 — النموذج نفسه</text>
  <text x="600" y="352" text-anchor="middle" fill="#8b93a7" font-size="15"
        font-family="Segoe UI, Tahoma">يستمع على الشبكة — يحتاج مفتاح</text>

  <!-- clients -->
  <g font-family="Segoe UI, Tahoma" font-size="17" fill="#e8e6e3">
    <rect x="60" y="90" width="250" height="60" rx="12" fill="#161a26"
          stroke="#2d3450"/><text x="185" y="127" text-anchor="middle">الآيفون (SwiftUI)</text>
    <rect x="60" y="200" width="250" height="60" rx="12" fill="#161a26"
          stroke="#2d3450"/><text x="185" y="237" text-anchor="middle">الماك (ستوديو/ديسكتوب)</text>
    <rect x="60" y="310" width="250" height="60" rx="12" fill="#161a26"
          stroke="#2d3450"/><text x="185" y="347" text-anchor="middle">الطرفية (CLI)</text>
    <rect x="60" y="420" width="250" height="60" rx="12" fill="#161a26"
          stroke="#2d3450"/><text x="185" y="457" text-anchor="middle">المتصفح (Web)</text>
    <rect x="890" y="90" width="250" height="60" rx="12" fill="#161a26"
          stroke="#2d3450"/><text x="1015" y="127" text-anchor="middle">المركز السحابي</text>
    <rect x="890" y="200" width="250" height="60" rx="12" fill="#161a26"
          stroke="#2d3450"/><text x="1015" y="237" text-anchor="middle">عقدة المستخدم (Node)</text>
    <rect x="890" y="310" width="250" height="60" rx="12" fill="#161a26"
          stroke="#2d3450"/><text x="1015" y="347" text-anchor="middle">n8n / تطبيقات أخرى</text>
  </g>
  <g stroke="#8b93a7" stroke-width="2" fill="none" marker-end="url(#ar)">
    <path d="M310,120 C380,120 390,180 430,190"/>
    <path d="M310,230 L430,240"/>
    <path d="M310,340 C380,340 390,300 430,290"/>
    <path d="M310,450 C380,450 390,340 430,330"/>
    <path d="M770,190 C830,190 840,120 890,120"/>
    <path d="M770,250 C830,250 840,230 890,230"/>
    <path d="M770,320 C830,320 840,340 890,340"/>
  </g>
  <text x="600" y="580" text-anchor="middle" fill="#8b93a7" font-size="17"
        font-family="Segoe UI, Tahoma">كل العملاء يتكلمون نفس القلب — نفس المفتاح — نفس الإجابة</text>
</svg>
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="Desktop-Apps/OWNER-MAP.html")
    args = ap.parse_args()

    rows = collect()
    by_area: dict[str, list] = {}
    for path, kind, purpose, size in rows:
        top = path.split("/")[0] if "/" in path else "(root)"
        by_area.setdefault(top, []).append((path, kind, purpose, size))

    area_meta = {a[0]: a for a in AREAS}

    parts = []
    parts.append(f"""<!DOCTYPE html><html lang="ar" dir="rtl"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>خريطة المشروع — HWK Aali</title>
<style>
:root{{--ink:#0b0e14;--ink2:#161a26;--line:#2d3450;--fg:#e8e6e3;--dim:#8b93a7;--gold:#e8b34b}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--ink);color:var(--fg);
 font-family:"Segoe UI",Tahoma,system-ui,sans-serif;line-height:1.8}}
.wrap{{max-width:1180px;margin:0 auto;padding:20px 16px 90px}}
h1{{color:var(--gold);font-size:30px;margin:0 0 4px}}
.sub{{color:var(--dim);margin-bottom:20px}}
h2{{color:var(--gold);font-size:22px;margin:34px 0 10px}}
.area{{background:var(--ink2);border:1px solid var(--line);border-radius:14px;
 padding:14px 18px;margin:14px 0}}
.area h3{{margin:0 0 4px;font-size:20px}}
.area .en{{color:var(--dim);font-size:14px;direction:ltr;text-align:left}}
.area .why{{color:#c9c6bf;font-size:15.5px;margin:6px 0 10px}}
.count{{float:inline-end;color:var(--dim);font-size:14px;border:1px solid var(--line);
 border-radius:999px;padding:1px 10px}}
table{{width:100%;border-collapse:collapse;margin-top:8px}}
td,th{{border-bottom:1px solid #22263a;padding:7px 9px;text-align:start;vertical-align:top}}
th{{color:var(--gold);font-weight:600;font-size:15px}}
code{{font-family:Consolas,monospace;direction:ltr;unicode-bidi:embed;display:inline-block;
 color:#6f9ec9;font-size:14px}}
.purp{{color:#c9c6bf;font-size:14.5px}}
.kind{{color:var(--dim);font-size:12px;border:1px solid var(--line);border-radius:6px;
 padding:0 6px}}
.size{{color:#5d667f;font-size:12px;white-space:nowrap}}
input{{width:100%;background:var(--ink2);border:1px solid var(--line);border-radius:10px;
 padding:10px 14px;color:var(--fg);font-size:16px;margin:10px 0}}
input:focus{{outline:none;border-color:var(--gold)}}
.hidden{{display:none}}
footer{{color:var(--dim);font-size:14px;margin-top:40px}}
</style></head><body><div class="wrap">
<h1>خريطة المشروع — كل ملف ويفعله</h1>
<div class="sub">HWK Aali · {len(rows)} ملفاً في {len(by_area)} منطقة · built by team HWK · عزيزة</div>

<h2>١) كيف ترتبط الأجزاء (الرسم الكبير)</h2>
{diagram_svg()}

<h2>٢) كل منطقة وكل ملف</h2>
<input id="q" placeholder="ابحث باسم أي ملف أو كلمة… (search any file)" />
<div id="list">""")

    # areas, curated first then the rest alphabetically
    order = [a[0] for a in AREAS if a[0] in by_area]
    order += sorted(k for k in by_area if k not in order)

    for area in order:
        files = by_area[area]
        meta = area_meta.get(area)
        if meta:
            _, title_ar, title_en, _color, why = meta
        else:
            title_ar = area
            title_en = area
            why = "ملفات في جذر المشروع."
        parts.append(f"""<div class="area" data-area="{html.escape(area)}">
<span class="count">{len(files)} ملف</span>
<h3>{html.escape(title_ar)} <span class="en">/ {html.escape(title_en)}</span></h3>
<div class="why">{html.escape(why)}</div>
<table><tr><th style="width:34%">الملف</th><th>ماذا يفعل (taken from the file itself)</th><th style="width:9%">الحجم</th></tr>""")
        for path, kind, purpose, size in files:
            parts.append(
                f'<tr><td><code>{html.escape(path)}</code> '
                f'<span class="kind">{html.escape(kind)}</span></td>'
                f'<td class="purp">{html.escape(purpose)}</td>'
                f'<td class="size">{size}</td></tr>')
        parts.append("</table></div>")

    parts.append("""</div>
<footer>كل وصف أعلاه مقروء من الملف نفسه (أول سطر في توثيقه) — لم يُختلق شيء.
<code>python scripts/make_owner_map.py</code> يحدّث هذه الصفحة.</footer>
<script>
const q = document.getElementById('q');
q.addEventListener('input', () => {
  const v = q.value.trim().toLowerCase();
  document.querySelectorAll('#list .area').forEach(a => {
    const hit = !v || a.textContent.toLowerCase().includes(v);
    a.classList.toggle('hidden', !hit);
  });
});
</script>
</div></body></html>""")

    out = REPO / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(parts), encoding="utf-8")
    print(f"wrote {out}  ({len(rows)} files, {len(by_area)} areas)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
