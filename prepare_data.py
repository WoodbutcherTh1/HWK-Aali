"""Download and clean The Pile Parquet shards without load_dataset."""

from __future__ import annotations

import argparse
import re
import unicodedata
from pathlib import Path
from typing import Iterable
from urllib.request import urlopen

import pandas as pd
from tqdm import tqdm


BASE_URL = (
    "https://huggingface.co/datasets/EleutherAI/pile/resolve/main/data/"
    "train-{part:05d}-of-00030.parquet"
)
DEFAULT_RAW_DIR = Path("processed_data/raw")
DEFAULT_OUTPUT = Path("cleaned_pile.parquet")


def _download(url: str, destination: Path) -> Path:
    if destination.exists() and destination.stat().st_size > 0:
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    with urlopen(url, timeout=120) as response, destination.open("wb") as output:
        total = int(response.headers.get("Content-Length", 0))
        with tqdm(total=total or None, unit="B", unit_scale=True, desc=destination.name) as bar:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
                bar.update(len(chunk))
    return destination


def _normalise_text(value: object) -> str:
    text = unicodedata.normalize("NFC", str(value))
    text = text.replace("\x00", " ")
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def clean_data(frames: Iterable[pd.DataFrame]) -> pd.DataFrame:
    combined = pd.concat(frames, ignore_index=True)
    if "text" not in combined.columns:
        raise ValueError("The downloaded Parquet data does not contain a text column.")
    cleaned = combined[["text"]].dropna().copy()
    cleaned["text"] = cleaned["text"].map(_normalise_text)
    cleaned = cleaned[cleaned["text"].str.len() >= 100]
    cleaned = cleaned.drop_duplicates(subset=["text"]).reset_index(drop=True)
    return cleaned


def load_pile_parts(
    parts: int = 10,
    raw_dir: str | Path = DEFAULT_RAW_DIR,
) -> pd.DataFrame:
    if not 1 <= parts <= 30:
        raise ValueError("parts must be between 1 and 30")
    raw_path = Path(raw_dir)
    frames: list[pd.DataFrame] = []
    for part in tqdm(range(parts), desc="Loading Pile shards"):
        url = BASE_URL.format(part=part)
        local_file = raw_path / f"train-{part:05d}-of-00030.parquet"
        source = _download(url, local_file)
        frames.append(pd.read_parquet(source, engine="pyarrow", columns=["text"]))
    return clean_data(frames)


def load_all_pile(
    raw_dir: str | Path = DEFAULT_RAW_DIR,
    output_path: str | Path = "processed_data/cleaned_pile.parquet",
) -> pd.DataFrame:
    """Download, clean, and save all 30 shards when explicitly requested."""
    data = load_pile_parts(parts=30, raw_dir=raw_dir)
    save_cleaned_data(data, output_path)
    return data


def save_cleaned_data(data: pd.DataFrame, output_path: str | Path = DEFAULT_OUTPUT) -> None:
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    data.to_parquet(destination, engine="pyarrow", index=False)
    if destination != Path(DEFAULT_OUTPUT):
        Path(DEFAULT_OUTPUT).parent.mkdir(parents=True, exist_ok=True)
        data.to_parquet(DEFAULT_OUTPUT, engine="pyarrow", index=False)


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare The Pile Parquet data.")
    parser.add_argument("--parts", type=int, default=10, help="Number of shards to load (1-30).")
    parser.add_argument("--raw-dir", default=str(DEFAULT_RAW_DIR))
    parser.add_argument("--output", default="processed_data/cleaned_pile.parquet")
    parser.add_argument("--all", action="store_true", help="Load all 30 shards.")
    args = parser.parse_args()

    data = load_all_pile(args.raw_dir, args.output) if args.all else load_pile_parts(args.parts, args.raw_dir)
    if not args.all:
        save_cleaned_data(data, args.output)
    print(f"Saved {len(data):,} cleaned samples to {args.output}")


if __name__ == "__main__":
    main()