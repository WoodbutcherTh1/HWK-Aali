"""Suggestion chips for Aali's replies — the "what next?" engine.

Owner request (2026-09-06): the senior agents suggest follow-up actions and
the user just clicks one; Aali should do the same. This module generates up
to three short, safe, actionable suggestions after each reply, in the user's
language, from deterministic rules that work on ANY brain (Ollama, scratch,
cloud) without an extra model call.

Design rules:
- Suggestions never reveal internals, never promise irreversible actions, and
  never push toward destructive tools (no delete/install/kill suggestions).
- Generic fallbacks exist for when no rule matches, so the UI always has
  something useful — but they stay honest ("what can you do?" style).
- Deterministic and cheap: pure string rules, no model round-trip.
"""

from __future__ import annotations

import re

_ARABIC = re.compile(r"[\u0600-\u06FF]")

# (pattern-on-reply-or-request, arabic suggestion, english suggestion)
_RULES: tuple[tuple[str, str, str], ...] = (
    # media
    (r"generate_image|edit_image|watermark|thumbnail|صورة", "حجم الصورة لتكون غلافًا (1280×720)", "Resize the image to a 1280×720 cover"),
    (r"edit_video|ffmpeg|فيديو|مقطع", "استخرج الصوت من المقطع", "Extract the audio from the clip"),
    (r"\bgif\b|متحركة", "حوّل مقطعًا قصيرًا إلى GIF", "Turn a short clip into a GIF"),
    # files / workspace
    (r"write_file|أنشأت|أُنشئ|تم إنشاء|created", "اعرض الملفات للتأكد", "List the files to verify"),
    (r"search_files|بحث|search", "اقرأ أفضل نتيجة كاملة", "Read the best result in full"),
    (r"web_search|fetch_url|صفحة|search the web", "لخّص الصفحة في نقاط", "Summarize the page in bullet points"),
    # memory
    (r"memory|تذكر|ذاكرة|remember", "احفظ هذا كمفضلة دائمة", "Save this as a standing preference"),
    (r"ماذا قلت|what did i say|سابقًا", "ابحث في الذاكرة عن الموضوع", "Search memory for the topic"),
    # machine ops (safe ones only)
    (r"system_info|نظام|GPU|الذاكرة RAM", "راقب الحرارة مرة أخرى بعد ساعة", "Check system stats again in an hour"),
    (r"list_processes|العمليات", "افتح التطبيق الذي أغلقته", "Reopen the app we discussed"),
    # training / project
    (r"تدريب|training|checkpoint|Phase A", "اعرض آخر خطوة في التدريب", "Show the latest training step"),
    (r"امتحان|exam|اختبار", "شغّل الامتحان على آخر checkpoint", "Run the exam on the latest checkpoint"),
    # verification behavior
    (r"لا أعرف|could not verify|لم أتحقق", "ابحث في الويب للتأكد", "Search the web to verify"),
)

_GENERIC_AR = ("ماذا تستطيع أن تفعل أيضًا؟", "احفظ هذا القرار في ذاكرتك", "اعرض الملفات في مجلد العمل")
_GENERIC_EN = ("What else can you do?", "Save this decision to your memory", "List the files in the workspace")

MAX_SUGGESTIONS = 3


def _has_arabic(*texts: str) -> bool:
    return any(_ARABIC.search(t or "") for t in texts)


def suggest(reply: str, user_message: str = "") -> list[str]:
    """Up to three follow-up suggestions for this turn, user's language first."""
    arabic = _has_arabic(user_message, reply)
    haystack = f"{user_message}\n{reply}" or ""
    lowered = haystack.lower()

    picked: list[str] = []
    seen: set[str] = set()
    for pattern, ar, en in _RULES:
        if re.search(pattern, lowered):
            suggestion = ar if arabic else en
            if suggestion not in seen:
                seen.add(suggestion)
                picked.append(suggestion)
            if len(picked) >= MAX_SUGGESTIONS:
                break

    if len(picked) < MAX_SUGGESTIONS:
        generics = (_GENERIC_AR if arabic else _GENERIC_EN)
        for suggestion in generics:
            if len(picked) >= MAX_SUGGESTIONS:
                break
            if suggestion not in seen:
                seen.add(suggestion)
                picked.append(suggestion)
    return picked[:MAX_SUGGESTIONS]
