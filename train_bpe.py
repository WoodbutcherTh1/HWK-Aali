"""Train the HWK BPE tokenizer from scratch on a sample of the corpora.

Extracts up to --sample-mb of text from D:\\hwk-data\\raw (parquet,
jsonl.zst, jsonl.xz, txt), then trains a 32k SentencePiece BPE model into
D:\\hwk-data\\tokenizer. Deterministic sampling keeps the run reproducible.
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

PROJECT_PACKAGE = Path(__file__).resolve().parent / "file-agent"
if str(PROJECT_PACKAGE) not in sys.path:
    sys.path.insert(0, str(PROJECT_PACKAGE))

import hwk_paths
from hwk_model.bpe_tokenizer import train_bpe
from tokenize_corpus import iter_documents


CHUNK_BYTES = 4 * 1024 * 1024


def extract_sample(sample_mb: int, seed: int) -> list[Path]:
    rng = random.Random(seed)
    sample_dir = hwk_paths.TOKENIZER_DIR / "corpus_sample"
    sample_dir.mkdir(parents=True, exist_ok=True)
    budget = sample_mb * 1024 * 1024
    written = 0
    outputs: list[Path] = []
    documents = 0

    def open_chunk() -> tuple[object, int]:
        chunk = sample_dir / f"sample_{len(outputs):04d}.txt"
        outputs.append(chunk)
        return chunk.open("w", encoding="utf-8"), 0

    handle, chunk_written = open_chunk()
    # RAW_DIR holds one sub-directory per corpus; iter_documents expects a
    # single corpus directory and yields (corpus, source_file, text).
    corpus_dirs = sorted(d for d in hwk_paths.RAW_DIR.iterdir() if d.is_dir())
    for corpus_dir in corpus_dirs:
        for corpus, _source, text in iter_documents(corpus_dir):
            if written >= budget:
                break
            # Arabic gets full sampling so the vocab covers it well; the much
            # larger English corpora are sub-sampled.
            if rng.random() > (1.0 if corpus == "arabic" else 0.1):
                continue
            encoded = (text + "\n").encode("utf-8")
            handle.write(text + "\n")
            written += len(encoded)
            chunk_written += len(encoded)
            documents += 1
            if chunk_written >= CHUNK_BYTES:
                handle.close()
                handle, chunk_written = open_chunk()
            if documents % 2000 == 0:
                print(f"  sampled {documents} docs ({written / 1e6:.0f} MB)", flush=True)
        if written >= budget:
            break
    handle.close()
    print(f"sampled {written / 1e6:.0f} MB across {len(outputs)} chunk files ({documents} docs)")
    return outputs


def main() -> None:
    parser = argparse.ArgumentParser(description="Train the HWK BPE tokenizer from scratch.")
    parser.add_argument("--sample-mb", type=int, default=600)
    parser.add_argument("--vocab-size", type=int, default=32000)
    parser.add_argument("--seed", type=int, default=11)
    args = parser.parse_args()

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    hwk_paths.ensure_dirs()
    inputs = extract_sample(args.sample_mb, args.seed)
    if not inputs:
        raise SystemExit("no corpus text found — run download_corpora.py first")
    tokenizer = train_bpe(inputs, hwk_paths.TOKENIZER_DIR, vocab_size=args.vocab_size)
    print(f"BPE trained: {tokenizer.model_path} vocab={tokenizer.vocab_size}")


if __name__ == "__main__":
    main()