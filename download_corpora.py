"""Download the HWK Aali pretraining corpora from Hugging Face.

Corpora (all into D:\\hwk-data\\raw\\<corpus>\\, capped, resume-safe):
  pile    - monology/pile-uncopyrighted   (English general; train/00..02.jsonl.zst)
  arabic  - HuggingFaceFW/fineweb-2       (data/arb_Arab/train/*.parquet)
  code    - codeparrot/github-code        (data/train-*.parquet)
  medical - bigbio/med_qa + lavita/ChatDoctor (parquet QA/text)
  law     - pile-of-law/pile-of-law       (contracts + court opinions + constitutions)
  know    - wikimedia/wikipedia (20231101.simple; encyclopedic: history/geography/nature)
  mmlu    - cais/mmlu (auxiliary_train + all splits; QA covering history/geography/science/law/medicine)

Completed downloads are recorded in D:\\hwk-data\\downloads.jsonl so reruns
skip existing files. Use --corpus to fetch a single corpus.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from urllib.request import Request, urlopen

from tqdm import tqdm

import hwk_paths

hwk_paths.apply_environment()

from huggingface_hub import HfApi  # noqa: E402

# corpus -> (repo, list of file filters, size cap in GB)
CORPORA: dict[str, tuple[str, list[str], float]] = {
    "pile": ("monology/pile-uncopyrighted", ["train/00.", "train/01.", "train/02."], 12.0),
    "arabic": ("HuggingFaceFW/fineweb-2", ["data/arb_Arab/train/"], 8.0),
    "code": ("codeparrot/github-code", ["data/train-"], 12.0),
    "codeinstruct": ("HuggingFaceH4/CodeAlpaca_20K", ["data/train-"], 0.5),
    "law": ("pile-of-law/pile-of-law", ["data/train.atticus_contracts.", "data/train.resource_contracts.", "data/train.constitutions.", "data/train.courtlisteneropinions.0", "data/train.courtlisteneropinions.1"], 4.0),
    "know": ("wikimedia/wikipedia", ["20231101.simple/"], 2.0),
    "mmlu": ("cais/mmlu", ["auxiliary_train/", "all/"], 1.0),
    # Middle-East languages: Hebrew + Farsi + Turkish Wikipedia (Arabic is in "arabic")
    "mideast": ("wikimedia/wikipedia", ["20231101.he/", "20231101.fa/", "20231101.tr/"], 4.0),
}

# Additional small medical QA corpora merged under the "medical" corpus.
MEDICAL_EXTRA: list[tuple[str, list[str]]] = [
    ("bigbio/med_qa", ["med_qa_en_bigbio_qa/train-"]),
    ("lavita/ChatDoctor-HealthCareMagic-100k", ["data/train-"]),
]


def _log_download(entry: dict[str, object]) -> None:
    with hwk_paths.DOWNLOAD_LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _remote_size(url: str) -> int | None:
    try:
        request = Request(url, method="HEAD")
        with urlopen(request, timeout=60) as response:
            return int(response.headers.get("Content-Length") or 0) or None
    except Exception:  # noqa: BLE001 - some hosts reject HEAD; size unknown
        return None


def _ready(local: Path, expected: int | None) -> bool:
    if not local.exists() or local.stat().st_size == 0:
        return False
    return expected is None or local.stat().st_size == expected


def download_url(url: str, local: Path, corpus: str) -> bool:
    """Download url to local unless already complete. True when the file is ready."""
    expected = _remote_size(url)
    if _ready(local, expected):
        return True
    local.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(4):
        try:
            with urlopen(url, timeout=120) as response, local.open("wb") as output:
                total = int(response.headers.get("Content-Length") or 0)
                with tqdm(
                    total=total or None, unit="B", unit_scale=True,
                    desc=f"{corpus}/{local.name}", mininterval=2.0,
                ) as bar:
                    while chunk := response.read(1024 * 1024):
                        output.write(chunk)
                        bar.update(len(chunk))
            if _ready(local, expected):
                _log_download({"corpus": corpus, "url": url, "local": str(local), "bytes": local.stat().st_size})
                return True
            raise OSError(f"size mismatch: got {local.stat().st_size}, expected {expected}")
        except Exception as exc:  # noqa: BLE001 - retry transient network errors
            print(f"  attempt {attempt + 1} failed for {url}: {exc}")
            time.sleep(4 * (attempt + 1))
            if local.exists():
                local.unlink()
    return False


def _target_files(repo: str, filters: list[str]) -> list[str]:
    api = HfApi()
    files = api.list_repo_files(repo, repo_type="dataset")
    wanted = [f for f in files if any(pattern in f for pattern in filters)]
    wanted.sort()
    return wanted


def fetch_corpus(corpus: str, repo: str, filters: list[str], cap_gb: float) -> int:
    out_dir = hwk_paths.RAW_DIR / corpus
    out_dir.mkdir(parents=True, exist_ok=True)
    cap = int(cap_gb * 1024**3)
    files = _target_files(repo, filters)
    if not files:
        print(f"[{corpus}] no matching files in {repo}")
        return 0
    total = 0
    for file in files:
        if total >= cap:
            print(f"[{corpus}] size cap reached ({cap_gb} GB); stopping.")
            break
        name = file.rsplit("/", 1)[-1]
        local = out_dir / name
        url = f"https://huggingface.co/datasets/{repo}/resolve/main/{file}"
        if download_url(url, local, corpus):
            total += local.stat().st_size
            print(f"[{corpus}] {name}: {local.stat().st_size / 1e9:.2f} GB (cumulative {total / 1e9:.2f} GB)")
    print(f"[{corpus}] done: {total / 1e9:.2f} GB in {out_dir}")
    return total


def fetch_all(corpora: list[str]) -> None:
    for corpus in corpora:
        print(f"\n=== {corpus} ===")
        if corpus == "medical":
            total = 0
            for repo, filters in MEDICAL_EXTRA:
                total += fetch_corpus("medical", repo, filters, 2.0)
            print(f"[medical] done: {total / 1e9:.2f} GB")
            continue
        repo, filters, cap = CORPORA[corpus]
        fetch_corpus(corpus, repo, filters, cap)


def main() -> None:
    parser = argparse.ArgumentParser(description="Download HWK Aali pretraining corpora.")
    parser.add_argument("--corpus", action="append", default=[], help="Corpus (pile, arabic, code, codeinstruct, medical, law, know, mmlu); repeat or omit for all.")
    args = parser.parse_args()
    hwk_paths.ensure_dirs()
    hwk_paths.DOWNLOAD_LOG.parent.mkdir(parents=True, exist_ok=True)
    fetch_all(args.corpus or list(CORPORA) + ["medical"])


if __name__ == "__main__":
    main()