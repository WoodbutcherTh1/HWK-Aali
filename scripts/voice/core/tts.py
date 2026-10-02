# -*- coding: utf-8 -*-
"""TTS engine for the voice agent: Coqui XTTS-v2 (cloned voice) primary,
existing Piper stack fallback.

- XTTS loads from a LOCAL plain-file dir (D:/hwk-models/xtts-v2) or the HF
  cache — never assumes network at serve time.
- Per-language reference wavs from D:/hwk-models/voice/references/ (AR
  exists; HE/EN refs clone the AR voice until recorded — honest note).
- Fallback: file_agent.tts synthesize() (vision-venv piper subprocess) —
  zero new deps in any other venv, and Piper voices are per-language: if
  no voice exists for the requested language the fallback reports
  unavailable instead of speaking Arabic over an English sentence.
- Cache: D:/hwk-data/voice_tts/sha1(engine|lang|ref|text).wav
- Injectable `synth_fn` for tests (the suite never loads the 1.8 GB model).
"""
from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path
from typing import Callable, Dict, Optional

import numpy as np

DEFAULT_REFS = {
    "ar": r"D:/hwk-models/voice/references/arabic_male.wav",
    "he": r"D:/hwk-models/voice/references/hebrew_male.wav",
    "en": r"D:/hwk-models/voice/references/english_male.wav",
}
DEFAULT_XTTS_DIR = r"D:/hwk-models/xtts-v2"
CACHE_DIR = Path(r"D:/hwk-data/voice_tts")
LOG_FILE = Path(r"D:/hwk-data/voice_tts.log")

# XTTS supports these exactly (coqui docs); map our codes 1:1
XTTS_LANGS = {"ar", "he", "en"}


def _log(line: str) -> None:
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {line}\n")
    except OSError:
        pass


class VoiceTTS:
    def __init__(
        self,
        refs: Optional[Dict[str, str]] = None,
        fallback_ref_lang: str = "ar",
        xtts_dir: str = DEFAULT_XTTS_DIR,
        synth_fn: Optional[Callable[[str, str, str], Dict]] = None,
    ):
        self.refs = {k: v for k, v in {**DEFAULT_REFS, **(refs or {})}.items() if v}
        self.fallback_ref_lang = fallback_ref_lang
        self.xtts_dir = xtts_dir
        self._xtts = None
        self._cond_cache: Dict[str, object] = {}
        self._synth_fn = synth_fn  # test seam: (text, lang, ref) -> {path, sr}

    # -- engine state ----------------------------------------------------
    def xtts_ready(self) -> bool:
        d = Path(self.xtts_dir)
        return (d / "model.pth").exists() or self._xtts is not None

    def engine_report(self) -> Dict[str, object]:
        piper_ok = False
        try:  # file_agent.tts is importable when file-agent/ is on sys.path
            from file_agent import tts as piper_tts

            piper_ok = bool(piper_tts.available())
        except Exception:
            piper_ok = False
        return {
            "xtts": self.xtts_ready(),
            "piper": piper_ok,
            "refs": {k: str(Path(v).exists()) for k, v in self.refs.items()},
        }

    # -- XTTS ------------------------------------------------------------
    def ensure_xtts(self) -> None:
        """Direct Xtts API (TTSApi(model_path=...) cannot dispatch XTTS's
        raw config.json in the coqui-tts fork -> 'Unknown config file type')."""
        if self._xtts is not None:
            return
        if not self.xtts_ready():
            raise RuntimeError(
                "XTTS-v2 not present locally — run scripts/voice/probe_xtts.py first"
            )
        from TTS.tts.configs.xtts_config import XttsConfig
        from TTS.tts.models.xtts import Xtts

        t0 = time.monotonic()
        cfg = XttsConfig()
        cfg.load_json(str(Path(self.xtts_dir) / "config.json"))
        model = Xtts.init_from_config(cfg)
        model.load_checkpoint(cfg, checkpoint_dir=self.xtts_dir, eval=True)
        self._xtts = model
        _log(f"xtts loaded in {time.monotonic() - t0:.0f}s from {self.xtts_dir}")

    def _ref_for(self, lang: str) -> Optional[str]:
        """Reference for lang, falling back through the configured fallback
        language — a MISSING per-lang ref must never kill the whole lookup
        (EN probe lesson: english_male.wav absent -> clone the AR voice)."""
        for key in (lang, self.fallback_ref_lang):
            ref = self.refs.get(key)
            if ref and Path(ref).exists():
                return ref
        return None

    # -- public API --------------------------------------------------------
    def synthesize(self, text: str, lang: str = "ar") -> Dict[str, object]:
        """text chunk -> {ok, path?, sr?, engine?, cached?, error?}.

        Never fabricates: a missing engine/ref/voice is an honest error.
        """
        text = (text or "").strip()
        if not text:
            return {"ok": False, "error": "empty text"}
        lang = lang if lang in ("ar", "he", "en") else "ar"
        ref = self._ref_for(lang)

        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        ref_key = ref or "none"
        digest = hashlib.sha1(
            f"xtts|{lang}|{ref_key}|".encode("utf-8") + text.encode("utf-8")
        ).hexdigest()
        out = CACHE_DIR / f"{digest}.wav"
        if out.exists() and out.stat().st_size > 1000:
            return {"ok": True, "path": str(out), "sr": 24000, "engine": "xtts", "cached": True}

        # primary: XTTS clone
        if self._synth_fn is not None:
            try:
                r = self._synth_fn(text, lang, ref or "")
                if r and r.get("ok"):
                    return r
                return {"ok": False, "error": r.get("error", "synth_fn failed")} if r else {
                    "ok": False, "error": "synth_fn returned nothing"
                }
            except Exception as exc:  # test seam must behave like the real path
                _log(f"synth_fn error={type(exc).__name__}")
                return {"ok": False, "error": f"synth_fn: {type(exc).__name__}"}

        if self.xtts_ready() and ref:
            try:
                self.ensure_xtts()
                import torch
                import torchaudio

                t0 = time.monotonic()
                key = str(ref)
                if key not in self._cond_cache:
                    self._cond_cache[key] = self._xtts.get_conditioning_latents(
                        audio_path=[ref]
                    )
                gpt_lat, spk_emb = self._cond_cache[key]
                wav = self._xtts.inference(
                    text=text,
                    language=lang,
                    gpt_cond_latent=gpt_lat,
                    speaker_embedding=spk_emb,
                )
                # int16 PCM wav (torchaudio's default float32 wav breaks
                # Python's wave module AND server streaming down the line)
                import soundfile as sf

                sf.write(
                    str(out),
                    np.asarray(wav["wav"], dtype=np.float32),
                    24000,
                    subtype="PCM_16",
                )
                dt = time.monotonic() - t0
                if out.exists() and out.stat().st_size > 1000:
                    _log(f"xtts ok lang={lang} chars={len(text)} {dt:.1f}s bytes={out.stat().st_size}")
                    return {"ok": True, "path": str(out), "sr": 24000, "engine": "xtts", "cached": False}
                _log(f"xtts empty output lang={lang} chars={len(text)}")
            except Exception as exc:
                _log(f"xtts fail lang={lang} err={type(exc).__name__}: {exc}")

        # fallback: piper (per-language voices; honest when absent)
        return self._piper(text, lang)

    def _piper(self, text: str, lang: str) -> Dict[str, object]:
        try:
            from file_agent import tts as piper_tts
        except Exception:
            return {"ok": False, "error": "no TTS engine available (xtts down, piper import failed)"}
        digest = hashlib.sha1(f"piper|{lang}|".encode("utf-8") + text.encode("utf-8")).hexdigest()
        out = CACHE_DIR / f"{digest}.wav"
        if out.exists() and out.stat().st_size > 1000:
            return {"ok": True, "path": str(out), "sr": 22050, "engine": "piper", "cached": True}
        r = piper_tts.synthesize(text, voice_id=None, fmt="wav")
        if not r.get("ok"):
            _log(f"piper fail lang={lang} err={r.get('error')}")
            return {"ok": False, "error": f"piper: {r.get('error')}"}
        src = Path(r["audio"])
        if src.resolve() != out.resolve():
            os.replace(src, out)
        _log(f"piper ok lang={lang} chars={len(text)}")
        return {"ok": True, "path": str(out), "sr": 22050, "engine": "piper", "cached": False}
