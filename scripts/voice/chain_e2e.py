# -*- coding: utf-8 -*-
"""One-shot chained integration run: XTTS probe (fresh int16 wavs) then the
CLI --file turn on the newest produced wav. Detached; log via launch_detached.

Also demonstrates the voice agent as a LIBRARY: it drives the real
VoiceTTS + VoicePipeline objects directly (no CLI subprocess).
"""
from __future__ import annotations

import os
import sys
import time

# scripts/voice/chain_e2e.py -> repo root is THREE dirname steps up
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO_ROOT, "file-agent"))
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))

from voice import force_utf8_stdio  # noqa: E402  (needs the sys.path insert above)

CACHE = r"D:\hwk-data\voice_tts"
REF_WAV = r"D:\hwk-models\voice\references\arabic_male.wav"
XTTS_DIR = r"D:\hwk-models\xtts-v2"
WHISPER_DIR = r"D:\hwk-models\faster-whisper-large-v3-turbo"

AR_TEXT = "مرحباً، أنا عالي، مساعدك الصوتي. كيف بحالك اليوم؟"


def log(m: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def newest_wav() -> str:
    files = [os.path.join(CACHE, f) for f in os.listdir(CACHE) if f.endswith(".wav")]
    if not files:
        raise FileNotFoundError("no wav produced")
    return max(files, key=os.path.getmtime)


def main() -> int:
    force_utf8_stdio()
    from voice.core.stt import WhisperSTT
    from voice.core.tts import VoiceTTS
    from voice.core.vad import default_prob_fn, SpeechSegmenter
    from voice.core.pipeline import VoicePipeline

    # 1) fresh int16 wav via the real TTS module
    tts = VoiceTTS(refs={"ar": REF_WAV}, xtts_dir=XTTS_DIR)
    t0 = time.monotonic()
    r = tts.synthesize(AR_TEXT, "ar")
    log(f"TTS: ok={r.get('ok')} engine={r.get('engine')} {time.monotonic()-t0:.1f}s -> {r.get('path') or r.get('error')}")
    if not r.get("ok"):
        return 1
    wav_path = r["path"]

    # 2) full turn on that wav: real whisper -> real Aali -> real TTS
    import wave as wavemod

    try:
        with wavemod.open(wav_path, "rb") as wf:
            if wf.getsampwidth() != 2:
                raise wavemod.Error("not 16-bit")
            sr = wf.getframerate()
            pcm_bytes = wf.readframes(wf.getnframes())
    except wavemod.Error:
        import numpy as np
        import soundfile as sf

        data, sr = sf.read(wav_path, dtype="int16")
        pcm_bytes = np.asarray(data, dtype="<i2").tobytes()

    import numpy as np

    pcm = np.frombuffer(pcm_bytes, dtype="<i2")
    if sr != 16000:
        n_out = int(pcm.shape[0] * 16000 / sr)
        idx = np.linspace(0, pcm.shape[0] - 1, n_out)
        pcm = np.interp(idx, np.arange(pcm.shape[0]), pcm.astype(np.float64)).astype("<i2")
        log(f"resampled {sr} -> 16k")

    pipe = VoicePipeline(
        segmenter=SpeechSegmenter(prob_fn=default_prob_fn()),
        stt=WhisperSTT(model_dir=WHISPER_DIR),
        tts=tts,
        api_key=os.environ.get("AALI_VOICE_API_KEY") or os.environ.get("AALI_API_KEY", ""),
    )
    t0 = time.monotonic()
    res = pipe.run_turn({"sample_rate": 16000, "pcm": pcm, "ms": pcm.shape[0] / 16.0})
    dt = time.monotonic() - t0
    if not res.get("ok") and not res.get("partial"):
        log(f"TURN FAILED: {res.get('error')}")
        return 1
    log(f"you  : {res.get('user_text')} (lang={res.get('user_lang')}, conf={res.get('confidence', 'n/a')})")
    log(f"aali : {res.get('reply')}")
    for c in res.get("chunks", []):
        log(f"  [{'OK' if c['ok'] else 'ERR'} {c.get('lang')}|{c.get('engine')}] {str(c.get('text',''))[:70]}")
    log(f"turn wall: {dt:.1f}s (whisper+ask+tts, CPU)")
    log("=== CHAIN DONE ===")
    return 0


if __name__ == "__main__":
    try:
        rc = main()
    except Exception as e:
        import traceback

        log("CHAIN FAILED:\n" + traceback.format_exc())
        rc = 1
    sys.exit(rc)
