"""Fetch the Knesset Corpus sentence shards (Hebrew Phase 3.5, source 3.5.2).

Source: HaifaCLGroup/KnessetCorpus on HuggingFace — an annotated corpus of
Hebrew parliamentary proceedings (plenary 1992-2024, committees 1998-2024),
released by the curators (University of Haifa / IAHLT) under
**CC-BY-SA-4.0** (dataset card license tag, verified live 2026-10-02).

We download ONLY the two no-morph sentence subsets (sentence_text without
morphological annotation — the card's recommended lightweight option):
  plenary_no_morph_sentences_shards_bzip2_files   (29 files, ~1.06 GB)
  committee_no_morph_sentences_shards_bzip2_files (91 files, ~2.61 GB)
Every file's sha256 is known from the LFS metadata (lfs.oid) and verified
after download. Extraction keeps ONLY sentence_text, grouped per protocol
per shard — speaker names, genders, factions and other personal metadata
are NOT copied into the corpus (owner rule: no personal data).

Stages (each resume-safe):
  fetch    — download + sha256-verify the .jsonl.bz2 shards; manifest.jsonl
  inspect  — print the first record's keys of one shard (schema check)
  extract  — bz2 -> jsonl rows {protocol_id, subset, source_url, text}
Stdlib only, CPU-only. Content-free counters in logs. No cleaning
(Phase 4), no tokenization (Phase 6).
"""

from __future__ import annotations

import argparse
import bz2
import hashlib
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

UA = "HWK-Aali-HebrewCorpus/0.1 (research; +https://github.com/HWK-Aali)"
REPO = "HaifaCLGroup/KnessetCorpus"
RESOLVE = f"https://huggingface.co/datasets/{REPO}/resolve/main/"
API_TREE = f"https://huggingface.co/api/datasets/{REPO}/tree/main/"
SUBSETS = [
    "protocols_sentences/plenary_no_morph_sentences_shards_bzip2_files",
    "protocols_sentences/committee_no_morph_sentences_shards_bzip2_files",
]
RAW = Path("D:/hwk-data/hebrew/raw/knesset")
EXTRACT = Path("D:/hwk-data/hebrew/extracted/knesset")
DOWNLOAD_LOG = Path("D:/hwk-data/hebrew/DOWNLOAD_LOG.md")
EXTRACT_LOG = Path("D:/hwk-data/hebrew/EXTRACT_LOG.md")
CARD_LICENSE = "cc-by-sa-4.0 (dataset card, verified live 2026-10-02)"


def utcnow() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())


def log(message: str) -> None:
    line = f"[{utcnow()} UTC] {message}"
    print(line, flush=True)


def list_tree(subdir: str) -> list[dict]:
    """All files (with sizes + lfs sha256) under one dataset subtree."""
    files: list[dict] = []
    url = f"{API_TREE}{subdir}?recursive=true"
    while url:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=120) as r:
            items = json.load(r)
            link = r.headers.get("Link", "")
        files.extend(i for i in items if i.get("type") == "file")
        url = None
        for part in link.split(","):
            if 'rel="next"' in part:
                url = part.split(";")[0].strip("<>")
    return files


def fetch_file(url: str, dest: Path, expect_sha: str | None, expect_size: int) -> dict:
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_suffix(dest.suffix + ".part")
            sha = hashlib.sha256()
            size = 0
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=600) as r, tmp.open("wb") as w:
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    sha.update(chunk)
                    size += len(chunk)
                    w.write(chunk)
            if expect_size and size != expect_size:
                raise IOError(f"size mismatch: got {size}, expected {expect_size}")
            if expect_sha and sha.hexdigest() != expect_sha:
                raise IOError(f"sha256 mismatch on {dest.name}")
            tmp.replace(dest)
            return {"path": dest.name, "bytes": size, "sha256": sha.hexdigest()}
        except Exception as exc:  # noqa: BLE001 - retry then fail loudly
            last_err = exc
            time.sleep(2.0 * (attempt + 1))
    raise RuntimeError(f"download failed after retries: {dest.name}: {last_err}")


def stage_fetch(workers: int) -> None:  # noqa: ARG001 - workers unused (sequential, HF-friendly)
    RAW.mkdir(parents=True, exist_ok=True)
    manifest_path = RAW / "manifest.jsonl"
    done: dict[str, dict] = {}
    if manifest_path.exists():
        with manifest_path.open("r", encoding="utf-8") as f:
            for line in f:
                try:
                    row = json.loads(line)
                    done[row["path"]] = row
                except (json.JSONDecodeError, KeyError):
                    continue
    out = manifest_path.open("a", encoding="utf-8", newline="\n")
    ok = failed = bytes_total = 0
    try:
        for subset in SUBSETS:
            files = list_tree(subset)
            log(f"fetch: {subset.split('/')[1]} -> {len(files)} files, "
                f"{sum(f['size'] for f in files) / 1e9:.2f} GB")
            for fmeta in files:
                rel = fmeta["path"]
                local = RAW / rel
                expect_size = int(fmeta.get("size", 0))
                expect_sha = (fmeta.get("lfs") or {}).get("oid")
                if rel in done and local.exists() and local.stat().st_size == expect_size:
                    continue  # manifest row already records the verified sha
                try:
                    url = RESOLVE + urllib.parse.quote(rel)
                    row = fetch_file(url, local, expect_sha, expect_size)
                    out.write(json.dumps({"path": rel, "subset": subset.split("/")[1],
                                          "license": CARD_LICENSE, "url": url, **row},
                                         ensure_ascii=False) + "\n")
                    out.flush()
                    ok += 1
                    bytes_total += row["bytes"]
                    if ok % 10 == 0:
                        log(f"fetch: {ok} files, {bytes_total / 1e9:.2f} GB so far")
                except Exception as exc:  # noqa: BLE001 - count and continue
                    failed += 1
                    log(f"FAILED {rel}: {exc}")
    finally:
        out.close()
    log(f"fetch DONE: ok={ok} failed={failed} bytes={bytes_total:,}")
    section = (
        f"\n## Knesset Corpus fetch ({utcnow()[:10]})\n"
        f"- source: huggingface.co/datasets/{REPO} (license: {CARD_LICENSE})\n"
        f"- subsets: plenary_no_morph + committee_no_morph sentence shards (.jsonl.bz2)\n"
        f"- files fetched: {ok} (sha256-verified vs LFS oid)\n"
        f"- failures: {failed}\n"
        f"- total bytes: {bytes_total:,}\n"
        f"- manifest: raw/knesset/manifest.jsonl (per-file sha256)\n"
    )
    print(section, flush=True)
    try:
        with DOWNLOAD_LOG.open("a", encoding="utf-8") as f:
            f.write(section)
    except OSError:
        pass


def stage_inspect() -> None:
    shards = sorted(RAW.rglob("*.jsonl.bz2"))
    if not shards:
        raise SystemExit("no shards downloaded yet")
    with bz2.open(shards[0], "rt", encoding="utf-8") as f:
        rec = json.loads(f.readline())
        print("shard:", shards[0].name)
        print("keys:", sorted(rec.keys()))
        for k in sorted(rec.keys()):
            v = rec[k]
            print(f"  {k}: {repr(v)[:90]}")


def protocol_key(rec: dict, shard_stem: str) -> str:
    """Composite public key for a protocol (no protocol_id field exists)."""
    parts = [str(rec.get(k) or "").strip() for k in
             ("protocol_type", "knesset_number", "session_name", "protocol_number", "protocol_date")]
    key = "|".join(parts)
    return key if key.strip("|") else shard_stem


def stage_extract(skip_ocr: bool = True) -> None:
    out_path = EXTRACT / "knesset_protocols.jsonl"
    EXTRACT.mkdir(parents=True, exist_ok=True)
    docs = sentences = skipped_bad = skipped_ocr = 0
    chars_total = 0
    with out_path.open("w", encoding="utf-8", newline="\n") as out:
        for shard in sorted(RAW.rglob("*.jsonl.bz2")):
            subset = shard.parent.name.replace("_no_morph_sentences_shards_bzip2_files", "")
            groups: dict[str, list[str]] = {}
            names: dict[str, str] = {}
            try:
                with bz2.open(shard, "rt", encoding="utf-8") as f:
                    for line in f:
                        try:
                            rec = json.loads(line)
                        except json.JSONDecodeError:
                            skipped_bad += 1
                            continue
                        if skip_ocr and rec.get("is_ocr_output"):
                            skipped_ocr += 1
                            continue
                        text = (rec.get("sentence_text") or "").strip()
                        if not text:
                            continue
                        pid = protocol_key(rec, shard.stem)
                        groups.setdefault(pid, []).append(text)
                        if pid not in names:
                            names[pid] = str(rec.get("protocol_name") or "")
                        sentences += 1
            except (OSError, EOFError) as exc:
                log(f"unreadable shard {shard.name}: {exc}")
                skipped_bad += 1
                continue
            for pid, parts in groups.items():
                text = "\n".join(parts)
                if not text:
                    continue
                out.write(json.dumps({
                    "protocol_id": pid,
                    "protocol_name": names.get(pid, ""),
                    "subset": subset,
                    "source_url": RESOLVE + urllib.parse.quote(str(shard.relative_to(RAW)).replace("\\", "/")),
                    "text": text,
                }, ensure_ascii=False) + "\n")
                docs += 1
                chars_total += len(text)
            log(f"extract: {shard.name} -> running docs={docs:,} sentences={sentences:,}")
    log(f"extract DONE: docs={docs:,} sentences={sentences:,} chars={chars_total:,} "
        f"bad={skipped_bad:,} ocr-skipped={skipped_ocr:,}")
    section = (
        f"\n## Knesset extraction ({utcnow()})\n"
        f"- docs written: {docs:,} -> extracted/knesset/knesset_protocols.jsonl\n"
        f"- sentences: {sentences:,}\n"
        f"- text chars: {chars_total:,} ({chars_total / 1e9:.2f} GB)\n"
        f"- privacy: ONLY sentence_text kept (no speaker names/genders/factions)\n"
        f"- OCR-quality gate: {skipped_ocr:,} sentences skipped (is_ocr_output; paper: heavy OCR errors)\n"
        f"- skipped bad lines: {skipped_bad:,}\n"
    )
    print(section, flush=True)
    try:
        with EXTRACT_LOG.open("a", encoding="utf-8") as f:
            f.write(section)
    except OSError:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=["fetch", "inspect", "extract", "all"], required=True)
    parser.add_argument("--keep-ocr", action="store_true",
                        help="keep is_ocr_output sentences too (default: skip, paper reports heavy OCR errors)")
    args = parser.parse_args()
    RAW.mkdir(parents=True, exist_ok=True)
    EXTRACT.mkdir(parents=True, exist_ok=True)
    if args.stage in ("fetch", "all"):
        stage_fetch(workers=1)
    if args.stage in ("inspect", "all"):
        stage_inspect()
    if args.stage in ("extract", "all"):
        stage_extract(skip_ocr=not args.keep_ocr)


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    main()
