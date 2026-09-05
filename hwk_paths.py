"""Central disk-layout configuration for the HWK Aali project.

Everything large (raw corpora, cleaned data, token shards, the tokenizer,
checkpoints) lives on D: and X: so the small code folder on C: (and OneDrive)
stays light. Every path can be overridden with an environment variable.
"""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent

DATA_DIR = Path(os.getenv("HWK_DATA_DIR", "D:/hwk-data")).expanduser()
MODELS_DIR = Path(os.getenv("HWK_MODELS_DIR", "D:/hwk-models")).expanduser()
BACKUP_DIR = Path(os.getenv("HWK_BACKUP_DIR", "X:/hwk-backups")).expanduser()

RAW_DIR = DATA_DIR / "raw"
CLEANED_DIR = DATA_DIR / "cleaned"
TOKENS_DIR = DATA_DIR / "tokens"
TOKENIZER_DIR = DATA_DIR / "tokenizer"
HF_CACHE_DIR = DATA_DIR / "hf-cache"

DOWNLOAD_LOG = DATA_DIR / "downloads.jsonl"

# Model checkpoints the training scripts write to and the agent loads.
SCRATCH_MODEL_DIR = MODELS_DIR / "scratch"
SCRATCH_FINAL = SCRATCH_MODEL_DIR / "final.pt"

# Small default checkpoint used by the app when LOCAL_MODEL_PATH is not set.
DEFAULT_LOCAL_MODEL = PROJECT_ROOT / "model" / "scratch" / "final.pt"


def ensure_dirs() -> None:
    for directory in (
        RAW_DIR,
        CLEANED_DIR,
        TOKENS_DIR,
        TOKENIZER_DIR,
        HF_CACHE_DIR,
        MODELS_DIR,
        BACKUP_DIR,
        SCRATCH_MODEL_DIR,
    ):
        directory.mkdir(parents=True, exist_ok=True)


def apply_environment() -> None:
    """Export HF-related cache variables so downloads land on D:."""
    ensure_dirs()
    os.environ.setdefault("HF_HOME", str(HF_CACHE_DIR))
    os.environ.setdefault("HF_HUB_CACHE", str(HF_CACHE_DIR / "hub"))
    os.environ.setdefault("TRANSFORMERS_CACHE", str(HF_CACHE_DIR / "transformers"))


if __name__ == "__main__":
    apply_environment()
    print(f"DATA_DIR     = {DATA_DIR}")
    print(f"MODELS_DIR   = {MODELS_DIR}")
    print(f"BACKUP_DIR   = {BACKUP_DIR}")
    print(f"TOKENIZER    = {TOKENIZER_DIR}")
    print(f"TOKENS       = {TOKENS_DIR}")
    print(f"HF_CACHE     = {HF_CACHE_DIR}")