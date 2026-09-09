"""آلي Brain Tree — how Aali actually works, as a living tree (OWNER ONLY).

The owner asked for a map he can watch LIVE: "if user signed in what happen,
if user start chat what happen, if user ask for output, if user generate key,
where Aali get information from" — and never for end users.

Three consumers, one source of truth:
  * render_ascii()          → quick CLI/print view
  * app.py /brain page      → admin-gated live HTML tree (refresh = live)
  * render_obsidian(vault)  → an Obsidian vault: one note per flow, every note
                              [[wiki-linked]] so the owner sees the same tree
                              as a graph in Obsidian.

Design rules:
  * Data only — this module never imports Flask and never reads user content.
    The live snapshot carries COUNTS and TIMESTAMPS, never message text.
  * Every step cites the real code file that implements it (the "c" field),
    so the tree can be verified against reality, not intentions.
"""

from __future__ import annotations

import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

TREE_VERSION = "1.0"

# ————————————————————————————————————————————————————————————————
# THE TREE
# Each flow: {"icon","title","why", "steps":[{"id","t","d","c","n"}], "related":[]}
#   t=title  d=what happens  c=code that does it  n=next step id
# ————————————————————————————————————————————————————————————————

FLOWS: dict[str, dict[str, Any]] = {
    "signin": {
        "icon": "🚪",
        "title": "تسجيل الدخول والوصول",
        "why": "من لحظة فتح المستخدم للرابط حتى أول طلب موثّق — كل باب له مفتاح.",
        "steps": [
            {"id": "open", "t": "المستخدم يفتح العميل (ويب/سطح المكتب/CLI)",
             "d": "الواجهة تقرأ الرابط والمفتاح من تخزين المتصفح المحلي فقط؛ لا كوكيز هوية.",
             "c": "web/src/api.ts", "n": "first-ask"},
            {"id": "first-ask", "t": "أول طلب API مع X-API-Key",
             "d": "كل /api/* تتطلب الترويسة X-API-Key عندما يعمل الخادم بوضع multi-user (AALI_API_KEY).",
             "c": "file-agent/app.py", "n": "auth"},
            {"id": "auth", "t": "الخادم يتحقق من المفتاح",
             "d": "مفتاح المدير (master) أو مفتاح مستخدم مُصدَر؛ تُفحص الحالة (ملغى؟ تجاوز الحد اليومي؟).",
             "c": "file-agent/app.py::_auth_state + file_agent/apikeys.py", "n": "isolate"},
            {"id": "isolate", "t": "عزل الجلسات لكل مستخدم",
             "d": "كل مفتاح يحصل على مساحة جلسات خاصة (بادئة u<key>:) — مستخدمان على خادم واحد لا يرى أحدهما محادثات الآخر.",
             "c": "file-agent/app.py::_client_key", "n": None},
        ],
        "related": ["key", "chat"],
    },
    "signup": {
        "icon": "📝",
        "title": "إنشاء حساب ذاتي (Signup)",
        "why": "المستخدم الجديد يحصل على مفتاحه بنفسه دون تدخل المدير.",
        "steps": [
            {"id": "page", "t": "صفحة /signup",
             "d": "استمارة واحدة: اسم مستعار فقط — لا بريد ولا كلمات مرور.",
             "c": "web/signup.html", "n": "register"},
            {"id": "register", "t": "POST /api/register",
             "d": "تحقق من السماح بالتسجيل الذاتي (AALI_OPEN_SIGNUP=0 يغلقه) وحد المعدل لكل IP/يوم.",
             "c": "file-agent/app.py::api_register", "n": "issue"},
            {"id": "issue", "t": "إصدار المفتاح",
             "d": "يُولَّد مفتاح aali-…، يُخزَّن SHA-256 فقط، ويُعرض صريحاً مرة واحدة للمستخدم.",
             "c": "file_agent/apikeys.py::signup", "n": "use"},
            {"id": "use", "t": "الاستخدام",
             "d": "المستخدم يحفظ المفتاح في إعدادات العميل؛ بعدها كل طلب يُحتسب عليه (طلبات/أحرف).",
             "c": "file-agent/app.py::_meter_chars", "n": None},
        ],
        "related": ["key", "signin"],
    },
    "chat": {
        "icon": "💬",
        "title": "دورة محادثة كاملة",
        "why": "ما يحدث فعلياً بين ضغطة إرسال وظهور الرد.",
        "steps": [
            {"id": "post", "t": "POST /api/ask أو /api/ask/stream",
             "d": "الرسالة + sid + سياسة التنفيذ. نسخة stream تبث أحداث النشاط الحية.",
             "c": "file-agent/app.py::api_ask", "n": "session"},
            {"id": "session", "t": "تحميل الجلسة",
             "d": "تُسترجع محفوظات sid (محدودة الطول) ليبقى السياق متصلاً عبر الطلبات.",
             "c": "file-agent/app.py::_get_session", "n": "loop"},
            {"id": "loop", "t": "agent_loop: العقل + الأدوات",
             "d": "يركّب موجه النظام (سياسة + أمان + مهارات + ذاكرة) ثم يدور: فكّر → أداة → نتيجة → فكّر… حتى الرد.",
             "c": "file-agent/agent_loop.py::agent_loop", "n": "tools"},
            {"id": "tools", "t": "بوابة السياسة ثم التنفيذ",
             "d": "كل استدعاء أداة يمر ببوابة (تلقائي/قوي/اسأل دائماً) — الخطير يوقف الدوران ويطلب تأكيد المستخدم.",
             "c": "file-agent/agent_loop.py::_policy_gate + file_agent/file_tools.py::execute_tool", "n": "final"},
            {"id": "final", "t": "تشطيب الرد",
             "d": "زخرفة إيموجي واحدة لائقة + اقتراحات متابعة بلغة المستخدم.",
             "c": "file-agent/file_agent/aali_emoji.py + file_agent/suggestions.py", "n": "save"},
            {"id": "save", "t": "الحفظ في مكانين",
             "d": "جلسة الخادم (sessions) + سجل المحادثات الدائم في ~/.aali/aali_conversations.jsonl.",
             "c": "file-agent/agent_loop.py::record_turn", "n": None},
        ],
        "related": ["brain", "memory", "output"],
    },
    "memory": {
        "icon": "🧠",
        "title": "الذاكرة الدائمة",
        "why": "كيف يتذكر آلي تعليماتك غداً، ولماذا لا يمكن للويب أو الملفات أن تخترق ذاكرته.",
        "steps": [
            {"id": "capture", "t": "التقاط التعليمات",
             "d": "كل رسالة مستخدم تُفحص: عبارات (من الآن/دائماً/تذكر…) تُحفظ تلقائياً حتى لو نسي آلي استدعاء الأداة.",
             "c": "file-agent/agent_loop.py::record_turn + file_agent/memory.py", "n": "guard"},
            {"id": "guard", "t": "بوابات الحفظ",
             "d": "المصدر يجب أن يكون المستخدم نفسه (لا نتائج أدوات/ويب)، والأسرار تُنقَّح إلى [REDACTED-SECRET].",
             "c": "file-agent/file_agent/memory.py::remember", "n": "store"},
            {"id": "store", "t": "التخزين",
             "d": "~/.aali/aali_memory.json — كتابة ذرّية على القرص قبل إرسال أي رد.",
             "c": "file-agent/file_agent/memory.py", "n": "recall"},
            {"id": "recall", "t": "الاستدعاء الهجين",
             "d": "كلمات مفتاحية + تشابه دلالي (nomic-embed-text) — أحدث تعليمة تفوز، والتناقض يُعلَن («سابقاً قلتَ X»).",
             "c": "file-agent/agent_loop.py::_memory_block", "n": None},
        ],
        "related": ["chat", "security"],
    },
    "brain": {
        "icon": "⚙️",
        "title": "أي عقل يجيب؟",
        "why": "قرار اختيار الدماغ في كل طلب — من نموذجك الخاص حتى السحابة الاحتياطية.",
        "steps": [
            {"id": "own", "t": "النموذج الخاص أولاً",
             "d": "إن وُجد checkpoint مُرقَّى (نموذج آلي المدرَّب من الصفر) فهو الافتراضي.",
             "c": "file-agent/agent_loop.py::promoted_own_model", "n": "ollama"},
            {"id": "ollama", "t": "أو Ollama المحلي — عقل مؤقت فقط",
             "d": "حلقة انتظار محلية 100% على جهازك (بلا إنترنت ولا مفاتيح ولا حسابات) يعمل بها آلي حتى تُرقَّى checkpoint نموذجك الخاص — ليست خدمة خارجية ولا تبعية: نفس سلسلة الأدوات والذاكرة والأمان تعمل فوقها، وتُستبدل تلقائياً بنموذجك عند ترقيته.",
             "c": "file-agent/agent_loop.py::_ollama_chat", "n": "scratch"},
            {"id": "scratch", "t": "أو النموذج الصغير المباشر",
             "d": "transformer من الصفر (hwk_model) — يعمل دائماً حتى قبل التدريب الكامل.",
             "c": "file-agent/hwk_model/model.py", "n": "cloud"},
            {"id": "cloud", "t": "الروابط الخارجية اختيارية فقط",
             "d": "mode=cloud عبر روابط مسجّلة (بمفاتيحك أنت من بيئة جهازك، لا يمر أي مفتاح عبر المحادثة أبداً) — عقل آلي يبقى نموذجك المدرَّب، والروابط مجرد جسر مؤقت اختياري.",
             "c": "file-agent/providers.py", "n": None},
        ],
        "related": ["chat", "training"],
    },
    "output": {
        "icon": "📡",
        "title": "الإخراج المباشر (SSE)",
        "why": "كيف ترى ما يفعله آلي لحظة بلحظة أثناء العمل.",
        "steps": [
            {"id": "bus", "t": "ناقل الأحداث",
             "d": "كل حدث (طلب أداة/نتيجة/اختيار مزود) يُنشر على قناة حية مرتبطة بمعرّف الطلب.",
             "c": "file-agent/agent_log.py", "n": "stream"},
            {"id": "stream", "t": "بث /api/ask/stream",
             "d": "أحداث activity فورية + نبضة keep-alive كل 15 ثانية، ثم حدث done أخير بحمولة كاملة.",
             "c": "file-agent/app.py::api_ask_stream", "n": "render"},
            {"id": "render", "t": "العرض في العميل",
             "d": "قائمة نشاط حية أثناء العمل، ثم الرد بتنسيق Markdown مع اقتراحات.",
             "c": "web/src/App.tsx::askStream", "n": None},
        ],
        "related": ["chat"],
    },
    "key": {
        "icon": "🔑",
        "title": "إصدار المفاتيح والاستهلاك",
        "why": "آلي مزوّد خدمة: كل مستخدم مفتاح، وكل مفتاح يُقاس ويُغلق عند الحاجة.",
        "steps": [
            {"id": "panel", "t": "لوحة المدير /admin",
             "d": "تتطلب مفتاح المدير (AALI_API_KEY)؛ عرض المستخدمين وإصدار/إلغاء المفاتيح ومتابعة الاستخدام.",
             "c": "file-agent/app.py::api_admin_keys_list", "n": "issue"},
            {"id": "issue", "t": "إصدار مفتاح",
             "d": "POST /api/admin/keys → يُخزَّن الهاش فقط، الصريح يُعرض مرة واحدة.",
             "c": "file_agent/apikeys.py::issue_key", "n": "meter"},
            {"id": "meter", "t": "قياس الاستهلاك",
             "d": "كل طلب: عدد الطلبات + أحرف داخل/خارج + آخر استخدام + حد يومي اختياري.",
             "c": "file_agent/apikeys.py::record_usage", "n": "revoke"},
            {"id": "revoke", "t": "الإلغاء",
             "d": "DELETE /api/admin/keys/<id> — المفتاح الملغى يُرفض فوراً في كل نقاط الدخول.",
             "c": "file_agent/apikeys.py::revoke", "n": None},
        ],
        "related": ["signin", "signup"],
    },
    "training": {
        "icon": "🏋️",
        "title": "التدريب من الصفر",
        "why": "من أين يأتي «عقل آلي» ولماذا هو ملكك بالكامل.",
        "steps": [
            {"id": "data", "t": "البيانات",
             "d": "متون عامة + عربية + حلقات تعليم من معلمين خارجيين + محادثاتك الحية (بعد تطهير الأسرار).",
             "c": "download_corpora.py + teacher_to_sft.py", "n": "tokens"},
            {"id": "tokens", "t": "الترميز",
             "d": "BPE 32k على مقاطع token shards خارج المستودع (D:/hwk-data).",
             "c": "tokenize_corpus.py", "n": "pretrain"},
            {"id": "pretrain", "t": "التدريب المسبق",
             "d": "Phase A/B قابل للاستئناف — ~125M معامل، نسخ احتياطي إلى X:.",
             "c": "train_scratch.py", "n": "sft"},
            {"id": "sft", "t": "الضبط الدقيق",
             "d": "SFT على القواعد والأدوات والذاكرة والأمان، ثم امتحان بوابة ترقية قبل أن يخدم المستخدمين.",
             "c": "scripts/soup_pipeline.py + docs/post_phaseA_pipeline.md", "n": None},
        ],
        "related": ["brain"],
    },
}

# ————— من أين يحصل آلي على المعلومات —————
SOURCES: dict[str, dict[str, str]] = {
    "model": {"label": "النموذج اللغوي", "path": "file-agent/hwk_model/",
              "what": "البنية (RoPE/SwiGLU/RMSNorm) + tokenizer BPE + توليد — العقلك من الصفر."},
    "sft": {"label": "بيانات SFT", "path": "data/*.jsonl",
            "what": "أمثلة المحادثة والأدوات والقواعد التي تتشكل منها ردود النموذج بعد التدريب."},
    "memory": {"label": "الذاكرة الدائمة", "path": "~/.aali/aali_memory.json",
               "what": "تعليماتك المحفوظة — خارج المستودع، لا يُرسل منها شيء لأي طرف."},
    "conversations": {"label": "سجل المحادثات", "path": "~/.aali/aali_conversations.jsonl",
                      "what": "كل الأدوار مؤرشفة بطابع زمني — مصدر حلقات SFT مستقبلاً."},
    "sessions": {"label": "جلسات الخادم", "path": "file-agent/sessions.jsonl",
                 "what": "سياق المحادثة قصير المدى لكل sid (معزول لكل مفتاح في multi-user)."},
    "rules": {"label": "القواعد والمهارات", "path": "rules/assistant_rules.md + skills/*.md",
              "what": "تُحمَّل في موجه النظام: أسلوب الرد، الأمان، أوامر الحاسوب، آداب الإيموجي."},
    "security": {"label": "دروس الأمان", "path": "docs/ai_security_lessons.md + skills/security-rules.md",
                 "what": "مُدرَّسة في كل موجات النظام ومفروضة في الكود (حجب/تنقيح/بوابات)."},
    "logs": {"label": "سجل الأحداث", "path": "file-agent/logs/agent.log",
             "what": "JSONL لدورة حياة كل طلب — للفحص والتشخيص، لا محتوى محادثة يُعرض للعملاء."},
    "ocr": {"label": "الوسائط والمستندات", "path": "win_ocr.ps1 + scripts/video_tools.py + file_agent/doc_tools.py",
            "what": "نصوص الصور (Windows OCR)، فيديو (إطارات + Whisper)، PDF/Word/Excel."},
}

SECURITY_NOTES: list[str] = [
    "البيئة محصورة: كل مسارات الأدوات تمر بـ _resolve داخل مجلد العمل — لا هروب بـ ../.",
    "الأوامر قائمة سماح صريحة (HWK_ALLOW_COMMANDS) وحجب صبّ البيئة (env/printenv/.env).",
    "الأسرار تُنقَّح من مخرجات الأدوات ومن الذاكرة قبل أي تخزين أو إرسال.",
    "بوابة السياسة: تلقائي/قوي/اسأل دائماً — الحذف والت overwrite الخطير يطلب تأكيداً.",
    "المفاتيح SHA-256 فقط على القرص؛ الصريح يُعرض مرة واحدة عند الإصدار.",
    "التسجيل الذات محدود المعدل لكل IP/يوم ويُغلق بـ AALI_OPEN_SIGNUP=0.",
    "ذاكرة آلي تقبل من المستخدم فقط — نتائج الأدوات/الويب لا تكتب ذاكرة (مضاد حقن الذاكرة).",
]

# ————————————————————————————————————————————————————————————————
# LIVE SNAPSHOT — counts and timestamps only, never user content
# ————————————————————————————————————————————————————————————————

def live_snapshot() -> dict[str, Any]:
    """Counters the owner can watch breathe: keys, log, sessions, memory, brain."""
    snap: dict[str, Any] = {
        "generated_at": time.time(),
        "generated_at_iso": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "tree_version": TREE_VERSION,
    }
    try:  # key platform counters
        from file_agent import apikeys
        snap["keys"] = apikeys.stats()
    except Exception:  # noqa: BLE001
        snap["keys"] = {}

    def _stat(path: Path) -> dict[str, Any]:
        try:
            st = path.stat()
            return {"exists": True, "size_kb": round(st.st_size / 1024, 1),
                    "modified": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")}
        except OSError:
            return {"exists": False}

    log_file = Path(os.getenv("AGENT_LOG_FILE", "")) if os.getenv("AGENT_LOG_FILE") else \
        Path(__file__).resolve().parents[1] / "logs" / "agent.log"
    snap["event_log"] = _stat(log_file)
    sessions_file = Path(__file__).resolve().parents[1] / "sessions.jsonl"
    try:
        sessions_file = Path(os.getenv("AALI_SESSIONS_FILE", sessions_file))
    except Exception:  # noqa: BLE001
        pass
    snap["sessions"] = _stat(sessions_file)
    mem_dir = Path(os.getenv("AALI_MEMORY_DIR", os.path.expanduser("~/.aali")))
    snap["memory_store"] = _stat(mem_dir / "aali_memory.json")
    snap["conversation_archive"] = _stat(mem_dir / "aali_conversations.jsonl")

    which = "النموذج الصغير المباشر (hwk_model)"
    try:  # which brain would answer right now
        from agent_loop import promoted_own_model, _ollama_available
        if promoted_own_model():
            which = "نموذج آلي الخاص ✦ (promoted checkpoint)"
        elif _ollama_available():
            which = "Ollama المحلي — عقل مؤقت حتى ترقية نموذج آلي الخاص"
    except Exception:  # noqa: BLE001
        pass
    snap["active_brain"] = which
    return snap


# ————————————————————————————————————————————————————————————————
# RENDERERS
# ————————————————————————————————————————————————————————————————

def render_ascii() -> str:
    """Plain-text tree — for the CLI, logs, and quick reading."""
    lines = [f"آلي — شجرة العقل v{TREE_VERSION}", ""]
    for i, (key, flow) in enumerate(FLOWS.items()):
        last_flow = i == len(FLOWS) - 1
        branch = "└─" if last_flow else "├─"
        lines.append(f"{branch} {flow['icon']} {flow['title']} ({key})")
        steps = flow["steps"]
        for j, step in enumerate(steps):
            last = j == len(steps) - 1
            sub = "   └─" if last_flow else "│  └─"
            arrow = "→ " + step["n"] if step.get("n") else "→ ●"
            lines.append(f"{sub} {step['t']}  [{arrow}]")
            if step.get("c"):
                pad = "      " if last_flow else "│     "
                lines.append(f"{pad} └ {step['c']}")
    lines.append("")
    lines.append("— المصادر —")
    for src in SOURCES.values():
        lines.append(f"  • {src['label']}: {src['path']}")
    return "\n".join(lines)


def render_html(live: dict[str, Any] | None = None, *, demo: bool = False,
                feed_token: str = "") -> str:
    """Self-contained RTL page for the admin-gated /brain route.

    feed_token: short-lived token allowing the page's live feed to stream
    without the browser ever holding the master key."""
    live_rows = ""
    if live:
        k = live.get("keys") or {}
        rows = [
            ("العقل النشط الآن", str(live.get("active_brain", "?"))),
            ("المفاتيح (كلي/نشط)", f"{k.get('total_keys', '—')} / {k.get('active_keys', '—')}"),
            ("إجمالي الطلبات", str(k.get("total_requests", "—"))),
            ("سجل الأحداث", _fmt_stat(live.get("event_log"))),
            ("الجلسات", _fmt_stat(live.get("sessions"))),
            ("الذاكرة الدائمة", _fmt_stat(live.get("memory_store"))),
            ("أرشيف المحادثات", _fmt_stat(live.get("conversation_archive"))),
        ]
        live_rows = "".join(
            f"<tr><th>{label}</th><td>{val}</td></tr>" for label, val in rows
        )
    flows_html = ""
    for key, flow in FLOWS.items():
        steps_html = ""
        for step in flow["steps"]:
            nxt = f'<span class="to">→ {step["n"]}</span>' if step.get("n") else '<span class="to end">●</span>'
            steps_html += (
                f"<li><div class='s-head'>{step['t']} {nxt}</div>"
                f"<div class='s-body'>{step['d']}</div>"
                f"<code>{step.get('c', '')}</code></li>"
            )
        rel = " ".join(f"<a class='rel' href='#{k2}'>{FLOWS[k2]['icon']} {FLOWS[k2]['title']}</a>"
                       for k2 in flow.get("related", []) if k2 in FLOWS)
        flows_html += (
            f"<section class='flow' id='{key}'>"
            f"<h2>{flow['icon']} {flow['title']} <small>({key})</small></h2>"
            f"<p class='why'>{flow['why']}</p>"
            f"<ol>{steps_html}</ol>"
            f"<div class='related'>يرتبط: {rel}</div></section>"
        )
    srcs = "".join(
        f"<tr><td>{s['label']}</td><td><code>{s['path']}</code></td><td>{s['what']}</td></tr>"
        for s in SOURCES.values()
    )
    sec = "".join(f"<li>{n}</li>" for n in SECURITY_NOTES)
    demo_banner = "<div class='demo'>عرض تجريبي — الأرقام ليست حية. أضف مفتاح المدير لعرض الحالة الحقيقية.</div>" if demo else ""
    return f"""<!doctype html>
<html lang="ar" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>آلي — شجرة العقل (المالك فقط)</title>
<style>
 body{{background:#121214;color:#f2f2f3;font-family:Tajawal,system-ui,sans-serif;margin:0;padding:24px;line-height:1.9}}
 .wrap{{max-width:980px;margin:0 auto}}
 h1{{color:#e8b34b;margin-bottom:2px}} h1 small{{color:#8f929c;font-weight:400}}
 .live{{background:#18181c;border:1px solid #2a2c35;border-radius:14px;padding:14px 18px;margin:18px 0}}
 .live table{{width:100%;border-collapse:collapse}} .live th{{text-align:start;color:#8f929c;font-weight:400;width:35%}}
 .live td{{color:#f5cf8a;font-weight:700}}
 .demo{{background:rgba(232,179,75,.12);border:1px solid rgba(232,179,75,.4);color:#f5cf8a;border-radius:12px;padding:10px 14px;margin:12px 0}}
 .flow{{background:#18181c;border:1px solid #2a2c35;border-radius:16px;padding:16px 20px;margin:14px 0}}
 .flow h2{{margin:0 0 4px;font-size:1.15rem}} .flow h2 small{{color:#8f929c;font-weight:400}}
 .why{{color:#8f929c;margin:0 0 10px}}
 ol{{margin:0;padding-inline-start:22px}} li{{margin:10px 0}}
 .s-head{{font-weight:700}} .to{{color:#e8b34b;font-size:.8rem}} .to.end{{color:#7fd6a4}}
 .s-body{{color:#b9bcc6;font-size:.92rem}}
 code{{display:block;background:#121214;border:1px solid #22242c;border-radius:8px;padding:2px 10px;margin-top:4px;color:#7aa5ff;font-size:.8rem;direction:ltr;text-align:left}}
 .related{{margin-top:10px;color:#8f929c;font-size:.85rem}}
 .rel{{display:inline-block;background:#23242b;border-radius:999px;padding:2px 10px;margin:2px;color:#f2f2f3;text-decoration:none}}
 table.src{{width:100%;border-collapse:collapse;background:#18181c;border-radius:14px;overflow:hidden}}
 table.src td,table.src th{{border-bottom:1px solid #22242c;padding:9px 12px;text-align:start;vertical-align:top}}
 table.src th{{color:#e8b34b}} table.src td:first-child{{font-weight:700;white-space:nowrap}}
 h2.sec{{color:#e8b34b;margin-top:26px}}
 .warn{{color:#f08c8c;font-weight:700}}
 .feed{{max-height:300px;overflow-y:auto;font-family:ui-monospace,Consolas,monospace;font-size:12px;direction:ltr;text-align:left}}
 .frow{{padding:4px 8px;border-bottom:1px solid #1e2027;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
 .frow .ev{{color:#e8b34b;font-weight:700}} .frow .tool{{color:#7aa5ff}} .frow .ok{{color:#7fd6a4}} .frow .err{{color:#f08c8c}}
 .frow.muted{{color:#8f929c}}
 .dot{{display:inline-block;width:8px;height:8px;border-radius:50%;background:#7fd6a4;margin-inline-end:6px;animation:pulse 2s infinite}}
 @keyframes pulse{{0%,100%{{opacity:1}}50%{{opacity:.3}}}}
</style></head><body><div class="wrap">
<h1>🧠 شجرة عقل آلي <small>v{TREE_VERSION} — للمالك فقط، تحديث حي</small></h1>
<p class="warn">هذه الصفحة إدارية: تُظهر كيف يعمل آلي من الداخل. لا تُشارك مع المستخدمين.</p>
{demo_banner}
{f"<div class='live'><h3>⏱ الحالة الحية — {live.get('generated_at_iso', '')}</h3><table>{live_rows}</table></div>" if live else ""}
<div class="live">
 <h3>📡 نبض آلي الحي — الأحداث كما تحدث</h3>
 <div id="feed" class="feed"><div class="frow muted">جارٍ الاتصال…</div></div>
</div>
{flows_html}
<h2 class="sec">📥 من أين يحصل آلي على المعلومات؟</h2>
<table class="src"><tr><th>المصدر</th><th>المسار</th><th>ماذا يقدّم</th></tr>{srcs}</table>
<h2 class="sec">🛡️ بوابات الأمان المفروضة</h2>
<ul>{sec}</ul>
</div>
<script>
(function(){{
  var feed=document.getElementById('feed'); if(!feed) return;
  var url='/api/brain/stream?limit=25'+'{f"&feed_token={feed_token}" if feed_token else ""}';
  function row(ev){{
    var d=document.createElement('div'); d.className='frow';
    var t=(ev.timestamp||'').replace('T',' ').slice(5,19);
    var kind=ev.event||'?';
    var cls=kind.indexOf('error')>=0?'err':(kind==='tool_result'?'ok':'');
    var detail=ev.tool?(' <span class="tool">'+ev.tool+'</span>')
      :(ev.provider?(' <span class="tool">'+ev.provider+'</span>'):'');
    var extra=ev.duration_ms?(' '+ev.duration_ms+'ms'):'';
    d.innerHTML='<span class="dot"></span>'+t+' <span class="ev '+cls+'">'+kind+'</span>'+detail+extra;
    return d;
  }}
  function connect(){{
    fetch(url).then(function(r){{ if(!r.ok) throw new Error('HTTP '+r.status); return r.body; }})
    .then(function(body){{
      var reader=body.getReader(), dec=new TextDecoder(), buf='';
      function pump(){{
        return reader.read().then(function(res){{
          if(res.done) {{ setTimeout(connect, 5000); return; }}
          buf+=dec.decode(res.value, {{stream:true}});
          var parts=buf.split('\\n\\n'); buf=parts.pop();
          parts.forEach(function(chunk){{
            var line=chunk.split('\\n').find(function(l){{return l.indexOf('data:')===0;}});
            if(!line) return;
            try{{ var ev=JSON.parse(line.slice(5));
              feed.appendChild(row(ev));
              while(feed.children.length>400) feed.removeChild(feed.firstChild);
              feed.scrollTop=feed.scrollHeight;
            }}catch(e){{}}
          }});
          return pump();
        }});
      }}
      return pump();
    }}).catch(function(){{
      feed.innerHTML='<div class="frow muted">تعذّر الاتصال بالبث — إعادة المحاولة…</div>';
      setTimeout(connect, 8000);
    }});
  }}
  connect();
}})();
</script>
</body></html>"""


def _fmt_stat(st: Any) -> str:
    if not st or not st.get("exists"):
        return "غير موجود بعد"
    return f"{st.get('size_kb', 0)} KB · آخر تحديث {st.get('modified', '—')}"


# ————————————————————————————————————————————————————————————————
# OBSIDIAN VAULT
# ————————————————————————————————————————————————————————————————

def render_obsidian(vault: Path) -> list[Path]:
    """Write an Obsidian vault: ROOT + one linked note per flow + sources + security."""
    vault = Path(vault)
    vault.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    root = vault / "ROOT.md"
    links = " ".join(f"[[{flow_file_name(k)}|{f['icon']} {f['title']}]]" for k, f in FLOWS.items())
    root.write_text(
        f"# 🧠 شجرة عقل آلي v{TREE_VERSION}\n\n"
        "> خريطة المالك لكيف يعمل آلي — افتح Graph View لرؤية الشجرة كاملة.\n"
        "> تُصدَّر من الكود نفسه؛ كل خطوة تذكر ملفها المنفّذ.\n\n"
        f"## التدفقات\n{links}\n\n"
        f"- [[SOURCES — من أين تأتي المعلومات]]\n"
        f"- [[SECURITY — بوابات الأمان]]\n\n"
        f"صُدِّرت: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n",
        encoding="utf-8",
    )
    written.append(root)

    for key, flow in FLOWS.items():
        body = [f"# {flow['icon']} {flow['title']}", "", f"> {flow['why']}", ""]
        for i, step in enumerate(flow["steps"], 1):
            nxt = f" → **{step['n']}**" if step.get("n") else " → ● النهاية"
            body.append(f"{i}. **{step['t']}**{nxt}")
            body.append(f"   - {step['d']}")
            if step.get("c"):
                body.append(f"   - الكود: `{step['c']}`")
            body.append("")
        rel = " ".join(f"[[{flow_file_name(k2)}|{FLOWS[k2]['title']}]]"
                       for k2 in flow.get("related", []) if k2 in FLOWS)
        body += [f"**يرتبط:** {rel}", ""]
        path = vault / flow_file_name(key)
        path.write_text("\n".join(body), encoding="utf-8")
        written.append(path)

    src_body = ["# 📥 من أين تأتي المعلومات", ""]
    for s in SOURCES.values():
        src_body += [f"- **{s['label']}** — `{s['path']}`", f"  - {s['what']}"]
    src_body.append("")
    src_path = vault / "SOURCES — من أين تأتي المعلومات.md"
    src_path.write_text("\n".join(src_body), encoding="utf-8")
    written.append(src_path)

    sec_body = ["# 🛡️ بوابات الأمان", ""] + [f"- {n}" for n in SECURITY_NOTES] + [""]
    sec_path = vault / "SECURITY — بوابات الأمان.md"
    sec_path.write_text("\n".join(sec_body), encoding="utf-8")
    written.append(sec_path)
    return written


def flow_file_name(key: str) -> str:
    return f"Flow — {key}.md"
