"""Prompt library (Wave 3 #9, 2026-09-25).

Built-in starter prompts (Arabic-first, English second) + per-user
custom prompts stored ns-scoped (same u<key_id>/local contract as
projects/shares). Pure data + a tiny JSONL store; the UI inserts a
prompt into the composer (never auto-sends without the user's send).
"""
from __future__ import annotations

import json
import secrets
import time
from pathlib import Path
from typing import Any

DEFAULT_STORE = Path("D:/hwk-data/prompt_library.jsonl")

MAX_TITLE = 80
MAX_BODY = 4000
MAX_CUSTOM_PER_NS = 100

# ————— built-in starters (arabic-first) —————
BUILTIN: list[dict[str, str]] = [
    {"id": "b_summary", "lang": "ar", "icon": "📝",
     "title": "لخّص ملفاً",
     "body": "اقرأ الملف التالي في مجلد العمل ولخّص أهم النقاط بالعربية: "},
    {"id": "b_app", "lang": "ar", "icon": "🛠️",
     "title": "ابنِ تطبيق ويب",
     "body": "أنشئ تطبيق ويب بسيط في مجلد العمل ثم شغّله وأرني النتيجة. الفكرة: "},
    {"id": "b_review", "lang": "ar", "icon": "🔍",
     "title": "راجع كوداً",
     "body": "راجع الكود في الملف التالي: اذكر الأخطاء والمشاكل الأمنية واقترح تحسينات: "},
    {"id": "b_translate", "lang": "ar", "icon": "🌍",
     "title": "ترجم واختصر",
     "body": "ترجم النص التالي إلى الإنجليزية بأسلوب واضح ومختصر:\n"},
    {"id": "b_email", "lang": "ar", "icon": "✉️",
     "title": "اكتب رسالة",
     "body": "اكتب رسالة رسمية قصيرة بالعربية حول الموضوع التالي: "},
    {"id": "b_plan", "lang": "ar", "icon": "🗂️",
     "title": "خطة عمل",
     "body": "ضع لي خطة عمل من مراحل واضحة ومهام قابلة للقياس للمشروع التالي: "},
    {"id": "b_excel", "lang": "ar", "icon": "📊",
     "title": "جدول بيانات",
     "body": "أنشئ ملف إكسل فيه البيانات التالية وارسم مخططاً مناسباً: "},
    {"id": "en_summary", "lang": "en", "icon": "📝",
     "title": "Summarize a file",
     "body": "Read the following file in the workspace and summarize the key points: "},
    {"id": "en_app", "lang": "en", "icon": "🛠️",
     "title": "Build a web app",
     "body": "Create a simple web app in the workspace, run it, and show me the result. Idea: "},
    {"id": "en_review", "lang": "en", "icon": "🔍",
     "title": "Review code",
     "body": "Review the code in the following file: list bugs, security issues, and improvements: "},
    {"id": "en_email", "lang": "en", "icon": "✉️",
     "title": "Draft an email",
     "body": "Draft a short professional email about: "},
    {"id": "en_plan", "lang": "en", "icon": "🗂️",
     "title": "Action plan",
     "body": "Draft an action plan with clear phases and measurable tasks for: "},
]


def _load(path: Path | str | None) -> list[dict[str, Any]]:
    p = Path(path) if path else DEFAULT_STORE
    out: list[dict[str, Any]] = []
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict) and rec.get("id") and not rec.get("deleted"):
                out.append(rec)
    except OSError:
        return []
    return out


def _save(rows: list[dict[str, Any]], path: Path | str | None) -> None:
    p = Path(path) if path else DEFAULT_STORE
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for rec in rows:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    tmp.replace(p)


def list_prompts(ns: str = "", path: Path | str | None = None
                 ) -> dict[str, Any]:
    """Builtins + this ns's custom prompts (custom first, newest first)."""
    mine = [r for r in _load(path) if r.get("ns") == str(ns or "")]
    mine.sort(key=lambda r: r.get("created", 0), reverse=True)
    return {
        "builtin": BUILTIN,
        "custom": [
            {"id": r["id"], "title": r.get("title", ""),
             "body": r.get("body", ""), "icon": r.get("icon", "✦"),
             "created": r.get("created", 0)}
            for r in mine
        ],
    }


def add_prompt(title: str, body: str, ns: str = "", icon: str = "✦",
               path: Path | str | None = None) -> dict[str, Any]:
    title = (title or "").strip()[:MAX_TITLE]
    body = (body or "").strip()[:MAX_BODY]
    if not title or not body:
        return {"ok": False, "error": "العنوان والنص مطلوبان"}
    rows = [r for r in _load(path) if r.get("ns") == str(ns or "")]
    if len(rows) >= MAX_CUSTOM_PER_NS:
        return {"ok": False, "error": f"الحد {MAX_CUSTOM_PER_NS} موجه مخصص"}
    rec = {"id": "p_" + secrets.token_hex(6), "ns": str(ns or ""),
           "title": title, "body": body, "icon": (icon or "✦")[:4],
           "created": time.time()}
    rows = _load(path)
    rows.append(rec)
    _save(rows, path)
    return {"ok": True, "prompt": {"id": rec["id"], "title": title,
                                   "body": body, "icon": rec["icon"],
                                   "created": rec["created"]}}


def delete_prompt(pid: str, ns: str = "",
                  path: Path | str | None = None) -> dict[str, Any]:
    rows = _load(path)
    kept = [r for r in rows
            if not (r.get("id") == pid and r.get("ns") == str(ns or ""))]
    if len(kept) == len(rows):
        return {"ok": False, "error": "الموجه غير موجود"}
    _save(kept, path)
    return {"ok": True}
