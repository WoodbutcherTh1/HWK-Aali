# -*- coding: utf-8 -*-
"""Aali voice CLI.

Modes:
  python -m voice.cli                      live mic loop (VAD + barge-in)
  python -m voice.cli --file x.wav         transcribe a wav through the
                                           full pipeline, print + play/save
  python -m voice.cli --fake               fake STT/TTS (no models) —
                                           wiring test on any machine
  python -m voice.cli --save out.wav       file mode: write TTS result

CPU-only. All local. Exit codes: 0 ok, 2 bad args, 3 audio device missing.
"""
from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from pathlib import Path

# run as a plain script from anywhere: scripts/ must be importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from voice import force_utf8_stdio
from voice.core.pipeline import VoicePipeline
from voice.core.vad import SpeechSegmenter, default_prob_fn

REPO_ROOT = Path(__file__).resolve().parents[2] / "file-agent"


def build_real_pipeline(cfg: dict) -> VoicePipeline:
    import os
    import sys

    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from voice.core.stt import WhisperSTT
    from voice.core.tts import VoiceTTS

    tts_cfg = cfg.get("tts", {})
    return VoicePipeline(
        segmenter=SpeechSegmenter(prob_fn=default_prob_fn()),
        stt=WhisperSTT(
            model_size=cfg.get("stt", {}).get("model", "large-v3-turbo"),
            compute_type=cfg.get("stt", {}).get("compute_type", "int8"),
            model_dir=cfg.get("stt", {}).get("model_dir"),
        ),
        tts=VoiceTTS(
            refs=tts_cfg.get("references") or None,
            fallback_ref_lang=tts_cfg.get("fallback_ref_lang", "ar"),
            xtts_dir=tts_cfg.get("xtts_snapshot_dir") or None,
        ),
        api_key=os.environ.get("AALI_VOICE_API_KEY") or os.environ.get("AALI_API_KEY", ""),
    )


def load_cfg() -> dict:
    import yaml

    cfg_path = Path(__file__).resolve().parent / "config.yaml"
    if cfg_path.exists():
        with open(cfg_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


# --------------------------------------------------------------------------
# live mic mode
# --------------------------------------------------------------------------

def run_live(pipe: VoicePipeline) -> int:
    try:
        import sounddevice as sd
    except Exception as exc:
        print(f"[voice] sounddevice unavailable: {exc}", file=sys.stderr)
        return 3

    IN_SR = 16000

    def on_chunk(item: dict) -> None:
        if not item.get("ok"):
            print(f"[tts {item.get('lang')}] FAILED: {item.get('error')}")
            return
        import wave

        with wave.open(item["path"], "rb") as wf:
            pcm = np.frombuffer(wf.readframes(wf.getnframes()), dtype="<i2")
        sr = int(item.get("sr") or 24000)
        print(f"[aali {item.get('lang')}|{item.get('engine')}] {item.get('text', '')}")
        # Phase 1: plain blocking playback (server-side barge-in cancels the
        # remaining chunks there; mic-side AEC is a later phase).
        stream = sd.OutputStream(samplerate=sr, channels=1, dtype="int16")
        stream.start()
        block = 1200
        for i in range(0, pcm.shape[0], block):
            stream.write(pcm[i : i + block].reshape(-1, 1))
        stream.stop(); stream.close()

    def callback(indata, frames, t, status):
        pcm = indata[:, 0].copy()
        segs = pipe.segmenter.feed(pcm)
        for seg in segs:
            threading.Thread(
                target=pipe.run_turn, args=(seg,), kwargs={"on_chunk": on_chunk},
                daemon=True,
            ).start()

    print("[voice] ميكروفون يعمل — تحدث، واضغط Ctrl+C للخروج")
    with sd.InputStream(samplerate=IN_SR, channels=1, dtype="int16",
                        blocksize=512, callback=callback):
        try:
            while True:
                time.sleep(0.2)
        except KeyboardInterrupt:
            print("\n[voice] وداعاً!")
    return 0


# --------------------------------------------------------------------------
# file mode (testable: no audio devices needed)
# --------------------------------------------------------------------------

def run_file(pipe: VoicePipeline, wav_path: str, save_to: str | None) -> int:
    import wave

    try:
        with wave.open(wav_path, "rb") as wf:
            if wf.getnchannels() != 1 or wf.getsampwidth() != 2:
                print("[voice] expected mono 16-bit wav", file=sys.stderr)
                return 2
            sr = wf.getframerate()
            pcm = np.frombuffer(wf.readframes(wf.getnframes()), dtype="<i2")
    except wave.Error:
        # float32/other-format wavs (e.g. XTTS outputs) via soundfile
        import soundfile as sf

        data, sr = sf.read(wav_path, dtype="int16")
        pcm = np.asarray(data, dtype="<i2")
        if pcm.ndim > 1:
            pcm = pcm[:, 0]
    if sr != 16000:
        # honest linear resample to 16k (whisper is robust to it; torchaudio
        # would be nicer but this keeps the CLI dependency-free)
        n_out = int(pcm.shape[0] * 16000 / sr)
        idx = np.linspace(0, pcm.shape[0] - 1, n_out)
        pcm = np.interp(idx, np.arange(pcm.shape[0]), pcm.astype(np.float64))
        pcm = pcm.astype("<i2")
        print(f"[voice] resampled {sr}Hz -> 16kHz")

    t0 = time.monotonic()
    res = pipe.run_turn({"sample_rate": 16000, "pcm": pcm, "ms": pcm.shape[0] / 16.0})
    dt = time.monotonic() - t0
    if not res.get("ok") and not res.get("partial"):
        print(f"[voice] turn failed: {res.get('error')}", file=sys.stderr)
        return 1
    print(f"[you] {res.get('user_text')}  (lang={res.get('user_lang')})")
    print(f"[aali] {res.get('reply')}")
    for c in res.get("chunks", []):
        mark = "OK " if c["ok"] else "ERR"
        print(f"  [{mark} {c.get('lang')}|{c.get('engine')}] {c.get('text', '')[:60]}")
    print(f"[voice] turn wall time: {dt:.1f}s")
    if save_to and res.get("chunks"):
        import shutil

        ok_paths = [c["path"] for c in res["chunks"] if c.get("ok") and c.get("path")]
        if ok_paths:
            shutil.copyfile(ok_paths[0], save_to)
            print(f"[voice] saved first chunk -> {save_to}")
    return 0


def main() -> int:
    force_utf8_stdio()
    ap = argparse.ArgumentParser(prog="aali-voice")
    ap.add_argument("--file", help="transcribe a 16k mono wav through the pipeline")
    ap.add_argument("--save", help="with --file: save the first TTS chunk here")
    ap.add_argument("--fake", action="store_true", help="fake STT/TTS (wiring test)")
    ap.add_argument("--base-url", default="http://127.0.0.1:5055")
    args = ap.parse_args()

    if args.fake:
        from voice.core.server import _fake_pipeline

        pipe = _fake_pipeline()
        pipe.base_url = args.base_url
    else:
        pipe = build_real_pipeline(load_cfg())
        pipe.base_url = args.base_url

    if args.file:
        return run_file(pipe, args.file, args.save)
    return run_live(pipe)


if __name__ == "__main__":
    sys.exit(main())
