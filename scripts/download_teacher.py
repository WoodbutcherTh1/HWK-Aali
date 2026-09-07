"""Download the Soup teacher model once into D:/hwk-models (soup serve/train
expect a LOCAL model directory — they do not fetch from the HF hub)."""
from __future__ import annotations

import sys
import time
from pathlib import Path

from huggingface_hub import snapshot_download

REPO_ID = "Qwen/Qwen2.5-1.5B-Instruct"
DEST = Path("D:/hwk-models/Qwen2.5-1.5B-Instruct")


def main() -> int:
    started = time.time()
    print(f"downloading {REPO_ID} -> {DEST}", flush=True)
    path = snapshot_download(REPO_ID, local_dir=str(DEST))
    minutes = (time.time() - started) / 60
    print(f"done in {minutes:.1f} min -> {path}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
