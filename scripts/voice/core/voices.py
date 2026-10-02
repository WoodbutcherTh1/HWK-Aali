# -*- coding: utf-8 -*-
"""Voice library: named voice profiles backed by recorded reference wavs.

Phase 2 "voice asset studio". Phase 1 hardcoded three ref paths and only
the Arabic one existed, so Hebrew and English spoke with an Arabic accent.
This module owns the asset side honestly:

- a small JSON store (D:/hwk-data/voice/voices.json) of voice PROFILES:
  name, language, reference wav path, note, created date;
- one DEFAULT voice per language, so "which voice speaks Arabic" is a
  setting, not a hardcoded constant;
- real VALIDATION of a candidate reference clip (mono/16-bit, duration,
  level, clipping) before it is admitted — a bad clip produces a robotic
  clone, so the studio refuses it with a reason instead of accepting it;
- reference wavs are copied INTO the references dir the library manages, so
  a profile is never a dangling pointer to somebody's Desktop.

No model is loaded here: pure stdlib + numpy, so the test suite exercises
it end to end with real wav files in a tmp dir.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import time
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

VOICE_DIR = Path(os.environ.get("AALI_VOICE_DIR") or r"D:/hwk-data/voice")
STORE_PATH = VOICE_DIR / "voices.json"
REFERENCES_DIR = Path(
    os.environ.get("AALI_VOICE_REFS") or r"D:/hwk-models/voice/references"
)
LOG_FILE = Path(r"D:/hwk-data/voice_library.log")

SUPPORTED_LANGS = ("ar", "he", "en")

# XTTS clones badly from clips outside this window; both ends are a real
# quality problem (too short = no speaker embedding, too long = timbre drift).
MIN_REF_SECONDS = 4.0
MAX_REF_SECONDS = 30.0
MIN_RMS = 0.02          # below this the clip is effectively silent
MAX_CLIPPED_RATIO = 0.02  # above this the input was pushed into the wall

_BAD_NAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def _log(line: str) -> None:
    """Content-free log: names and counts, never transcript text."""
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {line}\n")
    except OSError:
        pass


def safe_name(name: str) -> str:
    """Filesystem-safe, unicode-friendly voice name (Arabic names allowed)."""
    name = (name or "").strip()
    if not name or len(name) > 60:
        raise ValueError("اسم الصوت مطلوب (1-60 حرفاً)")
    if _BAD_NAME.search(name) or name.startswith("."):
        raise ValueError("اسم الصوت يحتوي محارف غير مسموحة")
    return name


# --------------------------------------------------------------------------
# reference wav validation
# --------------------------------------------------------------------------

def validate_reference_wav(
    path: str | os.PathLike,
    min_s: float = MIN_REF_SECONDS,
    max_s: float = MAX_REF_SECONDS,
) -> Dict[str, Any]:
    """Inspect a candidate reference clip. Never raises — returns a verdict.

    ok=True means XTTS has enough clean signal to clone from; every failure
    names the actual reason so the studio can teach instead of guess.
    """
    p = Path(path)
    out: Dict[str, Any] = {
        "ok": False, "path": str(p), "error": "",
        "sr": None, "channels": None, "sampwidth": None,
        "seconds": 0.0, "rms": 0.0, "peak": 0.0, "clipped": 0.0, "reasons": [],
    }
    if not p.exists():
        out["reasons"].append("الملف غير موجود")
        out["error"] = "missing file"
        return out
    try:
        with wave.open(str(p), "rb") as wf:
            channels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            sr = wf.getframerate()
            frames = wf.getnframes()
            raw = wf.readframes(frames)
    except (wave.Error, OSError) as exc:
        out["reasons"].append(f"ليس ملف wav صالح ({exc})")
        out["error"] = "unreadable wav"
        return out

    if sampwidth != 2:
        out["reasons"].append("يجب أن يكون 16-bit (PCM_16)")
    if channels != 1:
        out["reasons"].append("يجب أن يكون قناة واحدة (mono)")
    if sr < 16000:
        out["reasons"].append("معدل العينة منخفض (16000 Hz على الأقل)")

    samples = np.frombuffer(raw, dtype="<i2") if sampwidth == 2 else np.zeros(0, dtype="<i2")
    if samples.size == 0:
        out["reasons"].append("الملف فارغ")
    else:
        if channels > 1:  # analyse the first channel only for the verdict
            samples = samples[::channels]
        x = samples.astype(np.float64) / 32768.0
        rms = float(np.sqrt(np.mean(x * x)))
        peak = float(np.max(np.abs(x)))
        clipped = float(np.mean(np.abs(x) >= 0.999))
        seconds = samples.size / float(sr or 1)
        out.update({"sr": sr, "channels": channels, "sampwidth": sampwidth,
                    "seconds": round(seconds, 2), "rms": round(rms, 4),
                    "peak": round(peak, 4), "clipped": round(clipped, 4)})
        if seconds < min_s:
            out["reasons"].append(f"قصير جداً ({seconds:.1f}s، المطلوب {min_s:.0f}s على الأقل)")
        if seconds > max_s:
            out["reasons"].append(f"طويل جداً ({seconds:.1f}s، الحد {max_s:.0f}s)")
        if rms < MIN_RMS:
            out["reasons"].append("الصوت منخفض جداً (سجّل أقرب للميكروفون)")
        if clipped > MAX_CLIPPED_RATIO:
            out["reasons"].append("الصوت مشوَّه (clipped) — خفّض مستوى التسجيل")

    out["reasons"] = [r for r in out["reasons"] if r]
    out["ok"] = not out["reasons"]
    out["error"] = "; ".join(out["reasons"])
    return out


# --------------------------------------------------------------------------
# store
# --------------------------------------------------------------------------

def load_store(path: Optional[str | os.PathLike] = None) -> Dict[str, Any]:
    """Read the store; an unreadable/absent file is an EMPTY store, never a crash.

    The path is resolved at CALL time, never bound as a default — a module
    constant bound at import would silently ignore AALI_VOICE_DIR and any
    redirection made after import.
    """
    path = path or STORE_PATH
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {"version": 1, "voices": {}, "defaults": {}}
    if not isinstance(data, dict):
        return {"version": 1, "voices": {}, "defaults": {}}
    data.setdefault("voices", {})
    data.setdefault("defaults", {})
    data.setdefault("version", 1)
    return data


def save_store(store: Dict[str, Any], path: Optional[str | os.PathLike] = None) -> None:
    p = Path(path or STORE_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(store, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, p)  # atomic: a half-written library is worse than none


def list_voices(store: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Profiles + whether their reference clip actually exists on disk."""
    st = store if store is not None else load_store()
    defaults = st.get("defaults", {})
    out: List[Dict[str, Any]] = []
    for name, v in sorted(st.get("voices", {}).items()):
        ref = v.get("ref") or ""
        out.append({
            "name": name,
            "lang": v.get("lang", "ar"),
            "ref": ref,
            "note": v.get("note", ""),
            "created": v.get("created", ""),
            "has_ref": bool(ref) and Path(ref).exists(),
            "is_default": defaults.get(v.get("lang", "ar")) == name,
        })
    return out


def get_voice(name: str, store: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    st = store if store is not None else load_store()
    v = st.get("voices", {}).get(name)
    return dict(v) if isinstance(v, dict) else None


def add_voice(
    name: str,
    lang: str,
    ref: str | os.PathLike,
    note: str = "",
    store_path: Optional[str | os.PathLike] = None,
    refs_dir: Optional[str | os.PathLike] = None,
    validate: bool = True,
) -> Dict[str, Any]:
    """Validate a reference clip and register it as a named voice.

    The clip is COPIED into refs_dir/<name>.wav so the profile owns its asset.
    An invalid clip is refused with the reason — no silent acceptance of a
    clip that would clone into a robotic voice.
    """
    name = safe_name(name)
    if lang not in SUPPORTED_LANGS:
        return {"ok": False, "error": f"لغة غير مدعومة: {lang}"}
    src = Path(ref)
    verdict = validate_reference_wav(src)
    if validate and not verdict["ok"]:
        return {"ok": False, "error": verdict["error"], "check": verdict}

    refs = Path(refs_dir or REFERENCES_DIR)
    refs.mkdir(parents=True, exist_ok=True)
    dest = refs / f"{name}.wav"
    if src.resolve() != dest.resolve():
        shutil.copyfile(src, dest)

    st = load_store(store_path)
    st["voices"][name] = {
        "lang": lang,
        "ref": str(dest),
        "note": note[:200],
        "created": time.strftime("%Y-%m-%d"),
    }
    # first voice for a language becomes its default — a library that
    # answers "who speaks Hebrew?" with nothing is not usable.
    if not st["defaults"].get(lang):
        st["defaults"][lang] = name
    save_store(st, store_path)
    _log(f"voice added name={name} lang={lang} chars_ok={verdict['ok']} secs={verdict.get('seconds')}")
    return {"ok": True, "name": name, "lang": lang, "ref": str(dest), "check": verdict}


def delete_voice(
    name: str,
    store_path: Optional[str | os.PathLike] = None,
    keep_file: bool = False,
) -> Dict[str, Any]:
    st = load_store(store_path)
    v = st.get("voices", {}).pop(name, None)
    if v is None:
        return {"ok": False, "error": f"لا يوجد صوت بهذا الاسم: {name}"}
    for lang, dname in list(st.get("defaults", {}).items()):
        if dname == name:  # never leave a language pointing at a deleted voice
            replacement = next(
                (n for n, vv in st["voices"].items() if vv.get("lang") == lang), None
            )
            if replacement:
                st["defaults"][lang] = replacement
            else:
                st["defaults"].pop(lang, None)
    if not keep_file:
        try:
            ref = v.get("ref")
            if ref and Path(ref).exists():
                Path(ref).unlink()
        except OSError:
            pass
    save_store(st, store_path)
    _log(f"voice deleted name={name} file_kept={bool(keep_file)}")
    return {"ok": True, "name": name}


def set_default(lang: str, name: str, store_path: Optional[str | os.PathLike] = None) -> Dict[str, Any]:
    st = load_store(store_path)
    v = st.get("voices", {}).get(name)
    if v is None:
        return {"ok": False, "error": f"لا يوجد صوت بهذا الاسم: {name}"}
    if v.get("lang") != lang:
        return {"ok": False, "error": f"الصوت {name} لغته {v.get('lang')} وليست {lang}"}
    st["defaults"][lang] = name
    save_store(st, store_path)
    _log(f"voice default lang={lang} name={name}")
    return {"ok": True, "lang": lang, "name": name}


def default_voice(
    lang: str,
    store: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """The voice that speaks `lang`: the profile named default, else the
    first profile in that language with a real ref file, else None.

    Falls through (like VoiceTTS._ref_for) so a missing default never makes
    a language mute — and never invents a voice that does not exist.
    """
    st = store if store is not None else load_store()
    voices = st.get("voices", {})
    default_name = st.get("defaults", {}).get(lang)
    candidates = []
    if default_name and default_name in voices:
        candidates.append(voices[default_name])
    candidates += [v for n, v in sorted(voices.items())
                   if n != default_name and v.get("lang") == lang]
    for v in candidates:
        ref = v.get("ref")
        if ref and Path(ref).exists():
            return {"name": next(n for n, vv in voices.items() if vv is v), **v}
    return None


def piper_voices() -> List[Dict[str, Any]]:
    """Installed Piper voices (the engine that carries Hebrew — XTTS has no he).

    Returns [] when file_agent is not importable; never raises.
    """
    ok, voices = _piper_list()
    return voices if ok else []


def _piper_list() -> tuple[bool, List[Dict[str, Any]]]:
    try:
        from file_agent import tts as piper_tts

        return True, [
            {"id": v.get("id"), "language": v.get("language", ""),
             "quality": v.get("quality", "")}
            for v in piper_tts.list_voices()
        ]
    except Exception:
        return False, []


def library_report(store_path: Optional[str | os.PathLike] = None) -> Dict[str, Any]:
    """What the studio UI needs: per-language availability, honestly."""
    st = load_store(store_path)
    voices = list_voices(st)
    piper_ok, piper = _piper_list()
    langs = {}
    for lang in SUPPORTED_LANGS:
        d = default_voice(lang, st)
        has_piper = any(
            str(p.get("language") or "").lower().startswith(lang) for p in piper
        )
        langs[lang] = {
            "available": bool(d) or has_piper,
            "voice": d.get("name") if d else "",
            "recorded": any(v["lang"] == lang for v in voices),
            "count": sum(1 for v in voices if v["lang"] == lang),
            # XTTS cannot speak Hebrew; a Piper he_IL voice carries it
            "engine": ("xtts" if d else ("piper" if has_piper else None)),
        }
    return {"voices": voices, "languages": langs, "piper": piper,
            "piper_stack": piper_ok,
            "store": str(store_path or STORE_PATH)}