# -*- coding: utf-8 -*-
"""Language detection + tracking (AR/HE/EN, code-switch aware).

Two layers:
  - script detection on TEXT (fast, deterministic): Arabic block -> ar,
    Hebrew block -> he, else Latin -> en. Used per-sentence so a mixed reply
    speaks each sentence in its own tongue.
  - LanguageTracker: smoothed utterance-level language from Whisper's
    per-utterance detection (majority vote + confidence gate).
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Dict, List, Optional

# Arabic block incl. presentation forms; Hebrew block incl. points
_AR_RANGES = ((0x0600, 0x06FF), (0x0750, 0x077F), (0x08A0, 0x08FF), (0xFB50, 0xFDFF), (0xFE70, 0xFEFF))
_HE_RANGES = ((0x0590, 0x05FF), (0xFB1D, 0xFB4F))


# XTTS-v2 tokenizer window per language (chars). Past this the engine warns
# and TRUNCATES the utterance — never feed it more.
ENGINE_CHAR_CAPS: Dict[str, int] = {"ar": 160, "he": 160, "en": 280}


def _in_ranges(ch: str, ranges) -> bool:
    o = ord(ch)
    return any(lo <= o <= hi for lo, hi in ranges)


def detect_script_lang(text: str) -> Optional[str]:
    """Deterministic script language: 'ar' | 'he' | 'en' | None (no letters)."""
    ar = he = latin = 0
    for ch in text:
        if _in_ranges(ch, _AR_RANGES):
            ar += 1
        elif _in_ranges(ch, _HE_RANGES):
            he += 1
        elif ch.isascii() and ch.isalpha():
            latin += 1
    letters = ar + he + latin
    if letters == 0:
        return None
    if ar > he and ar >= latin:
        return "ar"
    if he > ar and he >= latin:
        return "he"
    if latin >= ar and latin >= he:
        return "en"
    return None


def split_sentences(text: str) -> List[str]:
    """Light sentence split respecting AR/HE/EN terminators."""
    import re

    if not text.strip():
        return []
    raw = re.split(r"(?<=[.!؟?…])\s+", text.strip())
    merged: List[str] = []
    for p in raw:
        if merged and len(p) < 4:  # stray fragment joins the previous
            merged[-1] += " " + p
        else:
            merged.append(p)
    return [p for p in merged if p.strip()]


def chunk_for_tts(
    text: str,
    max_chars: int = 240,
    lang_caps: Optional[Dict[str, int]] = None,
) -> List[str]:
    """Split a reply into TTS-sized chunks.

    Sentence-first, hard-cap fallback. Language-homogeneous sentences are
    packed together; a script CHANGE always starts a new chunk so each
    chunk speaks in a single tongue (a short mixed reply must not collapse
    into one chunk spoken in one language).

    `lang_caps` bounds a chunk by the ENGINE's per-language limit, not just
    by `max_chars`: XTTS silently truncates anything past its tokenizer
    window (166 chars for ar/he, ~300 for en) — a truncated reply is a
    broken voice, so the smaller per-language cap always wins.
    """
    caps = lang_caps if lang_caps is not None else ENGINE_CHAR_CAPS

    def cap_for(lang: Optional[str]) -> int:
        return min(max_chars, caps.get(lang or "", max_chars))

    sentences = split_sentences(text)
    if not sentences:
        return [text] if text.strip() else []
    chunks: List[str] = []
    cur = ""
    cur_lang: Optional[str] = None
    for s in sentences:
        s_lang = detect_script_lang(s)
        cap = cap_for(s_lang or cur_lang)
        cand = f"{cur} {s}".strip()
        lang_change = bool(cur and s_lang and cur_lang and s_lang != cur_lang)
        if len(cand) <= cap and not lang_change:
            cur = cand
            cur_lang = s_lang or cur_lang
            continue
        if cur:
            chunks.append(cur)
        if len(s) <= cap:
            cur = s
            cur_lang = s_lang
        else:  # pathological long sentence: hard-split on spaces
            words = s.split()
            cur = ""
            for w in words:
                cand = f"{cur} {w}".strip()
                if len(cand) > cap and cur:
                    chunks.append(cur)
                    cur = w
                else:
                    cur = cand
            cur_lang = detect_script_lang(cur) if cur else None
    if cur:
        chunks.append(cur)
    return chunks


_TASHKEEL = re.compile(r"[ً-ْٰـ]")
_AR_FOLD = str.maketrans({
    "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا",
    "ى": "ي", "ئ": "ي", "ؤ": "و", "ة": "ه", "ک": "ك", "گ": "ك",
    "پ": "ب", "چ": "ج", "ژ": "ز",
})


# Punctuation is folded to a space. Built with re.escape so no character in
# the set can ever break the pattern (an escaped-backslash class silently
# stopped matching Arabic punctuation once already).
_PUNCT = "،؛؟٪؍،«»\"'()[]{}<>.,!?…:/\\|+*=_#@&%$«»“”‘’–—"
_PUNCT_RE = re.compile("[" + re.escape(_PUNCT) + r"\s]+")


def normalize_for_match(text: str) -> str:
    """Fold a phrase for WAKE-WORD matching, not for display.

    Arabic is written with optional marks and several spellings of the same
    letter ("آلي" / "علي" / "الٰلي"), so a literal substring test would miss
    a correct wake word. Folds tashkeel, alef/ya/ta-marbuta variants and
    punctuation, keeping the text otherwise intact.
    """
    t = _TASHKEEL.sub("", text or "")
    t = t.translate(_AR_FOLD)
    return _PUNCT_RE.sub(" ", t).strip()


def contains_phrase(haystack: str, needle: str) -> bool:
    """Substring test on folded text (used by the wake-word gate)."""
    n = normalize_for_match(needle)
    return bool(n) and n in normalize_for_match(haystack)


class LanguageTracker:
    """Smoothed utterance language: majority vote over recent detections."""

    def __init__(self, window: int = 5, min_confidence: float = 0.55):
        self.window = window
        self.min_confidence = min_confidence
        self._votes: List[str] = []
        self.current: str = "ar"  # Arabic-first product default

    def update(self, lang: Optional[str], confidence: float = 1.0) -> Optional[str]:
        if lang in ("ar", "he", "en") and confidence >= self.min_confidence:
            self._votes.append(lang)
            if len(self._votes) > self.window:
                self._votes.pop(0)
            top, n = Counter(self._votes).most_common(1)[0]
            if n / len(self._votes) >= self.min_confidence:
                self.current = top
        return self.current

    def snapshot(self) -> Dict[str, object]:
        return {"current": self.current, "votes": list(self._votes)}
