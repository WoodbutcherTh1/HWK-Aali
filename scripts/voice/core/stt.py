# -*- coding: utf-8 -*-
"""faster-whisper STT (AR/HE/EN) + whisper language detection.

The transcriber object is injectable (any object with .transcribe) so the
segment-level logic (language gating, segment merge, LanguageTracker feed)
is testable without the model. :class:`WhisperSTT` wires the real model.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

# faster-whisper lang code -> our canonical code
_LANG_ALIASES = {"ar": "ar", "he": "he", "en": "en", "arb": "ar", "heb": "he"}


def _canon(lang: Optional[str]) -> Optional[str]:
    if not lang:
        return None
    return _LANG_ALIASES.get(str(lang).lower()[:3])


class WhisperSTT:
    """Whisper STT with per-utterance language handling (CPU, int8)."""

    def __init__(
        self,
        model_size: str = "large-v3-turbo",
        device: str = "cpu",
        compute_type: str = "int8",
        tracker: Any = None,
        language_window: int = 5,
        model_dir: Optional[str] = None,
    ):
        # model_dir wins: a local plain-file dir avoids HF cache symlinks
        # (WinError 1314) and the wrong default repo for turbo variants.
        self._model_ref = model_dir if model_dir else model_size
        self._device = device
        self._compute_type = compute_type
        self._model = None
        self.tracker = tracker
        # Last-resort script check on the TRANSCRIPT when whisper's top-1
        # language disagrees with the script of what was actually said.
        from voice.core.language_detect import LanguageTracker

        self.tracker = tracker or LanguageTracker(window=language_window)

    # -- model lifecycle -------------------------------------------------
    def ensure_model(self) -> None:
        if self._model is None:
            from faster_whisper import WhisperModel

            self._model = WhisperModel(
                self._model_ref,
                device=self._device,
                compute_type=self._compute_type,
            )

    # -- core API --------------------------------------------------------
    def transcribe(
        self,
        pcm16: "np.ndarray",
        sample_rate: int = 16000,
    ) -> Dict[str, Any]:
        """pcm16 (int16 mono) -> {text, lang, confidence, ms}.

        Language resolution order:
          1. whisper's detected language for THIS utterance;
          2. script check on the transcript (protects against a whisper
             mis-detection where the text is clearly another script);
          3. tracker's smoothed current language (code-switch smoothing).
        """
        import numpy as np

        if pcm16 is None or getattr(pcm16, "size", 0) == 0:
            return {"text": "", "lang": self.tracker.current, "confidence": 0.0, "ms": 0}
        self.ensure_model()
        audio = np.asarray(pcm16, dtype=np.float32) / 32768.0
        t0 = time_now()
        gen, info = self._model.transcribe(
            audio,
            beam_size=1,
            vad_filter=False,  # our own segmenter already did VAD
            condition_on_previous_text=False,
        )
        text = " ".join(seg.text.strip() for seg in gen if seg.text and seg.text.strip()).strip()
        dt_ms = int((time_now() - t0) * 1000)

        lang = _canon(getattr(info, "language", None))
        conf = float(getattr(info, "language_probability", 0.0))
        script = detect_script_lang(text)
        if script and (lang != script):
            # Hebrew pointed text can carry Arabic-script hesitation marks;
            # require strong script evidence to override whisper.
            lang = script if conf < 0.90 else lang
        self.tracker.update(lang, conf)
        return {
            "text": text,
            "lang": self.tracker.current,
            "confidence": conf,
            "ms": dt_ms,
            "whisper_lang": _canon(getattr(info, "language", None)),
        }

    # alias kept for pipeline readability
    def detect_lang(self, text: str) -> Optional[str]:
        return detect_script_lang(text)


def time_now() -> float:
    import time

    return time.monotonic()


def detect_script_lang(text: str):
    from voice.core.language_detect import detect_script_lang as _f

    return _f(text)


import numpy as np  # noqa: E402  (kept at bottom to keep the docstring first)
