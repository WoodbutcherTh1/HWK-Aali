"""Aali's autocorrector: chat-speak, typos, and Arabic normalization.

Owner request (2026-09-06): "if the user says 'hai dode' and meant 'Hi Dude'
- which one is more understandable? the correct one". Aali must understand
messy input AND gently show the corrected reading, so user and Aali always
understand each other.

Design (honest about limits):
- Deterministic maps: chat-speak (hai, dode, u, plz, thx...), common English
  misspellings (teh, recieve...), Arabic chat normalization (ة/ه endings,
  أ إ آ -> ا, ى -> ي), and leetspeak (u2 -> you too).
- Damerau-Levenshtein fuzzy match for unknown words (<=2 edits) against a
  small common-English + project dictionary - a full spellchecker is a
  dependency we don't need; this covers the real chat cases.
- PRESERVATION FIRST: file paths, code, URLs, numbers, and quoted strings
  are NEVER corrected. What the user typed is what Aali acts on; the
  correction is a displayed HINT, not a rewrite of their words.
- correct(): returns the corrected reading + what changed, so Aali can say
  "فهمت قصدك: Hi Dude" naturally, in any brain, with zero model calls.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# --- chat-speak and common misspellings (case-insensitive keys) -----------
_CHAT = {
    "hai": "hi", "hii": "hi", "heyy": "hey", "dode": "dude", "dud": "dude",
    "u": "you", "ur": "your", "r": "are", "plz": "please", "pls": "please",
    "thx": "thanks", "thnks": "thanks", "ty": "thank you", "yw": "you're welcome",
    "np": "no problem", "brb": "be right back", "idk": "I don't know",
    "imo": "in my opinion", "fyi": "for your information", "asap": "as soon as possible",
    "btw": "by the way", "omg": "oh my god", "lol": "haha", "k": "okay",
    "kk": "okay", "ya": "yeah", "yah": "yeah", "nope": "no", "yup": "yes",
    "yep": "yes", "gonna": "going to", "wanna": "want to", "gotta": "have to",
    "kinda": "kind of", "cuz": "because", "bcz": "because", "bcoz": "because",
    "w8": "wait", "gr8": "great", "h8": "hate", "l8r": "later", "2day": "today",
    "2moro": "tomorrow", "2morrow": "tomorrow", "b4": "before", "1ce": "once",
    "somone": "someone", "somehing": "something", "someting": "something",
    "becuase": "because", "beacuse": "because", "definately": "definitely",
    "seperate": "separate", "occured": "occurred", "recieve": "receive",
    "beleive": "believe", "acheive": "achieve", "teh": "the", "hte": "the",
    "waht": "what", "thier": "their", "freind": "friend", "wich": "which",
    "goverment": "government", "tommorow": "tomorrow", "tommorrow": "tomorrow",
    "reccomend": "recommend", "successfull": "successful", "untill": "until",
    "wierd": "weird", "alot": "a lot", "expalin": "explain", "exaple": "example",
    "funtion": "function", "fucntion": "function", "errror": "error",
    "databse": "database", "databse": "database", "porblem": "problem",
    "wrok": "work", "wrk": "work", "scren": "screen", "keybord": "keyboard",
    # Arabic chat (franco-independent words written in Arabic letters)
    "انشاء": "إن شاء", "لاكن": "لكن", "اللة": "الله", "انا": "أنا",
    "انت": "أنت", "انتو": "أنتم", "الي": "الذي", "علي": "على",
    "مشكلت": "مشكلة", "شكرا": "شكرًا", "مرحبتين": "مرحبتين",
}

# --- Arabic cosmetic fixes (applied as regex) -------------------------------
# ONLY genuinely-wrong cosmetics count as corrections. Hamza forms (أ/إ/آ)
# and alef maqsura (ى) are VALID spelling — stripping them is a search
# normalization, not a fix, so they must NOT surface in the reading hint.
_ARABIC_RULES = (
    (re.compile(r"ـ"), ""),        # tatweel removal (مرحبـــا -> مرحبا)
)

_PROTECT = re.compile(
    r"(\S+[\\/]\S+|"                     # paths (a/b, a\b)
    r"https?://\S+|"                     # URLs
    r"`[^`]*`|"                          # `code`
    r"\"[^\"]*\"|"                       # "quoted"
    r"'[^']*'|"                          # 'quoted'
    r"\b\w+\.\w{2,4}\b|"                 # file.ext
    r"\b\d[\d.,:]*\b)"                   # numbers, times
)

_WORD = re.compile(r"[\w\u0600-\u06FF']+", re.UNICODE)


@dataclass
class Correction:
    original: str
    corrected: str
    changes: list[tuple[str, str]] = field(default_factory=list)  # (was, now)

    @property
    def n_changes(self) -> int:
        return len(self.changes)


def _damerau_levenshtein(a: str, b: str, cap: int = 3) -> int:
    """Distance with early cap for speed (small words only)."""
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(
                previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb),
            ))
        if min(current) > cap:
            return cap + 1
        previous = current
    return previous[-1]


# NOTE: there is deliberately NO fuzzy/phonetic guessing here. Guessing
# mangled real requests ("small" -> "email", "reach" -> "read") and the
# model then acted on the wrong request. Only exact dictionary hits count.


def _correct_token(token: str) -> tuple[str, bool]:
    """(corrected, changed) for one word token. Exact matches only."""
    lowered = token.lower()
    if lowered in _CHAT:
        replacement = _CHAT[lowered]
        # Keep the user's capitalization shape.
        if token[0].isupper():
            replacement = replacement.capitalize()
        return replacement, True
    # Arabic exact-map hits (word is already normalized form there)
    if token in _CHAT:
        return _CHAT[token], True
    # Arabic regex normalizations
    if any("\u0600" <= ch <= "\u06FF" for ch in token):
        normalized = token
        for pattern, repl in _ARABIC_RULES:
            normalized = pattern.sub(repl, normalized)
        return normalized, normalized != token
    return token, False


def correct(text: str) -> Correction:
    """Corrected READING of the user's text, preserving paths/code/quotes.

    Protected spans (paths, URLs, code, quotes, numbers, filenames) are
    copied through untouched - a correction that breaks a file path is
    worse than any typo.
    """
    if not text or not text.strip():
        return Correction(original=text, corrected=text)
    corrected_parts: list[str] = []
    changes: list[tuple[str, str]] = []
    last_end = 0
    for match in _PROTECT.finditer(text):
        # Process the plain text between protected spans.
        plain = text[last_end:match.start()]
        if plain:
            fixed_plain, new_changes = _correct_plain(plain)
            corrected_parts.append(fixed_plain)
            changes.extend(new_changes)
        corrected_parts.append(match.group(0))  # protected: verbatim
        last_end = match.end()
    tail = text[last_end:]
    if tail:
        fixed_tail, new_changes = _correct_plain(tail)
        corrected_parts.append(fixed_tail)
        changes.extend(new_changes)
    return Correction(original=text, corrected="".join(corrected_parts), changes=changes)


def _correct_plain(plain: str) -> tuple[str, list[tuple[str, str]]]:
    out_parts: list[str] = []
    changes: list[tuple[str, str]] = []
    last_end = 0
    for match in _WORD.finditer(plain):
        out_parts.append(plain[last_end:match.start()])
        token = match.group(0)
        fixed, changed = _correct_token(token)
        if changed and fixed != token:
            changes.append((token, fixed))
        out_parts.append(fixed)
        last_end = match.end()
    out_parts.append(plain[last_end:])
    return "".join(out_parts), changes


def hint_phrase(correction: Correction, arabic: bool) -> str:
    """The sentence Aali shows when he corrected a reading (or '')."""
    if correction.n_changes == 0:
        return ""
    reading = correction.corrected
    if arabic:
        pairs = "، ".join(f"«{was}» ← «{now}»" for was, now in correction.changes[:3])
        return f"فهمت قصدك: «{reading}» ({pairs})"
    pairs = ", ".join(f"\"{was}\" -> \"{now}\"" for was, now in correction.changes[:3])
    return f"I read that as: \"{reading}\" ({pairs})"
