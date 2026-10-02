# -*- coding: utf-8 -*-
"""Silero VAD wrapper + streaming speech segmenter (CPU).

The segmenter is a pure state machine driven by an injectable probability
function, so tests exercise the full open/close logic without the model.
:class:`SileroVAD` wires in the real Silero probability function.
"""
from __future__ import annotations

from typing import Callable, List, Optional

import numpy as np

IN_SR = 16000
FRAME_SAMPLES = 512  # 32 ms @ 16 kHz — Silero's native frame
SR_MS = 1000.0 / IN_SR


class SpeechSegmenter:
    """Streaming VAD state machine: 512-sample frames -> speech segments.

    Yields segments as dict(sample_rate=16000, pcm=int16 numpy array,
    ms=int). Pre-roll keeps the utterance onset. Injectable prob_fn makes
    the state machine fully testable without the model.
    """

    def __init__(
        self,
        prob_fn: Optional[Callable[[np.ndarray], float]] = None,
        threshold: float = 0.5,
        pre_roll_ms: float = 380,
        silence_ms_to_close: float = 700,
        min_speech_ms: float = 200,
        frame_samples: int = FRAME_SAMPLES,
    ):
        self.prob_fn = prob_fn
        self.threshold = float(threshold)
        self.pre_roll_ms = float(pre_roll_ms)
        self.silence_ms_to_close = float(silence_ms_to_close)
        self.min_speech_ms = float(min_speech_ms)
        self.frame_samples = int(frame_samples)
        self._buf: List[np.ndarray] = []
        self._speaking = False
        self._speech_ms = 0.0
        self._sil_ms = 0.0
        self._in_speech_ms = 0.0  # total hot frames since open
        self.last_prob = 0.0      # probability of the most recent frame
        self._hot_in_feed = 0  # hot frames in the LAST feed() call

    @property
    def speaking(self) -> bool:
        return self._speaking

    @property
    def hot_frames(self) -> int:
        """How many 32ms frames in the LAST feed() were speech.

        Per-frame evidence the barge-in gate needs: one WS message carries
        many frames, so counting messages instead of frames would treat a
        single noisy packet as sustained speech.
        """
        return self._hot_in_feed

    def feed(self, pcm16: np.ndarray) -> List[dict]:
        """Feed 16k mono int16 PCM; return any COMPLETED segments."""
        out: List[dict] = []
        self._hot_in_feed = 0
        if pcm16.ndim != 1:
            pcm16 = pcm16.reshape(-1)
        n = pcm16.shape[0]
        i = 0
        while i < n:
            frame = pcm16[i : i + self.frame_samples]
            i += self.frame_samples
            if frame.shape[0] < self.frame_samples:
                if not self._speaking:
                    break  # drop a tail fragment while idle (harmless)
                # keep partial frame so a closing segment is not truncated
            prob = float(self.prob_fn(frame)) if self.prob_fn else 0.0
            self.last_prob = prob
            hot = prob >= self.threshold
            if hot:
                self._hot_in_feed += 1
            self._buf.append(frame)
            if self._speaking:
                self._in_speech_ms += self.frame_ms()
                if hot:
                    self._speech_ms += self.frame_ms()
                    self._sil_ms = 0.0
                else:
                    self._sil_ms += self.frame_ms()
                    if self._sil_ms >= self.silence_ms_to_close:
                        seg = self._close()
                        if seg is not None:
                            out.append(seg)
            else:
                if hot:
                    self._speaking = True
                    self._in_speech_ms = self.frame_ms()
                    self._speech_ms = self.frame_ms()
                    self._sil_ms = 0.0
        return out

    def frame_ms(self) -> float:
        return self.frame_samples * SR_MS

    def _close(self) -> Optional[dict]:
        pcm = np.concatenate(self._buf) if self._buf else np.zeros(0, dtype=np.int16)
        self._buf = []
        self._speaking = False
        self._sil_ms = 0.0
        self._speech_ms = 0.0
        # keep only up to the last hot frame + small pad, drop trailing silence
        keep = int((self._in_speech_ms + 120) * IN_SR / 1000)
        if keep > 0 and keep < pcm.shape[0]:
            pcm = pcm[:keep]
        dur_ms = pcm.shape[0] * SR_MS
        if dur_ms < self.min_speech_ms:
            return None
        return {"sample_rate": IN_SR, "pcm": pcm.astype(np.int16), "ms": dur_ms}

    def flush(self) -> Optional[dict]:
        """Force-close an open segment (end of stream / forced turn end)."""
        if not self._speaking:
            return None
        return self._close()

    def pending(self, min_ms: float = 400.0) -> Optional[dict]:
        """The utterance in progress, as a segment dict, WITHOUT closing it.

        Used for interim (partial) transcription so the user sees their
        words while still speaking. Returns None when nothing is being said
        or the audio is too short to transcribe meaningfully.
        """
        if not self._speaking or not self._buf:
            return None
        pcm = np.concatenate(self._buf)
        dur_ms = pcm.shape[0] * SR_MS
        if dur_ms < min_ms:
            return None
        return {"sample_rate": IN_SR, "pcm": pcm.astype(np.int16), "ms": dur_ms,
                "partial": True}


class SileroVAD:
    """Real Silero VAD probability function (ONNX, CPU)."""

    def __init__(self, threshold: float = 0.5):
        from silero_vad import load_silero_vad

        self.threshold = threshold
        # silero-vad 6.x returns the model DIRECTLY (5.x returned a tuple)
        self.model = load_silero_vad(onnx=True)

    def prob(self, frame_int16: np.ndarray) -> float:
        import torch

        x = torch.from_numpy(frame_int16.astype(np.float32) / 32768.0)
        with torch.no_grad():
            return float(self.model(x, IN_SR).item())


def default_prob_fn() -> Callable[[np.ndarray], float]:
    vad = SileroVAD()
    return vad.prob
