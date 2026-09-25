"""Piper TTS for Aali (Wave 1 #3, 2026-09-25).

Design
------
- Piper runs in an ISOLATED venv (D:/hwk-tools/vision-venv) and is called
  as a SUBPROCESS from here — the training/server venv gains zero new
  deps (stop-point rule: no new deps in .venv / training venv).
- Voices live in D:/hwk-data/voices/*.onnx (+ sidecar .onnx.json).
  Arabic-first: the first Arabic voice found is the default.
- Output WAV is cached by (voice, speed, sha256(text)) — identical
  requests return the cached file instantly.
- Guest policy: the endpoint gates this behind the `tts` permission;
  guests never reach this module. Content-free logging: no text, no
  filenames beyond the sid-ish cache hash.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
import wave
from pathlib import Path

VOICES_DIR = Path("D:/hwk-data/voices")
CACHE_DIR = Path("D:/hwk-data/voices/cache")
LOG_FILE = Path("D:/hwk-data/tts.log")

# The isolated venv's interpreter (never the server's own).
_CANDIDATE_PYTHONS = (
    Path("D:/hwk-tools/vision-venv/Scripts/python.exe"),
    Path("D:/hwk-tools/vision-venv/bin/python"),
)

MAX_TEXT_CHARS = 1000
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
with wave.open(out_path, "wb") as wf:
    voice.synthesize_wav(text, wf, syn_config=syn_config)
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
    """Piper + at least one voice present? (Cheap check for /api/voice/info.)"""
    return _python() is not None and bool(list_voices())


def list_voices() -> list[dict]:
    """All installed voices; Arabic first, then alphabetical."""
    voices: list[dict] = []
    if not VOICES_DIR.exists():
        return voices
    for onnx in sorted(VOICES_DIR.glob("*.onnx")):
        sidecar = Path(str(onnx) + ".json")
        if not sidecar.exists():
            continue
        name = onnx.stem
        lang = ""
        gender = ""
        try:
            meta = json.loads(sidecar.read_text(encoding="utf-8"))
            lang = meta.get("language", {}).get("code", "")
            gender = meta.get("inference", {}).get("tts", "") or ""
        except (OSError, ValueError, AttributeError):
            pass
        voices.append({
            "id": name,
            "file": str(onnx),
            "language": lang,
            "arabic": name.lower().startswith("ar") or lang.startswith("ar"),
        })
    voices.sort(key=lambda v: (not v["arabic"], v["id"]))
    return voices


def default_voice() -> str | None:
    vs = list_voices()
    return vs[0]["id"] if vs else None


def synthesize(text: str, voice_id: str | None = None,
               speed: float = 1.0) -> dict:
    """Synthesize text -> WAV path. Returns {ok, wav?, cached?, voice?, error?}."""
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
        speed = min(max(float(speed), 0.5), 2.0)
    except (TypeError, ValueError):
        speed = 1.0
    # Piper's length_scale is "slower when larger": speed 1.5 -> 1/1.5.
    length_scale = 1.0 / speed

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(
        f"{voice_id}|{speed:.2f}|".encode("utf-8") + text.encode("utf-8")
    ).hexdigest()[:32]
    wav_path = CACHE_DIR / f"{digest}.wav"
    if wav_path.exists() and wav_path.stat().st_size > 44:
        _log(f"hit voice={voice_id} chars={len(text)}")
        return {"ok": True, "wav": str(wav_path), "cached": True,
                "voice": voice_id}

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
        _log(f"fail voice={voice_id} rc={proc.returncode}")
        return {"ok": False, "error": "synthesis failed"}

    # Validate the WAV header before serving it (piper CLI taught us to
    # never trust a partially-written wave file).
    try:
        with wave.open(str(tmp_path), "rb") as wf:
            if wf.getnframes() <= 0:
                raise wave.Error("empty")
    except (wave.Error, OSError):
        tmp_path.unlink(missing_ok=True)
        _log(f"invalid wav voice={voice_id}")
        return {"ok": False, "error": "invalid audio produced"}

    tmp_path.replace(wav_path)
    _log(f"synth voice={voice_id} chars={len(text)} bytes={wav_path.stat().st_size}")
    return {"ok": True, "wav": str(wav_path), "cached": False, "voice": voice_id}


if __name__ == "__main__":
    out = synthesize(sys.argv[1] if len(sys.argv) > 1 else "اختبار")
    print(json.dumps({k: v for k, v in out.items() if k != "wav"}))
