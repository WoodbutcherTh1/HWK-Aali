"""Download small, openly-licensed instruction datasets hosted directly on
GitHub (no API key, no huggingface_hub needed) and convert them into the
instruction/input/output parquet schema tokenize_corpus.py already knows how
to read.

These are static, pre-published academic/research datasets meant for reuse
(unlike scraping a live commercial chat product) — Stanford Alpaca, Code
Alpaca, and Microsoft's GPT-4-generated Alpaca variant. All non-commercial /
research-use licensed, appropriate for a personal local model like Aali.

Usage:
  python download_github_corpora.py                # all three
  python download_github_corpora.py --corpus alpaca
"""

from __future__ import annotations

import argparse
import json
import urllib.request
from pathlib import Path

import pandas as pd

import hwk_paths

SOURCES = {
    "alpaca": "https://raw.githubusercontent.com/tatsu-lab/stanford_alpaca/main/alpaca_data.json",
    "code_alpaca": "https://raw.githubusercontent.com/sahil280114/codealpaca/master/data/code_alpaca_20k.json",
    "alpaca_gpt4": "https://raw.githubusercontent.com/Instruction-Tuning-with-GPT-4/GPT-4-LLM/main/data/alpaca_gpt4_data.json",
}


def fetch(name: str, url: str) -> None:
    print(f"[{name}] downloading {url}")
    with urllib.request.urlopen(url, timeout=180) as resp:
        data = json.load(resp)
    rows = []
    for rec in data:
        instr = rec.get("instruction", "")
        inp = rec.get("input", "")
        out = rec.get("output", "")
        if not instr or not out:
            continue
        rows.append({"instruction": instr, "input": inp, "output": out})
    df = pd.DataFrame(rows)
    out_dir = hwk_paths.RAW_DIR / name
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "part-00000.parquet"
    df.to_parquet(out_path, index=False)
    print(f"[{name}] {len(df)} records -> {out_path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", action="append", choices=sorted(SOURCES), default=None)
    args = parser.parse_args()
    hwk_paths.ensure_dirs()
    names = args.corpus or sorted(SOURCES)
    for name in names:
        fetch(name, SOURCES[name])
    print("Done. Next: python tokenize_corpus.py --tokenizer D:/hwk-data/tokenizer/hwk_spm.model "
          + " ".join(f"--corpus {n}" for n in names))


if __name__ == "__main__":
    main()
