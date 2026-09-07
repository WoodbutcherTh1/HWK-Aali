"""Emoji intelligence for Aali: understand, react, and use them well.

Owner request (2026-09-06): Aali must (1) understand the emotion inside the
user's emojis, (2) react to that feeling in his reply, and (3) know when,
where, and what emoji to add himself — without ever overdoing it.

Design:
- analyze(): map emojis to a small emotion model (joy/sadness/anger/fear/
  surprise/affection/celebration/disappointment + none). Reactions are chosen
  from the emotion, in the user's language.
- decorate(): add AT MOST ONE fitting emoji to a final reply, only when the
  context invites warmth or the news is positive, and NEVER on refusals,
  errors, security warnings, or serious topics — a laughing emoji on a
  refusal would embarrass the owner (the no-shame rule).
- Both are deterministic, cheap, and safe to run on every reply.
"""

from __future__ import annotations

import re

_ARABIC = re.compile(r"[\u0600-\u06FF]")
_EMOJI = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F0FF"
    "\U0001F900-\U0001F9FF\U00002700-\U000027BF\u2190-\u21FF\u2B00-\u2BFF\uFE0F]"
)

# emotion -> (emojis that signal it, reaction AR, reaction EN, success emoji)
_EMOTIONS: dict[str, tuple[tuple[str, ...], str, str, str]] = {
    "celebration": (
        ("🎉", "🥳", "🎊", "🍾"),
        "مبروك! يسعدني أخبار الفرح 🎉",
        "Congratulations! So happy to hear it 🎉",
        "🎉",
    ),
    "joy": (
        ("😄", "😃", "😀", "😁", "😊", "🙂", "☺️", "😂", "🤣"),
        "يفرح قلبي وأنت كذا 😄",
        "Love the good mood 😄",
        "😄",
    ),
    "affection": (
        ("❤️", "💕", "😍", "🥰", "😘", "💙", "💚", "🤗"),
        "وياك يا غالي 🤗",
        "Right back at you 🤗",
        "🤗",
    ),
    "sadness": (
        ("😢", "😭", "😞", "😔", "🙁", "💔", "🥺"),
        "أشوف إن في شي حزينك — أنا معك، ونصلحها سوا 😔",
        "I can see something's weighing on you — I'm here, we'll figure it out 😔",
        "",  # no cheerful decoration on sad turns
    ),
    "anger": (
        ("😠", "😡", "🤬", "👿"),
        "أقدّر غضبك، وخلينا نحل اللي أغضبك خطوة خطوة 😔",
        "I get why you're angry — let's fix what caused it, step by step 😔",
        "",
    ),
    "fear": (
        ("😨", "😰", "😱", "😖", "😟"),
        "لا تقلق، نأخذها بهدوء خطوة خطوة 🙏",
        "No worries — we'll take it calmly, step by step 🙏",
        "",
    ),
    "surprise": (
        ("😲", "😮", "🤯", "😯", "😦"),
        "والله مفاجأة! خليني أساعدك تتعامل معها 😮",
        "That is a surprise! Let me help you handle it 😮",
        "😮",
    ),
    "disappointment": (
        ("😞", "☹️", "😣", "😤", "🙄"),
        "صدقًا موفقك هالشي مزعج — جرب حل بديل؟ 😔",
        "That really is frustrating — want me to try an alternative? 😔",
        "",
    ),
}

_URGENT_BAD = re.compile(
    r"(error|failed|failure|تعذر|خطأ|فشل|تحذير|warning|security|أمان|"
    r"لا أستطيع|ما أقدر|لا يمكنني|cannot|can't|refused)",
    re.IGNORECASE,
)
_SERIOUS = re.compile(
    r"(password|كلمة السر|token|مفتاح|secret|سر|وفيك|وفاة|تعزية|"
    r"hospital|مستشفى|emergency|طوارئ)",
    re.IGNORECASE,
)
_POSITIVE_DONE = re.compile(
    r"(تم|نجح|أنشأت|أُنشئ|جاهز|اكتمل|✅|created|success|done|finished|fixed)",
    re.IGNORECASE,
)


def analyze(text: str) -> dict:
    """Read the emotion out of the user's emojis (first match wins)."""
    found = _EMOJI.findall(text or "")
    emotion, reaction = "none", ""
    for code, (_emojis, reaction_ar, reaction_en, _success) in _EMOTIONS.items():
        if any(e in found for e in _emojis):
            emotion = code
            reaction = reaction_ar if _ARABIC.search(text or "") else reaction_en
            break
    return {
        "emojis": found[:8],
        "emotion": emotion,
        "reaction": reaction,
        "user_is_arabic": bool(_ARABIC.search(text or "")),
    }


def decorate(reply: str, user_text: str = "") -> str:
    """Add at most one context-fitting emoji to the final reply.

    Rules enforced here (never left to the model's mood):
    - never on errors/refusals/security messages (_URGENT_BAD)
    - never on serious/secret topics (_SERIOUS)
    - never if the reply already carries an emoji (anti-overdo)
    - success messages earn a fitting emoji; plain answers stay plain
    """
    if not reply:
        return reply
    if _EMOJI.search(reply):
        return reply  # already has one - never pile on
    if _URGENT_BAD.search(reply) or _SERIOUS.search(reply or "") or _SERIOUS.search(user_text or ""):
        return reply
    arabic = bool(_ARABIC.search(user_text or ""))
    info = analyze(user_text or "")
    if info["emotion"] != "none":
        mirror = info["reaction"].rsplit(" ", 1)[-1] if info["reaction"] else ""
        success = _EMOTIONS.get(info["emotion"], ("", "", "", ""))[3]
        chosen = success or (mirror if _EMOJI.fullmatch(mirror or "") else "")
        return f"{reply} {chosen}".strip() if chosen else reply
    if _POSITIVE_DONE.search(reply):
        return f"{reply} ✅"
    return reply
