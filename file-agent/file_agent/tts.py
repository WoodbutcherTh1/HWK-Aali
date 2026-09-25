"""Piper TTS for Aali (Wave 1 #3, 2026-09-25; full contract 3.1-3.11).

Design
------
- Piper runs in an ISOLATED venv (D:/hwk-tools/vision-venv) and is called
  as a SUBPROCESS from here — the training/server venv gains zero new
  deps (stop-point rule: no new deps in .venv / training venv).
- Voices live in D:/hwk-models/piper/*.onnx (+ sidecar .onnx.json).
  Arabic-first: the first Arabic voice found is the default. Quality and
  language come from the sidecar (piper 1.8 format).
- Formats: WAV natively; MP3 via the machine's ffmpeg (best-effort —
  falls back to WAV with an honest note when ffmpeg is absent).
- Output cached by (voice, speed, format, sha256(text)).
- Role-aware limits are applied at the ENDPOINT (guests 500 chars,
  users/admins/devs/owner 2000); this module enforces the hard cap and
  validation only.
- Content-free logging: no text, no query strings.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import time
import wave
from pathlib import Path
from typing import Any

MODELS_DIR = Path("D:/hwk-models/piper")
CACHE_DIR = Path("D:/hwk-data/tts_cache")
LOG_FILE = Path("D:/hwk-data/tts.log")

# The isolated venv's interpreter (never the server's own).
_CANDIDATE_PYTHONS = (
    Path("D:/hwk-tools/vision-venv/Scripts/python.exe"),
    Path("D:/hwk-tools/vision-venv/bin/python"),
)

MAX_TEXT_CHARS = 2000       # hard cap; endpoint applies per-role limits
GUEST_TEXT_CHARS = 500      # applied by app.py for guest role
MIN_SPEED, MAX_SPEED = 0.5, 2.0
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_CHILD_SCRIPT = r'''
import json, sys, wave
from piper import PiperVoice, SynthesisConfig

# stdin carries ONE json object: {"text": ...} (args would need escaping).
req = json.loads(sys.stdin.buffer.read().decode("utf-8"))
model, out_path, text = sys.argv[1], sys.argv[2], req["text"]
length_scale = float(sys.argv[3]) if len(sys.argv) > 3 else 1.0
voice = PiperVoice.load(model)
syn_config = SynthesisConfig(length_scale=length_scale)
rate = getattr(voice.config, "sample_rate", 22050) or 22050

# Pre-set the WAV format OURSELVES and disable piper's per-chunk format
# setting: when espeak-ng phonemizes a text to NOTHING, piper yields zero
# chunks, set_wav_format never runs, and the with-block close() blows up
# with "# channels not specified" (2026-09-25 13:04 incident). With the
# format pre-set, a no-chunk run closes cleanly as 0 frames and the PARENT
# reports it honestly instead of a cryptic wave.Error.
wf = wave.open(out_path, "wb")
wf.setnchannels(1)
wf.setsampwidth(2)
wf.setframerate(int(rate))
try:
    voice.synthesize_wav(text, wf, syn_config=syn_config,
                         set_wav_format=False)
finally:
    wf.close()
print("OK")
'''


def _python() -> Path | None:
    for candidate in _CANDIDATE_PYTHONS:
        if candidate.exists():
            return candidate
    return None


def _log(line: str) -> None:
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {line}\n")
    except OSError:
        pass


def available() -> bool:
    """Piper + at least one voice present? (Cheap check for /api/voice/list.)"""
    return _python() is not None and bool(list_voices())


def list_voices() -> list[dict[str, Any]]:
    """All installed voices; Arabic first, then alphabetical.

    Each entry: {id, name, language, gender?, quality, arabic}.
    Quality/gender come from the piper sidecar when present."""
    voices: list[dict[str, Any]] = []
    if not MODELS_DIR.exists():
        return voices
    for onnx in sorted(MODELS_DIR.glob("*.onnx")):
        sidecar = Path(str(onnx) + ".json")
        if not sidecar.exists():
            continue
        name = onnx.stem
        lang = ""
        quality = ""
        gender = ""
        try:
            meta = json.loads(sidecar.read_text(encoding="utf-8"))
            lang = meta.get("language", {}).get("code", "")
            quality = meta.get("audio", {}).get("quality", "")
            # piper training metadata sometimes carries speaker gender
            gender = str(meta.get("speaker_metadata", {}).get("gender", "")
                         or "").lower() or None  # type: ignore[assignment]
        except (OSError, ValueError, AttributeError):
            pass
        voices.append({
            "id": name,
            "name": name,
            "file": str(onnx),
            "language": lang,
            "gender": gender,
            "quality": quality,
            "arabic": name.lower().startswith("ar") or lang.startswith("ar"),
        })
    voices.sort(key=lambda v: (not v["arabic"], v["id"]))
    return voices


def default_voice() -> str | None:
    vs = list_voices()
    return vs[0]["id"] if vs else None


def _ffmpeg() -> Path | None:
    found = shutil.which("ffmpeg")
    return Path(found) if found else None


def _wav_to_mp3(wav_path: Path) -> Path | None:
    """Best-effort WAV -> MP3 via ffmpeg; None when unavailable/failed."""
    ff = _ffmpeg()
    if ff is None:
        return None
    mp3_path = wav_path.with_suffix(".mp3")
    try:
        proc = subprocess.run(
            [str(ff), "-y", "-loglevel", "error", "-i", str(wav_path),
             "-codec:a", "libmp3lame", "-qscale:a", "4", str(mp3_path)],
            capture_output=True, timeout=60,
            creationflags=CREATE_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode == 0 and mp3_path.exists() and mp3_path.stat().st_size > 1000:
        return mp3_path
    return None


def synthesize(text: str, voice_id: str | None = None,
               speed: float = 1.0, fmt: str = "wav") -> dict[str, Any]:
    """Synthesize text -> cached audio file.

    Returns {ok, audio?, format?, cached?, voice?, duration_hint?, error?}."""
    text = (text or "").strip()
    if not text:
        return {"ok": False, "error": "empty text"}
    if len(text) > MAX_TEXT_CHARS:
        return {"ok": False, "error": f"text too long (max {MAX_TEXT_CHARS} chars)"}

    py = _python()
    if py is None:
        return {"ok": False, "error": "piper venv not installed"}

    voices = {v["id"]: v for v in list_voices()}
    voice_id = voice_id or (default_voice() or "")
    model = voices.get(voice_id, {}).get("file")
    if not model or not Path(model).exists():
        return {"ok": False, "error": f"unknown voice: {voice_id}"}

    try:
        speed = min(max(float(speed), MIN_SPEED), MAX_SPEED)
    except (TypeError, ValueError):
        speed = 1.0
    fmt = (fmt or "wav").lower()
    if fmt not in ("wav", "mp3"):
        fmt = "wav"
    # Piper's length_scale is "slower when larger": speed 1.5 -> 1/1.5.
    length_scale = 1.0 / speed

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(
        f"{voice_id}|{speed:.2f}|".encode("utf-8") + text.encode("utf-8")
    ).hexdigest()[:32]
    wav_path = CACHE_DIR / f"{digest}.wav"
    final_path = wav_path.with_suffix(f".{fmt}")

    def _finish(cached: bool, path: Path) -> dict[str, Any]:
        _log(f"{'hit' if cached else 'synth'} voice={voice_id} "
             f"chars={len(text)} fmt={fmt} bytes={path.stat().st_size}")
        return {"ok": True, "audio": str(path), "format": fmt,
                "cached": cached, "voice": voice_id}

    if final_path.exists() and final_path.stat().st_size > 1000:
        return _finish(cached=True, path=final_path)

    tmp_path = wav_path.with_suffix(".tmp.wav")
    script_path = CACHE_DIR / "_child.py"
    try:
        script_path.write_text(_CHILD_SCRIPT, encoding="utf-8")
        payload = json.dumps({"text": text})
        proc = subprocess.run(
            [str(py), str(script_path), model, str(tmp_path), f"{length_scale:.3f}"],
            input=payload.encode("utf-8"),
            capture_output=True, timeout=120,
            creationflags=CREATE_NO_WINDOW)
    except subprocess.TimeoutExpired:
        _log(f"timeout voice={voice_id} chars={len(text)}")
        tmp_path.unlink(missing_ok=True)
        return {"ok": False, "error": "synthesis timed out"}
    except OSError as exc:
        _log(f"spawn error={type(exc).__name__}")
        return {"ok": False, "error": "piper subprocess failed"}

    if proc.returncode != 0 or not tmp_path.exists() \
            or tmp_path.stat().st_size < 1000:
        tmp_path.unlink(missing_ok=True)
        # 12:11 smoke_server lesson: never let the real error sit in a
        # pipe nobody reads — keep the child's stderr tail in the log.
        err_tail = (proc.stderr or b"")[-300:].decode("utf-8", "replace")
        _log(f"fail voice={voice_id} rc={proc.returncode} err={err_tail!r}")
        return {"ok": False, "error": "synthesis failed"}

    # Validate the WAV header before caching/serving it (never trust a
    # partially-written wave file).
    try:
        with wave.open(str(tmp_path), "rb") as wf:
            if wf.getnframes() <= 0:
                raise wave.Error("empty")
    except (wave.Error, OSError):
        tmp_path.unlink(missing_ok=True)
        _log(f"invalid wav voice={voice_id}")
        return {"ok": False, "error": "invalid audio produced"}

    if fmt == "mp3":
        mp3 = _wav_to_mp3(tmp_path)
        tmp_path.replace(wav_path)  # keep the wav as the mp3's source cache
        if mp3 is None:
            _log(f"mp3-unavailable voice={voice_id} -> wav fallback")
            result = _finish(cached=False, path=wav_path)
            result["format"] = "wav"
            result["note"] = "ffmpeg unavailable — served WAV instead"
            return result
        mp3_final = wav_path.with_suffix(".mp3")
        if mp3 != mp3_final:
            mp3.replace(mp3_final)
        return _finish(cached=False, path=mp3_final)

    tmp_path.replace(wav_path)
    return _finish(cached=False, path=wav_path)


if __name__ == "__main__":
    out = synthesize(sys.argv[1] if len(sys.argv) > 1 else "اختبار")
    print(json.dumps({k: v for k, v in out.items() if k != "audio"}))
