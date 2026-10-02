# -*- coding: utf-8 -*-
"""Pre-download faster-whisper large-v3-turbo as PLAIN FILES.

HF cache symlinks fail on this box (WinError 1314, no Developer Mode) —
same lesson as XTTS run 1. local_dir -> plain files on D:, and WhisperModel
accepts a local directory path directly.
"""
from __future__ import annotations

import os
import sys
import time

DEST = r"D:\hwk-models\faster-whisper-large-v3-turbo"
# SYSTRAN has no turbo repo (verified live via the HF search API); this is
# the standard ct2 conversion: MIT, ctranslate2 library, AR/HE/EN tags.
REPO = "deepdml/faster-whisper-large-v3-turbo-ct2"


def log(m: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def main() -> int:
    from huggingface_hub import snapshot_download

    log(f"fetching {REPO} -> {DEST}")
    d = snapshot_download(REPO, local_dir=DEST)
    log(f"done: {d}")
    for f in sorted(os.listdir(d)):
        log(f"  {f} ({os.path.getsize(os.path.join(d, f)) // 1024} KB)")
    return 0


if __name__ == "__main__":
    try:
        rc = main()
    except Exception as e:
        log(f"FAILED: {type(e).__name__}: {e}")
        rc = 1
    sys.exit(rc)
