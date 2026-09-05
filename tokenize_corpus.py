"""Clean and tokenize downloaded corpora into uint16 token shards.

Reads D:\\hwk-data\\raw\\<corpus>\\ (parquet / jsonl.zst / jsonl.xz / .txt),
normalizes the text, and writes token shards (uint16 arrays) to
D:\\hwk-data\\tokens\\<corpus>\\ with a manifest. The final EVAL_TAIL tokens
of each corpus are held back as an evaluation shard.

Usage:
  python tokenize_corpus.py                      # all corpora
  python tokenize_corpus.py --corpus arabic code # selected corpora
  python tokenize_corpus.py --tokenizer D:/hwk-data/tokenizer/hwk_spm.model
"""

from __future__ import annotations

import argparse
import io
import json
import lzma
import re
import sys
import unicodedata
from collections import deque
from pathlib import Path

PROJECT_PACKAGE = Path(__file__).resolve().parent / "file-agent"
if str(PROJECT_PACKAGE) not in sys.path:
    sys.path.insert(0, str(PROJECT_PACKAGE))

import numpy as np
import pandas as pd
import zstandard

import hwk_paths
from hwk_model import ByteTokenizer
from hwk_model.bpe_tokenizer import load_bpe

SHARD_TOKENS = 8_000_000
EVAL_TAIL = 2_000_000
MIN_DOC_CHARS = 50


def _clean(text: str) -> str:
    text = unicodedata.normalize("NFC", str(text))
    text = text.replace("\x00", " ")
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def _json_text(record: dict[str, object]) -> str | None:
    for key in ("text", "content", "article", "body"):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def iter_parquet_texts(path: Path):
    """Yield cleaned texts, reading the parquet row-group by row-group."""
    import pyarrow.parquet as pq

    parquet_file = pq.ParquetFile(path)
    columns = parquet_file.schema_arrow.names
    if "text" in columns:
        column = "text"
    elif "content" in columns:
        column = "content"
    elif {"question", "answer"}.issubset(columns):
        column = "qa"
    elif {"instruction", "output"}.issubset(columns):
        column = "instruct"
    else:
        raise ValueError(f"no usable text column in {path}: {columns}")

    if column == "text":
        for batch in parquet_file.iter_batches(columns=["text"], batch_size=4096):
            for value in batch.column("text").to_pylist():
                if value is None:
                    continue
                text = _clean(value)
                if len(text) >= MIN_DOC_CHARS:
                    yield text
        return
    if column == "content":
        for batch in parquet_file.iter_batches(columns=["content"], batch_size=4096):
            for value in batch.column("content").to_pylist():
                if value is None:
                    continue
                text = _clean(value)
                if len(text) >= MIN_DOC_CHARS:
                    yield text
        return
    if column == "qa":
        wanted = [c for c in ("question", "answer", "context") if c in columns]
        for batch in parquet_file.iter_batches(columns=wanted, batch_size=4096):
            question = batch.column("question").to_pylist()
            answer = batch.column("answer").to_pylist()
            context = batch.column("context").to_pylist() if "context" in columns else [""] * len(question)
            for q, a, c in zip(question, answer, context):
                text = _clean(f"Q: {q or ''}\nA: {a or ''}\nCtx: {c or ''}")
                if len(text) >= MIN_DOC_CHARS:
                    yield text
        return
    wanted = [c for c in ("instruction", "input", "output") if c in columns]
    for batch in parquet_file.iter_batches(columns=wanted, batch_size=4096):
        instruction = batch.column("instruction").to_pylist()
        inputs = batch.column("input").to_pylist() if "input" in columns else [""] * len(instruction)
        output = batch.column("output").to_pylist()
        for i, inp, out in zip(instruction, inputs, output):
            text = _clean(f"Q: {i or ''} {inp or ''}\nA: {out or ''}")
            if len(text) >= MIN_DOC_CHARS:
                yield text


def iter_json_lines(path: Path, compression: str):
    if compression == "zst":
        with path.open("rb") as handle:
            reader = zstandard.ZstdDecompressor().stream_reader(handle)
            text_stream = io.TextIOWrapper(
                reader, encoding="utf-8", errors="replace"
            )
            with text_stream:
                yield from _iter_text_stream(text_stream, path)
    elif compression == "xz":
        with lzma.open(path, "rt", encoding="utf-8", errors="replace") as handle:
            yield from _iter_text_stream(handle, path)
    else:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            yield from _iter_text_stream(handle, path)


def _iter_text_stream(handle, path: Path):
    for line in handle:
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            text = _json_text(record)
        elif isinstance(record, str):
            text = record
        else:
            text = None
        if text:
            text = _clean(text)
            if len(text) >= MIN_DOC_CHARS:
                yield text


def iter_documents(raw_dir: Path):
    """Yield (corpus, source_file, text) for every file in a raw corpus dir."""
    corpus = raw_dir.name
    for path in sorted(raw_dir.iterdir()):
        if path.suffix == ".parquet":
            yield from ((corpus, path.name, text) for text in iter_parquet_texts(path))
        elif path.suffix == ".zst":
            yield from ((corpus, path.name, text) for text in iter_json_lines(path, "zst"))
        elif path.suffix == ".xz":
            yield from ((corpus, path.name, text) for text in iter_json_lines(path, "xz"))
        elif path.suffix == ".txt":
            text = _clean(path.read_text(encoding="utf-8", errors="replace"))
            if len(text) >= MIN_DOC_CHARS:
                yield corpus, path.name, text


def tokenize_corpus(corpus: str, tokenizer, out_root: Path) -> dict[str, object]:
    raw_dir = hwk_paths.RAW_DIR / corpus
    out_dir = out_root / corpus
    out_dir.mkdir(parents=True, exist_ok=True)

    train_shards: list[dict[str, object]] = []
    eval_shards: list[dict[str, object]] = []
    buffer: list[int] = []
    tail: deque[int] = deque(maxlen=EVAL_TAIL)
    total_tokens = 0
    documents = 0
    shard_index = 0

    def write_shard(tokens: list[int], name: str) -> None:
        array = np.asarray(tokens, dtype=np.uint16)
        destination = out_dir / name
        array.tofile(destination)
        return int(array.size)

    for _corpus, source, text in iter_documents(raw_dir):
        documents += 1
        tokens = tokenizer.encode(text)
        total_tokens += len(tokens)
        for token in tokens:
            tail.append(token)  # rolling window of the final EVAL_TAIL tokens
            buffer.append(token)
        # Flush complete train shards, always keeping the eval tail buffered.
        while len(buffer) > EVAL_TAIL + SHARD_TOKENS:
            chunk = buffer[:SHARD_TOKENS]
            del buffer[:SHARD_TOKENS]
            size = write_shard(chunk, f"train_{shard_index:05d}.bin")
            train_shards.append({"file": f"train_{shard_index:05d}.bin", "tokens": size})
            shard_index += 1
        if documents % 5000 == 0:
            print(f"  [{corpus}] docs={documents} tokens={total_tokens:,} shards={shard_index}", flush=True)

    # Flush whatever training tokens remain, minus the eval tail.
    keep = max(0, len(buffer) - EVAL_TAIL)
    if keep:
        size = write_shard(buffer[:keep], f"train_{shard_index:05d}.bin")
        train_shards.append({"file": f"train_{shard_index:05d}.bin", "tokens": size})
        shard_index += 1

    # Write the eval tail (the final contiguous EVAL_TAIL tokens).
    if tail:
        size = write_shard(list(tail), "eval.bin")
        eval_shards.append({"file": "eval.bin", "tokens": size})

    manifest = {
        "corpus": corpus,
        "documents": documents,
        "total_tokens": total_tokens,
        "train_shards": train_shards,
        "eval_shards": eval_shards,
    }
    with (out_dir / "manifest.json").open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2)
    print(f"[{corpus}] docs={documents} tokens={total_tokens:,} train_shards={len(train_shards)} eval_tokens={sum(s['tokens'] for s in eval_shards):,}", flush=True)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Tokenize HWK corpora into shards.")
    parser.add_argument("--corpus", action="append", default=[])
    parser.add_argument("--tokenizer", default=None, help="Path to the BPE .model file; default byte tokenizer.")
    parser.add_argument("--out-root", default=None)
    args = parser.parse_args()

    hwk_paths.ensure_dirs()
    out_root = Path(args.out_root or hwk_paths.TOKENS_DIR)

    if args.tokenizer:
        tokenizer = load_bpe(args.tokenizer)
        print(f"using BPE tokenizer: {tokenizer.model_path} (vocab {tokenizer.vocab_size})")
    else:
        tokenizer = ByteTokenizer()
        print("using byte tokenizer (vocab 259)")

    corpora = args.corpus or [d.name for d in sorted(hwk_paths.RAW_DIR.iterdir()) if d.is_dir()]
    for corpus in corpora:
        if not (hwk_paths.RAW_DIR / corpus).is_dir():
            print(f"[{corpus}] no raw data; skipping")
            continue
        print(f"=== tokenizing {corpus} ===")
        tokenize_corpus(corpus, tokenizer, out_root)


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    main()