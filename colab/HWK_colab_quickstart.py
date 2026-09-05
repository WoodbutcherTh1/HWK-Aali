"""HWK Aali — extra practice runs on Google Colab's free GPU.

Why this exists: while your RTX 3070 trains Phase A at home, Colab (free T4)
can run small experiments, evals, or micro fine-tunes without touching the
main run. It is a *separate* machine, so it needs its own copy of the project.

How to use (in your open Colab tab):
  1. Runtime -> Change runtime type -> T4 GPU.
  2. In a cell run:  from google.colab import drive; drive.mount('/content/drive')
  3. Upload this file (and optionally data/*.jsonl + file-agent/ folder) to
     your Drive, e.g. /content/drive/MyDrive/HWK/
  4. In a cell run:  !python "/content/drive/MyDrive/HWK/HWK_colab_quickstart.py"
     (edit DRIVE_ROOT below first if you put it somewhere else)

What it does: trains a tiny scratch model on the instruction data you upload
and evaluates it — proof the same pipeline runs on Colab's GPU.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

# --- Edit these for your Drive layout -------------------------------------
DRIVE_ROOT = Path("/content/drive/MyDrive/HWK")
DATA_FILE = DRIVE_ROOT / "data" / "agent_instructions.jsonl"   # any SFT jsonl
OUT_DIR = Path("/content/hwk-sft-output")
TOKENIZER = DRIVE_ROOT / "tokenizer" / "hwk_spm.model"          # optional
# ---------------------------------------------------------------------------

def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    # Make the project code importable if you uploaded the file-agent folder.
    project_package = DRIVE_ROOT / "file-agent"
    if project_package.is_dir() and str(project_package) not in sys.path:
        sys.path.insert(0, str(project_package))

    if not DATA_FILE.exists():
        raise SystemExit(
            f"Training data not found at {DATA_FILE}. Upload data/*.jsonl to your Drive."
        )

    import torch

    print(f"GPU available: {torch.cuda.is_available()}")
    if torch.cuda.is_available():
        print(f"GPU: {torch.cuda.get_device_name(0)}")

    data = DATA_FILE
    tokenizer_args = [f"--tokenizer={TOKENIZER}"] if TOKENIZER.exists() else []
    command = [
        "python", "train_scratch.py",
        f"--data={data}",
        f"--output-dir={OUT_DIR}",
        "--context=256", "--d-model=256", "--heads=8", "--layers=6",
        "--batch-size=4", "--gradient-accumulation=2",
        "--max-steps=400", "--save-steps=100", "--log-steps=10",
        "--device=cuda" if torch.cuda.is_available() else "--device=cpu",
    ] + tokenizer_args

    print("Running:", " ".join(command))
    started = time.time()
    code = os.system(" ".join(str(part) for part in command))
    print(f"Finished in {(time.time() - started) / 60:.1f} min with exit code {code}")
    checkpoint = OUT_DIR / "final.pt"
    if checkpoint.exists():
        print(f"Checkpoint ready: {checkpoint}")


if __name__ == "__main__":
    main()
