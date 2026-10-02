# -*- coding: utf-8 -*-
"""XTTS-v2 live probe — voice-cloned Arabic synthesis (CPU), via the REAL
VoiceTTS module (scripts/voice/core/tts.py) so the probe exercises the
exact production path (owner rule: replicate the real code path before
theorizing; probe ≠ production).

Runs detached. Log: D:/hwk-data/voice_probe.log
Model: D:/hwk-models/xtts-v2 (plain files, already downloaded — 2.0 GB).
"""
from __future__ import annotations

import os
import sys
import time
import traceback

LOG_PATH = r"D:\hwk-data\voice_probe.log"
OUT_DIR = r"D:\hwk-data\voice_probe"
REF_WAV = r"D:\hwk-models\voice\references\arabic_male.wav"
XTTS_DIR = r"D:\hwk-models\xtts-v2"
# probe lives at scripts/voice/probe_xtts.py -> repo root is 3 levels up
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# file_agent.tts (piper fallback) imports from file-agent/ — probe must set
# the same sys.path the real server builds (run-2 lesson: a wrong join
# silently breaks the fallback and the honest error is the only signal).
REPO_SCRIPTS = REPO_ROOT

AR_TEXT = "مرحباً، أنا عالي، مساعدك الصوتي. كيف بحالك اليوم؟"
EN_TEXT = "Hello, I am Aali, your voice assistant. How are you today?"


def log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def ram_free_gb() -> float:
    try:
        import psutil

        return psutil.virtual_memory().available / (1024**3)
    except Exception:
        import ctypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        st = MEMORYSTATUSEX()
        st.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
        return st.ullAvailPhys / (1024**3)


def main() -> int:
    sys.path.insert(0, os.path.join(REPO_ROOT, "file-agent"))
    sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))
    log("=== XTTS-v2 probe v2 start (CPU, via VoiceTTS) ===")
    free = ram_free_gb()
    log(f"RAM free: {free:.2f} GB")
    if free < 3.5:
        log("ABORT: <3.5 GB RAM free (house RAM gate)")
        return 4

    os.makedirs(OUT_DIR, exist_ok=True)
    if not os.path.isfile(REF_WAV):
        log(f"ABORT: reference wav missing: {REF_WAV}")
        return 2

    from voice.core.tts import VoiceTTS

    tts = VoiceTTS(refs={"ar": REF_WAV}, xtts_dir=XTTS_DIR)
    log(f"engine report: {tts.engine_report()}")

    t2 = time.time()
    ar = tts.synthesize(AR_TEXT, "ar")
    dt = time.time() - t2
    log(f"AR synth: ok={ar.get('ok')} engine={ar.get('engine')} {dt:.1f}s -> {ar.get('path') or ar.get('error')}")
    if not ar.get("ok"):
        log("PROBE FAILED: AR synthesis failed (see voice_tts.log for the real error)")
        return 1

    t3 = time.time()
    en = tts.synthesize(EN_TEXT, "en")
    log(f"EN synth: ok={en.get('ok')} engine={en.get('engine')} {time.time() - t3:.1f}s -> {en.get('path') or en.get('error')}")

    ok = bool(ar.get("ok")) and bool(en.get("ok"))
    log(f"=== probe {'DONE' if ok else 'PARTIAL (AR ok, EN failed)'} — RAM at end {ram_free_gb():.2f} GB free ===")
    return 0 if ok else 2


if __name__ == "__main__":
    try:
        rc = main()
    except Exception:
        log("PROBE FAILED:\n" + traceback.format_exc())
        rc = 1
    sys.exit(rc)
